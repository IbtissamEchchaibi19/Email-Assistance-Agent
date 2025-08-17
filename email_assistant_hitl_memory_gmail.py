from typing import Literal 
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from langgraph.store.base import BaseStore
from langgraph.types import interrupt, Command
from tools import get_tools, get_tools_by_name
from tools.gmail.prompt_templates import GMAIL_TOOLS_PROMPT
from tools.gmail.gmail_tools import mark_as_read
from prompts import (
    triage_system_prompt, 
    triage_user_prompt, 
    agent_system_prompt_hitl_memory, 
    default_triage_instructions, 
    default_background, 
    default_response_preferences, 
    default_cal_preferences, 
    MEMORY_UPDATE_INSTRUCTIONS,
)
from schemas import State, RouterSchema, StateInput, UserPreferences
from utils import parse_gmail, format_for_display, format_gmail_markdown
from dotenv import load_dotenv
import os
import json
import traceback
import logging
import time
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("GOOGLE_API_KEY not found in environment variables")
llm = init_chat_model(
    "gemini-2.5-flash", 
    model_provider="google_genai",
    api_key=api_key
)
tools = get_tools(["send_email_tool", "schedule_meeting_tool", "check_calendar_tool", "Question", "Done"], include_gmail=True)
tools_by_name = get_tools_by_name(tools)
llm_router = llm.with_structured_output(RouterSchema) 
llm_with_tools = llm.bind_tools(tools, tool_choice="any")
MAX_LLM_ITERATIONS = 10 
MAX_WORKFLOW_TIME = 300 
INTERRUPT_TIMEOUT = 1800

def debug_email_parsing(email_input):
    try:
        print(f"DEBUG: Raw email input type: {type(email_input)}")
        if isinstance(email_input, str):
            try:
                parsed_json = json.loads(email_input)
                print(f"DEBUG: Successfully parsed as JSON")
                return parsed_json
            except json.JSONDecodeError:
                print("DEBUG: Not valid JSON, treating as plain text")
                return email_input
        elif isinstance(email_input, dict):
            print(f"DEBUG: Already a dictionary")
            return email_input
        else:
            print(f"DEBUG: Unexpected type, converting to string")
            return str(email_input)
    except Exception as e:
        print(f"DEBUG: Error in email parsing: {e}")
        return str(email_input)

def safe_parse_gmail(email_input):
    try:
        parsed_input = debug_email_parsing(email_input)
        
        if isinstance(parsed_input, dict):
            author = parsed_input.get('from_email', 'Unknown')
            to = parsed_input.get('to_email', 'Unknown')
            subject = parsed_input.get('subject', 'No Subject')
            email_thread = parsed_input.get('page_content', '')
            email_id = parsed_input.get('id', 'unknown_id')
            
            return author, to, subject, email_thread, email_id
        else:
            return parse_gmail(email_input)
            
    except Exception as e:
        logger.error(f"Failed to parse email input: {e}")
        return "Unknown", "Unknown", "Unknown Subject", str(email_input), "unknown_id"

def get_memory(store, namespace, default_content=None):
    """Get memory from the store with better error handling"""
    try:
        user_preferences = store.get(namespace, "user_preferences")
        if user_preferences:
            return user_preferences.value
        else:
            if default_content:
                store.put(namespace, "user_preferences", default_content)
            return default_content or ""
    except Exception as e:
        logger.error(f"Error getting memory: {e}")
        return default_content or ""

def update_memory(store, namespace, messages):
    """Update memory profile in the store with better error handling"""
    try:
        user_preferences = store.get(namespace, "user_preferences")
        current_profile = user_preferences.value if user_preferences else ""
        
        llm_memory = init_chat_model("gemini-2.5-flash", model_provider="google_genai", api_key=api_key).with_structured_output(UserPreferences)
        result = llm_memory.invoke(
            [
                {"role": "system", "content": MEMORY_UPDATE_INSTRUCTIONS.format(current_profile=current_profile, namespace=namespace)},
            ] + messages
        )
        store.put(namespace, "user_preferences", result.user_preferences)
    except Exception as e:
        logger.error(f"Error updating memory: {e}")

