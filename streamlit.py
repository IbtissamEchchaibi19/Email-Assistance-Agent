import streamlit as st
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
import logging

# Import your existing modules
try:
    from email_assistant.tools.gmail.gmail_tools import fetch_group_emails, send_email, get_calendar_events, send_calendar_invite
    from email_assistant.email_assistant_hitl_memory_gmail import run_email_assistant_with_config, email_assistant
    from email_assistant.utils import parse_gmail, format_gmail_markdown
    from langgraph.store.memory import InMemoryStore
except ImportError as e:
    st.error(f"Import error: {e}")
    st.stop()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Streamlit page config
st.set_page_config(
    page_title="Email Assistant",
    page_icon="📧",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for black background with white text
st.markdown("""
<style>
    .stApp {
        background-color: #000000;
        color: #FFFFFF;
    }
    
    .stSidebar {
        background-color: #1a1a1a;
    }
    
    .stTextInput > div > div > input {
        background-color: #2d2d2d;
        color: #FFFFFF;
        border: 1px solid #404040;
    }
    
    .stTextArea > div > div > textarea {
        background-color: #2d2d2d;
        color: #FFFFFF;
        border: 1px solid #404040;
    }
    
    .stSelectbox > div > div > select {
        background-color: #2d2d2d;
        color: #FFFFFF;
        border: 1px solid #404040;
    }
    
    .stButton > button {
        background-color: #404040;
        color: #FFFFFF;
        border: 1px solid #606060;
    }
    
    .stButton > button:hover {
        background-color: #505050;
        border: 1px solid #707070;
    }
    
    .stExpander {
        background-color: #1a1a1a;
        border: 1px solid #404040;
    }
    
    .stMarkdown {
        color: #FFFFFF;
    }
    
    .element-container {
        color: #FFFFFF;
    }
    
    h1, h2, h3, h4, h5, h6 {
        color: #FFFFFF;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'store' not in st.session_state:
    st.session_state.store = InMemoryStore()

if 'fetched_emails' not in st.session_state:
    st.session_state.fetched_emails = []

if 'selected_email' not in st.session_state:
    st.session_state.selected_email = None

if 'classification_result' not in st.session_state:
    st.session_state.classification_result = None

if 'generated_response' not in st.session_state:
    st.session_state.generated_response = ""

if 'calendar_events' not in st.session_state:
    st.session_state.calendar_events = []

# Main title
st.title("📧 Email Assistant")

# Sidebar for configuration
with st.sidebar:
    st.header("Configuration")
    
    # Email configuration
    st.subheader("Email Settings")
    user_email = st.text_input("Your Email Address", value="", key="user_email")
    minutes_since = st.number_input("Fetch emails from last (minutes)", min_value=1, value=30, key="minutes_since")
    
    # Gmail API tokens
    st.subheader("Gmail API (Optional)")
    gmail_token = st.text_area("Gmail Token JSON", value="", height=100, key="gmail_token")
    gmail_secret = st.text_area("Gmail Secret JSON", value="", height=100, key="gmail_secret")
    
    if st.button("Clear All Data"):
        for key in ['fetched_emails', 'selected_email', 'classification_result', 'generated_response', 'calendar_events']:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()

# Main content area
if not user_email:
    st.warning("Please enter your email address in the sidebar to continue.")
    st.stop()

# Tab layout
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📥 Fetch Emails", "🔍 Classify", "✏️ Generate Response", "📅 Schedule Meeting", "📊 Summary"])

# Tab 1: Fetch Emails
with tab1:
    st.header("Fetch Recent Emails")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.write(f"Fetching emails for: **{user_email}**")
        st.write(f"Time range: Last **{minutes_since}** minutes")
    
    with col2:
        if st.button("🔄 Fetch Emails", type="primary"):
            with st.spinner("Fetching emails..."):
                try:
                    # Use your existing fetch_group_emails function
                    emails = list(fetch_group_emails(
                        email_address=user_email,
                        minutes_since=minutes_since,
                        gmail_token=gmail_token if gmail_token else None,
                        gmail_secret=gmail_secret if gmail_secret else None,
                        include_read=True,
                        skip_filters=False
                    ))
                    
                    st.session_state.fetched_emails = emails
                    st.success(f"✅ Fetched {len(emails)} emails")
                    
                except Exception as e:
                    st.error(f"❌ Error fetching emails: {str(e)}")
                    logger.error(f"Email fetch error: {e}")
    
    # Display fetched emails
    if st.session_state.fetched_emails:
        st.subheader(f"Found {len(st.session_state.fetched_emails)} emails")
        
        for idx, email in enumerate(st.session_state.fetched_emails):
            if email.get("user_respond", False):
                continue
                
            with st.expander(f"📧 {email.get('subject', 'No Subject')} - From: {email.get('from_email', 'Unknown')}"):
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    st.write(f"**From:** {email.get('from_email', 'Unknown')}")
                    st.write(f"**To:** {email.get('to_email', 'Unknown')}")
                    st.write(f"**Subject:** {email.get('subject', 'No Subject')}")
                    st.write(f"**Time:** {email.get('send_time', 'Unknown')}")
                    st.write(f"**Content:** {email.get('page_content', '')[:200]}...")
                
                with col2:
                    if st.button(f"Select Email {idx}", key=f"select_{idx}"):
                        st.session_state.selected_email = email
                        st.success("✅ Email selected")
                        st.rerun()

# Tab 2: Classify Email
with tab2:
    st.header("Email Classification")
    
    if not st.session_state.selected_email:
        st.warning("⚠️ Please select an email from the Fetch Emails tab first.")
    else:
        email = st.session_state.selected_email
        
        # Display selected email
        st.subheader("Selected Email")
        email_markdown = format_gmail_markdown(
            email.get('subject', 'No Subject'),
            email.get('from_email', 'Unknown'),
            email.get('to_email', 'Unknown'),
            email.get('page_content', ''),
            email.get('id', 'unknown')
        )
        st.markdown(email_markdown)
        
        col1, col2 = st.columns([3, 1])
        
        with col2:
            if st.button("🔍 Classify Email", type="primary"):
                with st.spinner("Classifying email..."):
                    try:
                        # Use your existing run_email_assistant_with_config function instead
                        result = run_email_assistant_with_config(
                            email_data=json.dumps(email),
                            store=st.session_state.store
                        )
                        
                        classification = result.get("classification_decision", "unknown")
                        st.session_state.classification_result = classification
                        
                        if classification == "respond":
                            st.success("🤖 Classification: **RESPOND** - This email requires a response")
                        elif classification == "notify":
                            st.info("🔔 Classification: **NOTIFY** - This email contains important information")
                        elif classification == "ignore":
                            st.warning("🚫 Classification: **IGNORE** - This email can be safely ignored")
                        else:
                            st.error(f"❓ Classification: **{classification.upper()}**")
                            
                    except Exception as e:
                        st.error(f"❌ Classification error: {str(e)}")
                        logger.error(f"Classification error: {e}")
        
        # Show classification result
        if st.session_state.classification_result:
            with col1:
                st.write(f"**Current Classification:** {st.session_state.classification_result.upper()}")

# Tab 3: Generate Response
with tab3:
    st.header("Generate Email Response")
    
    if not st.session_state.selected_email:
        st.warning("⚠️ Please select an email first.")
    else:
        email = st.session_state.selected_email
        
        # Option to choose generation method
        st.subheader("Choose Response Generation Method")
        response_method = st.radio(
            "Select method:",
            ["Simple Generation (Recommended)", "Full Workflow (May cause recursion issues)"],
            index=0,
            key="response_method"
        )
        
        col1, col2 = st.columns([3, 1])
        
        with col2:
            if st.button("✨ Generate Response", type="primary"):
                with st.spinner("Generating response..."):
                    try:
                        if response_method.startswith("Simple Generation"):
                            # Simple direct LLM call to avoid recursion issues
                            from langchain.chat_models import init_chat_model
                            
                            # Get Google API key from environment
                            api_key = os.getenv("GOOGLE_API_KEY")
                            if not api_key:
                                st.error("❌ GOOGLE_API_KEY not found in environment variables")
                                st.stop()
                            
                            simple_llm = init_chat_model(
                                "gemini-2.5-flash", 
                                model_provider="google_genai",
                                api_key=api_key
                            )
                            
                            # Create a simple prompt
                            email_content = email.get('page_content', '')
                            subject = email.get('subject', 'your message')
                            sender = email.get('from_email', 'Unknown')
                            
                            prompt = f"""
                            Please generate a professional email response to the following email:

                            From: {sender}
                            Subject: {subject}
                            Content: {email_content}

                            Write a helpful, professional, and appropriate response. Keep it concise and courteous.
                            """
                            
                            response = simple_llm.invoke([{"role": "user", "content": prompt}])
                            response_content = response.content if hasattr(response, 'content') else str(response)
                            
                            st.session_state.generated_response = response_content
                            st.success("✅ Response generated successfully using simple method!")
                            
                        else:
                            # Use your existing workflow (may cause recursion issues)
                            result = run_email_assistant_with_config(
                                email_data=json.dumps(email),
                                store=st.session_state.store
                            )
                            
                            # Try to extract response from result
                            messages = result.get("messages", [])
                            response_content = ""
                            
                            # Look for the generated response in messages
                            for msg in reversed(messages):
                                if isinstance(msg, dict):
                                    if msg.get('role') == 'assistant' and msg.get('content'):
                                        content = msg.get('content', '')
                                        if content and len(content) > 10:
                                            response_content = content
                                            break
                                elif hasattr(msg, 'content'):
                                    if hasattr(msg.content, 'strip') and len(msg.content.strip()) > 10:
                                        response_content = msg.content
                                        break
                            
                            if not response_content:
                                # Fallback response
                                response_content = f"Thank you for your email regarding '{email.get('subject', 'your message')}'. I will review this and get back to you soon."
                            
                            st.session_state.generated_response = response_content
                            st.success("✅ Response generated successfully using full workflow!")
                            
                    except Exception as e:
                        st.error(f"❌ Response generation error: {str(e)}")
                        logger.error(f"Response generation error: {e}")
                        
                        # Provide fallback response on error
                        fallback_response = f"Thank you for your email regarding '{email.get('subject', 'your message')}'. I will review this and get back to you soon."
                        st.session_state.generated_response = fallback_response
                        st.warning("⚠️ Using fallback response due to error")
        
        # Allow user to edit the response
        if st.session_state.generated_response:
            st.subheader("Generated Response (Editable)")
            edited_response = st.text_area(
                "Edit the response before sending:",
                value=st.session_state.generated_response,
                height=200,
                key="response_editor"
            )
            
            col1, col2, col3 = st.columns([2, 1, 1])
            
            with col1:
                st.write("**Preview:** Response will be sent to:", email.get('from_email', 'Unknown'))
            
            with col2:
                if st.button("💾 Update Response"):
                    st.session_state.generated_response = edited_response
                    st.success("✅ Response updated!")
            
            with col3:
                if st.button("📤 Send Email", type="primary"):
                    with st.spinner("Sending email..."):
                        try:
                            success = send_email(
                                email_id=email.get('id', 'unknown'),
                                response_text=edited_response,
                                email_address=user_email,
                                addn_receipients=None
                            )
                            
                            if success:
                                st.success("✅ Email sent successfully!")
                            else:
                                st.error("❌ Failed to send email")
                                
                        except Exception as e:
                            st.error(f"❌ Send email error: {str(e)}")
                            logger.error(f"Send email error: {e}")

# Tab 4: Schedule Meeting
with tab4:
    st.header("Schedule Meeting")
    
    if not st.session_state.selected_email:
        st.warning("⚠️ Please select an email first.")
    else:
        email = st.session_state.selected_email
        
        # Check calendar availability first
        st.subheader("Check Calendar Availability")
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            check_dates = st.text_input("Enter dates to check (DD-MM-YYYY, comma separated)", 
                                      value=datetime.now().strftime("%d-%m-%Y"))
        
        with col2:
            if st.button("📅 Check Calendar"):
                if check_dates:
                    with st.spinner("Checking calendar..."):
                        try:
                            dates_list = [date.strip() for date in check_dates.split(",")]
                            events = get_calendar_events(dates_list)
                            st.session_state.calendar_events = events
                            st.success("✅ Calendar checked!")
                            
                        except Exception as e:
                            st.error(f"❌ Calendar check error: {str(e)}")
                            logger.error(f"Calendar check error: {e}")
        
        # Display calendar events
        if st.session_state.calendar_events:
            st.subheader("Calendar Events")
            st.text(st.session_state.calendar_events)
        
        # Meeting scheduling form
        st.subheader("Schedule New Meeting")
        
        with st.form("meeting_form"):
            meeting_title = st.text_input("Meeting Title", value=f"Re: {email.get('subject', 'Meeting')}")
            
            col1, col2 = st.columns(2)
            
            with col1:
                meeting_date = st.date_input("Meeting Date", value=datetime.now().date())
                start_time = st.time_input("Start Time", value=datetime.now().time())
            
            with col2:
                duration = st.selectbox("Duration (hours)", [0.5, 1, 1.5, 2, 2.5, 3], index=1)
                timezone = st.text_input("Timezone", value="America/Los_Angeles")
            
            attendees_input = st.text_input("Attendees (email addresses, comma separated)", 
                                          value=email.get('from_email', ''))
            
            submitted = st.form_submit_button("📅 Schedule Meeting", type="primary")
            
            if submitted:
                with st.spinner("Scheduling meeting..."):
                    try:
                        # Calculate start and end times
                        start_datetime = datetime.combine(meeting_date, start_time)
                        end_datetime = start_datetime + timedelta(hours=duration)
                        
                        start_iso = start_datetime.isoformat()
                        end_iso = end_datetime.isoformat()
                        
                        # Parse attendees
                        attendees = [email.strip() for email in attendees_input.split(",") if email.strip()]
                        
                        # Schedule meeting using your existing function
                        success = send_calendar_invite(
                            attendees=attendees,
                            title=meeting_title,
                            start_time=start_iso,
                            end_time=end_iso,
                            organizer_email=user_email,
                            timezone=timezone
                        )
                        
                        if success:
                            st.success("✅ Meeting scheduled successfully!")
                            st.balloons()
                        else:
                            st.error("❌ Failed to schedule meeting")
                            
                    except Exception as e:
                        st.error(f"❌ Meeting scheduling error: {str(e)}")
                        logger.error(f"Meeting scheduling error: {e}")

# Tab 5: Summary
with tab5:
    st.header("Session Summary")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📊 Statistics")
        st.metric("Fetched Emails", len(st.session_state.fetched_emails))
        st.metric("Selected Email", "Yes" if st.session_state.selected_email else "No")
        st.metric("Classification", st.session_state.classification_result or "None")
        st.metric("Response Generated", "Yes" if st.session_state.generated_response else "No")
    
    with col2:
        st.subheader("🔧 Current Configuration")
        st.write(f"**User Email:** {user_email}")
        st.write(f"**Fetch Range:** {minutes_since} minutes")
        st.write(f"**Gmail Token:** {'Configured' if gmail_token else 'Not configured'}")
        st.write(f"**Gmail Secret:** {'Configured' if gmail_secret else 'Not configured'}")
    
    # Show selected email details
    if st.session_state.selected_email:
        st.subheader("📧 Selected Email Details")
        email = st.session_state.selected_email
        
        with st.expander("Email Details", expanded=True):
            st.json({
                "ID": email.get('id', 'unknown'),
                "Thread ID": email.get('thread_id', 'unknown'),
                "From": email.get('from_email', 'Unknown'),
                "To": email.get('to_email', 'Unknown'),
                "Subject": email.get('subject', 'No Subject'),
                "Send Time": email.get('send_time', 'Unknown'),
                "Content Length": len(email.get('page_content', ''))
            })
    
    # Show generated response
    if st.session_state.generated_response:
        st.subheader("✏️ Generated Response")
        with st.expander("Response Content", expanded=True):
            st.text(st.session_state.generated_response)

# Footer
st.markdown("---")
st.markdown("**Email Assistant** - Powered by LangGraph and Gmail API")