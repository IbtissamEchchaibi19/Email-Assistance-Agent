#!/usr/bin/env python3
"""
Create LangSmith datasets from your email evaluation data
This script transforms your email data into LangSmith-compatible datasets
"""

from langsmith import Client
import json
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import your email data (assuming it's in a file called email_dataset.py)
# You'll need to adjust this import based on where you save your email data
from email_assistant.eval.email_dataset  import (
    email_inputs, email_names, response_criteria_list, 
    triage_outputs_list, expected_tool_calls
)

def create_langsmith_client():
    """Initialize LangSmith client"""
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        raise ValueError("LANGCHAIN_API_KEY not found in environment variables")
    
    return Client(api_key=api_key)

def create_triage_dataset(client, dataset_name="email-triage-evaluation"):
    """Create a dataset for triage evaluation"""
    
    print(f"🏗️  Creating triage dataset: {dataset_name}")
    
    # Create dataset
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="Email triage classification evaluation dataset - classify emails as respond/ignore/notify",
        metadata={
            "created_at": datetime.now().isoformat(),
            "purpose": "triage_evaluation",
            "total_examples": len(email_inputs)
        }
    )
    
    # Add examples to dataset
    examples = []
    for i, (email_input, expected_output) in enumerate(zip(email_inputs, triage_outputs_list)):
        
        example = {
            "inputs": {
                "email_input": email_input
            },
            "outputs": {
                "classification": expected_output
            },
            "metadata": {
                "email_id": email_names[i],
                "subject": email_input.get("subject", ""),
                "author": email_input.get("author", ""),
                "expected_classification": expected_output
            }
        }
        examples.append(example)
    
    # Upload examples in batches
    client.create_examples(
        inputs=[ex["inputs"] for ex in examples],
        outputs=[ex["outputs"] for ex in examples],
        metadata=[ex["metadata"] for ex in examples],
        dataset_id=dataset.id
    )
    
    print(f"✅ Created triage dataset with {len(examples)} examples")
    return dataset

def create_response_dataset(client, dataset_name="email-response-evaluation"):
    """Create a dataset for response generation evaluation"""
    
    print(f"🏗️  Creating response dataset: {dataset_name}")
    
    # Filter only emails that need responses (not ignore/notify)
    response_examples = []
    for i, (email_input, triage_output, response_criteria, expected_tools) in enumerate(
        zip(email_inputs, triage_outputs_list, response_criteria_list, expected_tool_calls)
    ):
        if triage_output == "respond":  # Only include emails that need responses
            example = {
                "inputs": {
                    "email_input": email_input,
                    "classification_decision": "respond"
                },
                "outputs": {
                    "response_criteria": response_criteria,
                    "expected_tool_calls": expected_tools
                },
                "metadata": {
                    "email_id": email_names[i],
                    "subject": email_input.get("subject", ""),
                    "author": email_input.get("author", ""),
                    "triage_classification": triage_output,
                    "num_expected_tools": len(expected_tools)
                }
            }
            response_examples.append(example)
    
    # Create dataset
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="Email response generation evaluation dataset - generate appropriate responses and tool calls",
        metadata={
            "created_at": datetime.now().isoformat(),
            "purpose": "response_evaluation", 
            "total_examples": len(response_examples),
            "only_respond_emails": True
        }
    )
    
    # Upload examples
    client.create_examples(
        inputs=[ex["inputs"] for ex in response_examples],
        outputs=[ex["outputs"] for ex in response_examples],
        metadata=[ex["metadata"] for ex in response_examples],
        dataset_id=dataset.id
    )
    
    print(f"✅ Created response dataset with {len(response_examples)} examples")
    return dataset

def create_full_workflow_dataset(client, dataset_name="email-assistant-full-workflow"):
    """Create a comprehensive dataset for end-to-end workflow evaluation"""
    
    print(f"🏗️  Creating full workflow dataset: {dataset_name}")
    
    examples = []
    for i, (email_input, triage_output, response_criteria, expected_tools) in enumerate(
        zip(email_inputs, triage_outputs_list, response_criteria_list, expected_tool_calls)
    ):
        
        # Determine what the workflow should do
        if triage_output == "ignore":
            expected_workflow = "mark_as_read"
            expected_actions = []
        elif triage_output == "notify":
            expected_workflow = "notify_user"
            expected_actions = ["interrupt"]
        else:  # respond
            expected_workflow = "generate_response"
            expected_actions = expected_tools
        
        example = {
            "inputs": {
                "email_input": email_input
            },
            "outputs": {
                "triage_classification": triage_output,
                "expected_workflow": expected_workflow,
                "expected_actions": expected_actions,
                "response_criteria": response_criteria if triage_output == "respond" else None
            },
            "metadata": {
                "email_id": email_names[i],
                "subject": email_input.get("subject", ""),
                "author": email_input.get("author", ""),
                "complexity": "high" if len(expected_tools) > 2 else "medium" if len(expected_tools) > 0 else "low"
            }
        }
        examples.append(example)
    
    # Create dataset
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="Complete email assistant workflow evaluation - from triage to final action",
        metadata={
            "created_at": datetime.now().isoformat(),
            "purpose": "full_workflow_evaluation",
            "total_examples": len(examples),
            "includes_all_classifications": True
        }
    )
    
    # Upload examples
    client.create_examples(
        inputs=[ex["inputs"] for ex in examples],
        outputs=[ex["outputs"] for ex in examples], 
        metadata=[ex["metadata"] for ex in examples],
        dataset_id=dataset.id
    )
    
    print(f"✅ Created full workflow dataset with {len(examples)} examples")
    return dataset