def triage_router(state: State, store: BaseStore) -> Command[Literal["triage_interrupt_handler", "response_agent", "__end__"]]:
    try:
        logger.info("Starting triage_router")
        author, to, subject, email_thread, email_id = safe_parse_gmail(state["email_input"])
        user_prompt = triage_user_prompt.format(
            author=author, to=to, subject=subject, email_thread=email_thread
        )
        email_markdown = format_gmail_markdown(subject, author, to, email_thread, email_id)
        triage_instructions = get_memory(store, ("email_assistant", "triage_preferences"), default_triage_instructions)
        system_prompt = triage_system_prompt.format(
            background=default_background,
            triage_instructions=triage_instructions,
        )
        result = llm_router.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        classification = result.classification
        logger.info(f"Classification result: {classification}")
        if classification == "respond":
            logger.info("📧 Classification: RESPOND - This email requires a response")
            return Command(
                goto="response_agent",
                update={
                    "classification_decision": result.classification,
                    "messages": [{"role": "user", "content": f"Respond to the email: {email_markdown}"}],
                    "workflow_start_time": time.time(), 
                }
            )
        elif classification == "ignore":
            logger.info("🚫 Classification: IGNORE - This email can be safely ignored")
            return Command(goto="mark_as_read_node", update={"classification_decision": classification})
        elif classification == "notify":
            logger.info("🔔 Classification: NOTIFY - This email contains important information") 
            return Command(goto="triage_interrupt_handler", update={"classification_decision": classification})
        else:
            logger.warning(f"Invalid classification: {classification}")
            return Command(goto="triage_interrupt_handler", update={"classification_decision": "notify"}) 
    except Exception as e:
        logger.error(f"Error in triage_router: {e}")
        return Command(goto="mark_as_read_node", update={"classification_decision": "error"})

def triage_interrupt_handler(state: State, store: BaseStore) -> Command[Literal["response_agent", "__end__"]]:
    """Handles interrupts from the triage step with timeout protection"""
    try:
        logger.info("Starting triage_interrupt_handler")  
        author, to, subject, email_thread, email_id = safe_parse_gmail(state["email_input"])
        email_markdown = format_gmail_markdown(subject, author, to, email_thread, email_id)

        messages = [{"role": "user", "content": f"Email to notify user about: {email_markdown}"}]

        request = {
            "action_request": {
                "action": f"Email Assistant: {state['classification_decision']}",
                "args": {}
            },
            "config": {
                "allow_ignore": True,  
                "allow_respond": True,
                "allow_edit": False, 
                "allow_accept": False,
                "timeout": INTERRUPT_TIMEOUT,  # Add timeout
            },
            "description": email_markdown,
        }
        logger.info("Sending interrupt request with timeout") 
        try:
            response = interrupt([request])[0]
            logger.info(f"Received interrupt response: {response}")
        except Exception as interrupt_error:
            logger.error(f"Interrupt failed: {interrupt_error}")
            # Default to ignore if interrupt fails
            return Command(goto="mark_as_read_node", update={"messages": []})
        if response["type"] == "response":
            user_input = response["args"]
            messages.append({"role": "user", "content": f"User wants to reply to the email. Use this feedback to respond: {user_input}"})
            update_memory(store, ("email_assistant", "triage_preferences"), [{
                "role": "user",
                "content": f"The user decided to respond to the email, so update the triage preferences to capture this."
            }] + messages)
            return Command(
                goto="response_agent", 
                update={
                    "messages": messages,
                    "workflow_start_time": time.time()
                }
            )
        elif response["type"] == "ignore":
            messages.append({"role": "user", "content": f"The user decided to ignore the email even though it was classified as notify. Update triage preferences to capture this."})
            update_memory(store, ("email_assistant", "triage_preferences"), messages)
            return Command(goto="mark_as_read_node", update={})
        else:
            logger.warning(f"Invalid response type: {response}")
            return Command(goto="mark_as_read_node", update={})
    except Exception as e:
        logger.error(f"Error in triage_interrupt_handler: {e}")
        return Command(goto="mark_as_read_node", update={})

