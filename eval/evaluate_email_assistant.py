

from langsmith import Client
from langsmith.evaluation import evaluate
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import your email assistant (adjust import as needed)
from email_assistant_hitl_memory_gmail import email_assistant, run_email_assistant_with_config
from langgraph.store.memory import InMemoryStore


def create_langsmith_client():
    """Initialize LangSmith client"""
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        raise ValueError("LANGCHAIN_API_KEY not found in environment variables")
    
    return Client(api_key=api_key)

# Evaluation functions for different components

def evaluate_triage_only(inputs):
    """Evaluate just the triage classification"""
    try:
        store = InMemoryStore()
        
        # Run only the triage part of your workflow
        # You might need to extract just the triage router from your main script
        result = email_assistant.invoke(
            {"email_input": inputs["email_input"]},
            config={"store": store}
        )
        
        return {
            "classification": result.get("classification_decision", "unknown")
        }
    except Exception as e:
        return {"classification": "error", "error": str(e)}

def evaluate_full_workflow(inputs):
    """Evaluate the complete email assistant workflow"""
    try:
        store = InMemoryStore()
        
        result = run_email_assistant_with_config(
            inputs["email_input"], 
            store
        )
        
        return {
            "workflow_result": result,
            "classification": result.get("classification_decision", "unknown"),
            "actions_taken": result.get("actions_taken", []),
            "final_state": result.get("final_state", "unknown")
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}

def evaluate_response_generation(inputs):
    """Evaluate response generation for emails that need responses"""
    try:
        store = InMemoryStore()
        
        # Force the workflow to respond (skip triage)
        modified_input = {
            **inputs,
            "classification_decision": "respond"
        }
        
        result = run_email_assistant_with_config(
            modified_input["email_input"],
            store
        )
        
        return {
            "response_generated": result,
            "tools_called": result.get("tools_called", []),
            "messages": result.get("messages", [])
        }
    except Exception as e:
        return {"error": str(e)}

# Custom evaluators

def triage_accuracy_evaluator(run, example):
    """Evaluate triage classification accuracy"""
    prediction = run.outputs.get("classification", "unknown")
    expected = example.outputs.get("classification", "unknown")
    
    return {
        "key": "triage_accuracy",
        "score": 1.0 if prediction == expected else 0.0,
        "comment": f"Predicted: {prediction}, Expected: {expected}"
    }

def response_completeness_evaluator(run, example):
    """Evaluate if response includes all expected actions"""
    tools_called = run.outputs.get("tools_called", [])
    expected_tools = example.outputs.get("expected_tool_calls", [])
    
    # Check if all expected tools were called
    missing_tools = set(expected_tools) - set(tools_called)
    extra_tools = set(tools_called) - set(expected_tools)
    
    # Calculate score based on precision and recall
    if not expected_tools:
        score = 1.0 if not tools_called else 0.5  # Penalty for unnecessary actions
    else:
        recall = len(set(tools_called) & set(expected_tools)) / len(expected_tools)
        precision = len(set(tools_called) & set(expected_tools)) / len(tools_called) if tools_called else 0
        score = (recall + precision) / 2
    
    return {
        "key": "response_completeness",
        "score": score,
        "comment": f"Missing: {list(missing_tools)}, Extra: {list(extra_tools)}"
    }

def workflow_success_evaluator(run, example):
    """Evaluate overall workflow success"""
    if run.outputs.get("error"):
        return {
            "key": "workflow_success", 
            "score": 0.0,
            "comment": f"Workflow failed: {run.outputs['error']}"
        }
    
    # Check if workflow completed without errors
    final_state = run.outputs.get("final_state", "unknown")
    
    if final_state in ["completed", "success"]:
        score = 1.0
    elif final_state in ["timeout", "max_iterations"]:
        score = 0.5
    else:
        score = 0.0
    
    return {
        "key": "workflow_success",
        "score": score,
        "comment": f"Final state: {final_state}"
    }

def run_triage_evaluation(client, dataset_name="email-triage-evaluation"):
    """Run evaluation on triage dataset"""
    
    print(f"🔍 Running triage evaluation on dataset: {dataset_name}")
    
    experiment_results = evaluate(
        evaluate_triage_only,
        data=dataset_name,
        evaluators=[triage_accuracy_evaluator],
        experiment_prefix="triage-eval",
        description="Evaluate email triage classification accuracy",
        metadata={"component": "triage_router", "evaluation_type": "classification"}
    )
    
    print(f"✅ Triage evaluation completed")
    return experiment_results