def create_hitl_testing_dataset(client, dataset_name="email-assistant-hitl-testing"):
    """Create a dataset specifically for HITL testing scenarios"""
    
    print(f"🏗️  Creating HITL testing dataset: {dataset_name}")
    
    # Filter emails that would trigger HITL interactions
    hitl_examples = []
    for i, (email_input, triage_output, response_criteria, expected_tools) in enumerate(
        zip(email_inputs, triage_outputs_list, response_criteria_list, expected_tool_calls)
    ):
        # Include emails that either need response or notification (both trigger HITL)
        if triage_output in ["respond", "notify"]:
            
            # Define expected interrupt scenarios
            if triage_output == "notify":
                interrupt_scenarios = ["user_ignore", "user_respond"]
            else:  # respond
                if "schedule_meeting" in expected_tools:
                    interrupt_scenarios = ["accept_meeting", "edit_meeting_time", "ignore_meeting"]
                elif "write_email" in expected_tools:
                    interrupt_scenarios = ["accept_email", "edit_email_tone", "ignore_email"]
                else:
                    interrupt_scenarios = ["accept_action", "ignore_action"]
            
            example = {
                "inputs": {
                    "email_input": email_input,
                    "classification_decision": triage_output
                },
                "outputs": {
                    "expected_interrupts": len([t for t in expected_tools if t in ["write_email", "schedule_meeting"]]),
                    "interrupt_scenarios": interrupt_scenarios,
                    "expected_tools": expected_tools
                },
                "metadata": {
                    "email_id": email_names[i],
                    "subject": email_input.get("subject", ""),
                    "author": email_input.get("author", ""),
                    "triage_classification": triage_output,
                    "hitl_complexity": "high" if len(expected_tools) > 2 else "medium",
                    "test_scenarios": interrupt_scenarios
                }
            }
            hitl_examples.append(example)
    
    # Create dataset
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="HITL testing scenarios for email assistant - test human interrupt handling",
        metadata={
            "created_at": datetime.now().isoformat(),
            "purpose": "hitl_testing",
            "total_examples": len(hitl_examples),
            "includes_interrupts": True
        }
    )
    
    # Upload examples
    client.create_examples(
        inputs=[ex["inputs"] for ex in hitl_examples],
        outputs=[ex["outputs"] for ex in hitl_examples],
        metadata=[ex["metadata"] for ex in hitl_examples],
        dataset_id=dataset.id
    )
    
    print(f"✅ Created HITL testing dataset with {len(hitl_examples)} examples")
    return dataset

def print_dataset_summary():
    """Print summary of the email dataset"""
    
    print("\n📊 EMAIL DATASET SUMMARY")
    print("=" * 50)
    print(f"Total emails: {len(email_inputs)}")
    
    # Count by triage classification
    triage_counts = {}
    for output in triage_outputs_list:
        triage_counts[output] = triage_counts.get(output, 0) + 1
    
    print("\nTriage Classifications:")
    for classification, count in triage_counts.items():
        print(f"  • {classification}: {count} emails")
    
    # Count emails with tool calls
    emails_with_tools = sum(1 for tools in expected_tool_calls if len(tools) > 0)
    print(f"\nEmails requiring actions: {emails_with_tools}")
    print(f"Emails for notification only: {triage_counts.get('notify', 0)}")
    print(f"Emails to ignore: {triage_counts.get('ignore', 0)}")

def main():
    """Main function to create all datasets"""
    
    print("🚀 CREATING LANGSMITH DATASETS FOR EMAIL ASSISTANT")
    print("=" * 60)
    
    # Print dataset summary
    print_dataset_summary()
    
    try:
        # Initialize client
        client = create_langsmith_client()
        print(f"\n✅ Connected to LangSmith")
        
        # Ask user which datasets to create
        print(f"\nWhich datasets would you like to create?")
        print("1. Triage evaluation dataset")
        print("2. Response generation dataset") 
        print("3. Full workflow dataset")
        print("4. HITL testing dataset")
        print("5. All datasets")
        
        choice = input("\nEnter choice (1-5): ").strip()
        
        if choice == "1":
            create_triage_dataset(client)
        elif choice == "2":
            create_response_dataset(client)
        elif choice == "3":
            create_full_workflow_dataset(client)
        elif choice == "4":
            create_hitl_testing_dataset(client)
        elif choice == "5":
            print("\n🏗️  Creating all datasets...")
            create_triage_dataset(client)
            create_response_dataset(client) 
            create_full_workflow_dataset(client)
            create_hitl_testing_dataset(client)
        else:
            print("❌ Invalid choice")
            return
        
        print(f"\n🎉 Dataset creation completed!")
        print(f"📊 View your datasets at: https://smith.langchain.com")
        
    except Exception as e:
        print(f"❌ Error creating datasets: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

# USAGE INSTRUCTIONS:
"""
1. Save your email data in a file called email_dataset.py (copy the content from the document)

2. Make sure you have LangSmith configured:
   export LANGCHAIN_API_KEY=your_langsmith_api_key
   pip install langsmith

3. Run this script:
   python create_langsmith_datasets.py

4. The script will create different types of datasets:
   - Triage dataset: For testing email classification
   - Response dataset: For testing response generation
   - Full workflow dataset: For end-to-end testing
   - HITL dataset: For testing human-in-the-loop scenarios

5. Use these datasets in LangSmith to:
   - Evaluate your triage router
   - Test response generation quality
   - Benchmark full workflow performance
   - Test HITL interrupt handling
"""