def llm_call(state: State, store: BaseStore):
    """LLM decides whether to call a tool or not - with strict safety limits"""
    try:
        logger.info("Starting llm_call")
        workflow_start = state.get("workflow_start_time", time.time())
        if time.time() - workflow_start > MAX_WORKFLOW_TIME:
            logger.warning("Workflow timeout reached, forcing completion")
            return {
                "messages": [{"role": "assistant", "content": "Workflow timed out. Marking as complete."}],
                "force_complete": True
            }
        iteration_count = state.get("llm_iteration_count", 0) + 1
        logger.info(f"LLM iteration count: {iteration_count}")
        if iteration_count > MAX_LLM_ITERATIONS:
            logger.warning(f"Maximum LLM iterations ({MAX_LLM_ITERATIONS}) reached, forcing completion")
            return {
                "messages": [{"role": "assistant", "content": "Maximum iterations reached. Task completed."}],
                "llm_iteration_count": iteration_count,
                "force_complete": True
            }
        messages = state.get("messages", [])
        if messages:
            recent_messages = messages[-3:] 
            for msg in recent_messages:
                if hasattr(msg, 'content') and isinstance(msg.content, str):
                    content_lower = msg.content.lower()
                    if any(phrase in content_lower for phrase in [
                        "task completed", "email sent", "meeting scheduled", 
                        "successfully sent", "successfully scheduled"
                    ]):
                        logger.info("Found completion indicator in recent messages")
                        return {
                            "messages": [{"role": "assistant", "content": "Task appears to be completed based on previous messages."}],
                            "llm_iteration_count": iteration_count,
                            "force_complete": True
                        }
        cal_preferences = get_memory(store, ("email_assistant", "cal_preferences"), default_cal_preferences)
        response_preferences = get_memory(store, ("email_assistant", "response_preferences"), default_response_preferences)
        enhanced_system_prompt = agent_system_prompt_hitl_memory.format(
            tools_prompt=GMAIL_TOOLS_PROMPT,
            background=default_background,
            response_preferences=response_preferences, 
            cal_preferences=cal_preferences
        ) + f"""

CRITICAL INSTRUCTIONS FOR COMPLETION:
- You are on iteration {iteration_count} of maximum {MAX_LLM_ITERATIONS}
- After ANY main action (sending email, scheduling meeting, answering question), you MUST call the "Done" tool immediately
- Do NOT make multiple tool calls in sequence without calling "Done" 
- If you have already completed an action in this conversation, call "Done" immediately
- If you are unsure what to do, call "Done" to end the conversation safely

EXAMPLES OF WHEN TO CALL DONE:
- After send_email_tool is executed
- After schedule_meeting_tool is executed  
- After answering a Question
- If no action is needed
- If you are on iteration {MAX_LLM_ITERATIONS-1} or higher"""
        messages_to_send = [
            {"role": "system", "content": enhanced_system_prompt}
        ] + messages
        logger.info(f"Sending {len(messages_to_send)} messages to LLM")
        try:
            result = llm_with_tools.invoke(messages_to_send)
            logger.info(f"LLM response received")
            if hasattr(result, 'tool_calls') and result.tool_calls:
                for tool_call in result.tool_calls:
                    logger.info(f"LLM wants to call: {tool_call['name']}")
            else:
                logger.info("No tool calls in LLM response")
        except Exception as llm_error:
            logger.error(f"LLM call failed: {llm_error}")
            return {
                "messages": [{"role": "assistant", "content": f"LLM call failed: {str(llm_error)}"}],
                "llm_iteration_count": iteration_count,
                "force_complete": True
            }
        
        return {
            "messages": [result],
            "llm_iteration_count": iteration_count
        }
        
    except Exception as e:
        logger.error(f"Error in llm_call: {e}")
        return {
            "messages": [{"role": "assistant", "content": f"Error in llm_call: {str(e)}"}],
            "force_complete": True
        }
    