def run_response_evaluation(client, dataset_name="email-response-evaluation"):
    """Run evaluation on response generation dataset"""
    
    print(f"🔍 Running response evaluation on dataset: {dataset_name}")
    
    experiment_results = evaluate(
        evaluate_response_generation,
        data=dataset_name,
        evaluators=[response_completeness_evaluator],
        experiment_prefix="response-eval",
        description="Evaluate response generation and tool calling accuracy",
        metadata={"component": "response_agent", "evaluation_type": "generation"}
    )
    
    print(f"✅ Response evaluation completed")
    return experiment_results

def run_full_workflow_evaluation(client, dataset_name="email-assistant-full-workflow"):
    """Run evaluation on full workflow dataset"""
    
    print(f"🔍 Running full workflow evaluation on dataset: {dataset_name}")
    
    experiment_results = evaluate(
        evaluate_full_workflow,
        data=dataset_name,
        evaluators=[
            triage_accuracy_evaluator,
            workflow_success_evaluator,
            response_completeness_evaluator
        ],
        experiment_prefix="full-workflow-eval",
        description="Evaluate complete email assistant workflow end-to-end",
        metadata={"component": "full_workflow", "evaluation_type": "end_to_end"}
    )
    
    print(f"✅ Full workflow evaluation completed")
    return experiment_results

def run_hitl_evaluation(client, dataset_name="email-assistant-hitl-testing"):
    """Run evaluation specifically for HITL scenarios"""
    
    print(f"🔍 Running HITL evaluation on dataset: {dataset_name}")
    print("⚠️  Note: HITL evaluation requires manual interaction - this will test interrupt creation")
    
    def hitl_interrupt_evaluator(run, example):
        """Check if proper interrupts were created"""
        # This would need to be customized based on your interrupt handling
        interrupts_created = run.outputs.get("interrupts_created", 0)
        expected_interrupts = example.outputs.get("expected_interrupts", 0)
        
        return {
            "key": "hitl_interrupts",
            "score": 1.0 if interrupts_created == expected_interrupts else 0.0,
            "comment": f"Created: {interrupts_created}, Expected: {expected_interrupts}"
        }
    
    experiment_results = evaluate(
        evaluate_full_workflow,
        data=dataset_name,
        evaluators=[hitl_interrupt_evaluator, workflow_success_evaluator],
        experiment_prefix="hitl-eval",
        description="Evaluate HITL interrupt handling and user interaction flows",
        metadata={"component": "hitl_system", "evaluation_type": "interaction"}
    )
    
    print(f"✅ HITL evaluation completed")
    return experiment_results

def main():
    """Main evaluation runner"""
    
    print("📊 LANGSMITH EMAIL ASSISTANT EVALUATION")
    print("=" * 50)
    
    try:
        client = create_langsmith_client()
        print("✅ Connected to LangSmith")
        
        print("\nWhich evaluation would you like to run?")
        print("1. Triage accuracy evaluation")
        print("2. Response generation evaluation") 
        print("3. Full workflow evaluation")
        print("4. HITL system evaluation")
        print("5. All evaluations")
        
        choice = input("\nEnter choice (1-5): ").strip()
        
        if choice == "1":
            run_triage_evaluation(client)
        elif choice == "2":
            run_response_evaluation(client)
        elif choice == "3":
            run_full_workflow_evaluation(client)
        elif choice == "4":
            run_hitl_evaluation(client)
        elif choice == "5":
            print("\n🔍 Running all evaluations...")
            run_triage_evaluation(client)
            run_response_evaluation(client)
            run_full_workflow_evaluation(client)
            run_hitl_evaluation(client)
        else:
            print("❌ Invalid choice")
            return
        
        print(f"\n🎉 Evaluation completed!")
        print(f"📊 View results at: https://smith.langchain.com")
        
    except Exception as e:
        print(f"❌ Error running evaluation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

# USAGE INSTRUCTIONS:
"""
1. First create datasets using the dataset creator script
2. Make sure your email assistant script is importable
3. Configure LangSmith:
   export LANGCHAIN_API_KEY=your_langsmith_api_key
4. Run evaluations:
   python evaluate_email_assistant.py
5. View results in LangSmith dashboard

EVALUATION TYPES:
- Triage: Tests classification accuracy (respond/ignore/notify)
- Response: Tests tool calling and response generation
- Full Workflow: Tests end-to-end performance
- HITL: Tests human interaction and interrupt handling

METRICS TRACKED:
- Triage accuracy
- Response completeness
- Workflow success rate
- HITL interrupt handling
- Tool calling precision/recall
"""