def interrupt_handler(state: State, store: BaseStore) -> Command[Literal["llm_call", "mark_as_read_node"]]:
    """Creates an interrupt for human review of tool calls with timeout protection"""
    try:
        logger.info("Starting interrupt_handler")
        
        if state.get("force_complete", False):
            logger.info("Force completion detected, ending workflow")
            return Command(goto="mark_as_read_node", update={})
        workflow_start = state.get("workflow_start_time", time.time())
        if time.time() - workflow_start > MAX_WORKFLOW_TIME:
            logger.warning("Workflow timeout in interrupt_handler, ending")
            return Command(goto="mark_as_read_node", update={})
        
        messages = state.get("messages", [])
        if not messages:
            logger.info("No messages found, ending workflow")
            return Command(goto="mark_as_read_node", update={})
            
        last_message = messages[-1]
        if not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
            logger.info("No tool calls found, ending workflow")
            return Command(goto="mark_as_read_node", update={})

        logger.info(f"Processing {len(last_message.tool_calls)} tool calls")

        result = []
        goto = "llm_call"

        for tool_call in last_message.tool_calls:
            logger.info(f"Processing tool call: {tool_call['name']}")
            if tool_call["name"] == "Done":
                logger.info("Done tool called, ending workflow")
                result.append({"role": "tool", "content": "Task completed successfully.", "tool_call_id": tool_call["id"]})
                return Command(goto="mark_as_read_node", update={"messages": result})
            hitl_tools = ["send_email_tool", "schedule_meeting_tool", "Question"]
            if tool_call["name"] not in hitl_tools:
                logger.info(f"Executing tool {tool_call['name']} directly")
                try:
                    tool = tools_by_name[tool_call["name"]]
                    observation = tool.invoke(tool_call["args"])
                    result.append({"role": "tool", "content": observation, "tool_call_id": tool_call["id"]})
                except Exception as tool_error:
                    logger.error(f"Tool execution failed: {tool_error}")
                    result.append({"role": "tool", "content": f"Tool execution failed: {str(tool_error)}", "tool_call_id": tool_call["id"]})
                continue
            try:
                email_input = state["email_input"]
                author, to, subject, email_thread, email_id = safe_parse_gmail(email_input)
                original_email_markdown = format_gmail_markdown(subject, author, to, email_thread, email_id)
                tool_display = format_for_display(tool_call)
                description = original_email_markdown + tool_display
                if tool_call["name"] == "send_email_tool":
                    config = {
                        "allow_ignore": True,
                        "allow_respond": True,
                        "allow_edit": True,
                        "allow_accept": True,
                        "timeout": INTERRUPT_TIMEOUT,
                    }
                elif tool_call["name"] == "schedule_meeting_tool":
                    config = {
                        "allow_ignore": True,
                        "allow_respond": True,
                        "allow_edit": True,
                        "allow_accept": True,
                        "timeout": INTERRUPT_TIMEOUT,
                    }
                elif tool_call["name"] == "Question":
                    config = {
                        "allow_ignore": True,
                        "allow_respond": True,
                        "allow_edit": False,
                        "allow_accept": False,
                        "timeout": INTERRUPT_TIMEOUT,
                    }
                request = {
                    "action_request": {
                        "action": tool_call["name"],
                        "args": tool_call["args"]
                    },
                    "config": config,
                    "description": description,
                }
                logger.info(f"Sending interrupt request for {tool_call['name']} with timeout")
                try:
                    response = interrupt([request])[0]
                    logger.info(f"Received interrupt response: {response['type']}")
                except Exception as interrupt_error:
                    logger.error(f"Interrupt failed for {tool_call['name']}: {interrupt_error}")
                    result.append({"role": "tool", "content": f"Interrupt timeout for {tool_call['name']}. Skipping.", "tool_call_id": tool_call["id"]})
                    goto = "mark_as_read_node"
                    break
                if response["type"] == "accept":
                    tool = tools_by_name[tool_call["name"]]
                    observation = tool.invoke(tool_call["args"])
                    result.append({"role": "tool", "content": observation, "tool_call_id": tool_call["id"]})
                    if tool_call["name"] in ["send_email_tool", "schedule_meeting_tool"]:
                        logger.info(f"Completed main action {tool_call['name']}, forcing completion")
                        result.append({"role": "system", "content": "Main task completed. Call the 'Done' tool immediately."})  
                elif response["type"] == "ignore":
                    result.append({"role": "tool", "content": f"User ignored this {tool_call['name']}. Ending workflow.", "tool_call_id": tool_call["id"]})
                    goto = "mark_as_read_node"
                    break
                elif response["type"] == "edit":
                    logger.info("Handling edit response")    
                elif response["type"] == "response":
                    logger.info("Handling user response")
            except Exception as tool_error:
                logger.error(f"Error handling tool {tool_call['name']}: {tool_error}")
                result.append({"role": "tool", "content": f"Error: {str(tool_error)}", "tool_call_id": tool_call["id"]})
        update = {"messages": result}
        logger.info(f"Interrupt handler returning with goto: {goto}")
        return Command(goto=goto, update=update)
    except Exception as e:
        logger.error(f"Error in interrupt_handler: {e}")
        return Command(goto="mark_as_read_node", update={})
def should_continue(state: State) -> Literal["interrupt_handler", "mark_as_read_node"]:
    """Route to tool handler, or end if conditions met"""
    try:
        if state.get("force_complete", False):
            logger.info("Force completion detected in should_continue")
            return "mark_as_read_node"
        workflow_start = state.get("workflow_start_time", time.time())
        if time.time() - workflow_start > MAX_WORKFLOW_TIME:
            logger.warning("Workflow timeout in should_continue")
            return "mark_as_read_node"
        iteration_count = state.get("llm_iteration_count", 0)
        if iteration_count > MAX_LLM_ITERATIONS:
            logger.warning(f"Max iterations reached in should_continue")
            return "mark_as_read_node"
        messages = state.get("messages", [])
        if not messages:
            return "mark_as_read_node"
        last_message = messages[-1]
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            for tool_call in last_message.tool_calls: 
                if tool_call["name"] == "Done":
                    logger.info("Done tool found in should_continue")
                    return "mark_as_read_node"
            logger.info("Tool calls found, going to interrupt_handler")
            return "interrupt_handler"
        logger.info("No tool calls found, ending workflow")
        return "mark_as_read_node"
    except Exception as e:
        logger.error(f"Error in should_continue: {e}")
        return "mark_as_read_node"
    
def mark_as_read_node(state: State):
    """Mark email as read"""
    try:
        email_input = state["email_input"]
        author, to, subject, email_thread, email_id = safe_parse_gmail(email_input)
        mark_as_read(email_id)
        logger.info(f"✅ Marked email as read: {email_id}")
    except Exception as e:
        logger.error(f"Error marking email as read: {e}")

agent_builder = StateGraph(State)
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("interrupt_handler", interrupt_handler)
agent_builder.add_node("mark_as_read_node", mark_as_read_node)
agent_builder.add_edge(START, "llm_call")
agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    {
        "interrupt_handler": "interrupt_handler",
        "mark_as_read_node": "mark_as_read_node",
    },
)
agent_builder.add_conditional_edges(
    "interrupt_handler",
    lambda state: "llm_call" if not state.get("force_complete") and state.get("messages") else "mark_as_read_node",
    {
        "llm_call": "llm_call",
        "mark_as_read_node": "mark_as_read_node",
    }
)
agent_builder.add_edge("mark_as_read_node", END)
response_agent = agent_builder.compile()
overall_workflow = (
    StateGraph(State, input_schema=StateInput)
    .add_node("triage_router", triage_router)
    .add_node("triage_interrupt_handler", triage_interrupt_handler)
    .add_node("response_agent", response_agent)
    .add_node("mark_as_read_node", mark_as_read_node)
    .add_edge(START, "triage_router")
    .add_conditional_edges(
        "triage_router",
        lambda state: state.get("classification_decision", "ignore"),
        {
            "respond": "response_agent",
            "notify": "triage_interrupt_handler",
            "ignore": "mark_as_read_node",
            "error": "mark_as_read_node"
        }
    )
    .add_conditional_edges(
        "triage_interrupt_handler",
        lambda state: "response_agent" if state.get("messages") else "mark_as_read_node",
        {
            "response_agent": "response_agent",
            "mark_as_read_node": "mark_as_read_node",
        }
    )
    .add_edge("response_agent", END)
    .add_edge("mark_as_read_node", END)
)
email_assistant = overall_workflow.compile()
def run_email_assistant_with_config(email_data, store):
    """Run the email assistant with safety configuration"""
    config = {
        "recursion_limit": MAX_LLM_ITERATIONS + 5,  
        "store": store,
        "max_execution_time": MAX_WORKFLOW_TIME, 
    }
    
    try:
        logger.info("Starting email assistant workflow")
        result = email_assistant.invoke(
            {"email_input": email_data},
            config=config
        )
        logger.info("Email assistant workflow completed successfully")
        return result
    except Exception as e:
        logger.error(f"Email assistant workflow failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {"error": str(e), "status": "failed"}
    
