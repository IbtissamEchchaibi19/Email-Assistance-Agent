import base64
import json
import uuid
import hashlib
import asyncio
import argparse
import os
from pathlib import Path
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from langgraph_sdk import get_client
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.absolute()
_SECRETS_DIR = _ROOT / ".secrets"
TOKEN_PATH = _SECRETS_DIR / "token.json"

def extract_message_part(payload):
    if payload.get("parts"):
        for part in payload["parts"]:
            mime_type = part.get("mimeType", "")
            if mime_type == "text/plain" and part.get("body", {}).get("data"):
                data = part["body"]["data"]
                return base64.urlsafe_b64decode(data).decode("utf-8")
                
        for part in payload["parts"]:
            mime_type = part.get("mimeType", "")
            if mime_type == "text/html" and part.get("body", {}).get("data"):
                data = part["body"]["data"]
                return base64.urlsafe_b64decode(data).decode("utf-8")
                
        for part in payload["parts"]:
            content = extract_message_part(part)
            if content:
                return content
    
    if payload.get("body", {}).get("data"):
        data = payload["body"]["data"]
        return base64.urlsafe_b64decode(data).decode("utf-8")

    return ""

def load_gmail_credentials():
    token_data = None
    
    env_token = os.getenv("GMAIL_TOKEN")
    if env_token:
        try:
            token_data = json.loads(env_token)
            print("Using GMAIL_TOKEN environment variable")
        except Exception as e:
            print(f"Could not parse GMAIL_TOKEN environment variable: {str(e)}")
    
    if token_data is None:
        if TOKEN_PATH.exists():
            try:
                with open(TOKEN_PATH, "r") as f:
                    token_data = json.load(f)
                print(f"Using token from {TOKEN_PATH}")
            except Exception as e:
                print(f"Could not load token from {TOKEN_PATH}: {str(e)}")
        else:
            print(f"Token file not found at {TOKEN_PATH}")
    
    if token_data is None:
        print("Could not find valid token data in any location")
        return None
    
    try:
        credentials = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes", ["https://www.googleapis.com/auth/gmail.modify"])
        )
        return credentials
    except Exception as e:
        print(f"Error creating credentials object: {str(e)}")
        return None

def extract_email_data(message):
    headers = message['payload']['headers']
    
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
    from_email = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
    to_email = next((h['value'] for h in headers if h['name'] == 'To'), 'Unknown Recipient')
    date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown Date')
    
    content = extract_message_part(message['payload'])
    
    email_data = {
        "from_email": from_email,
        "to_email": to_email,
        "subject": subject,
        "page_content": content,
        "id": message['id'],
        "thread_id": message['threadId'],
        "send_time": date
    }
    
    return email_data

async def ingest_email_to_langgraph(email_data, graph_name, url="http://127.0.0.1:2024"):
    try:
        client = get_client(url=url)
        
        raw_thread_id = email_data["thread_id"]
        thread_id = str(
            uuid.UUID(hex=hashlib.md5(raw_thread_id.encode("UTF-8")).hexdigest())
        )
        print(f"Gmail thread ID: {raw_thread_id} → LangGraph thread ID: {thread_id}")
        
        thread_exists = False
        try:
            thread_info = await client.threads.get(thread_id)
            thread_exists = True
            print(f"Found existing thread: {thread_id}")
        except Exception as e:
            print(f"Creating new thread: {thread_id}")
            thread_info = await client.threads.create(thread_id=thread_id)
        
        if thread_exists:
            try:
                runs = await client.runs.list(thread_id)
                
                for run_info in runs:
                    run_id = run_info.id
                    print(f"Deleting previous run {run_id} from thread {thread_id}")
                    try:
                        await client.runs.delete(thread_id, run_id)
                    except Exception as e:
                        print(f"Failed to delete run {run_id}: {str(e)}")
            except Exception as e:
                print(f"Error listing/deleting runs: {str(e)}")
        
        await client.threads.update(thread_id, metadata={"email_id": email_data["id"]})
        
        print(f"Creating run for thread {thread_id} with graph {graph_name}")
        
        email_input_str = json.dumps(email_data)
        
        run = await client.runs.create(
            thread_id,
            graph_name,
            input={"email_input": email_input_str},
            multitask_strategy="rollback",
        )
        
        print(f"✅ Run created successfully with thread ID: {thread_id}")
        
        return thread_id, run
        
    except Exception as e:
        print(f"❌ Error in ingest_email_to_langgraph: {str(e)}")
        import traceback
        traceback.print_exc()
        raise

async def fetch_and_process_emails(args):
    credentials = load_gmail_credentials()
    if not credentials:
        print("Failed to load Gmail credentials")
        return 1
        
    service = build("gmail", "v1", credentials=credentials)
    
    processed_count = 0
    
    try:
        email_address = args.email
        
        query = f"to:{email_address} OR from:{email_address}"
        
        if args.minutes_since > 0:
            after = int((datetime.now() - timedelta(minutes=args.minutes_since)).timestamp())
            query += f" after:{after}"
            
        if not args.include_read:
            query += " is:unread"
            
        print(f"Gmail search query: {query}")
        
        results = service.users().messages().list(userId="me", q=query).execute()
        messages = results.get("messages", [])
        
        if not messages:
            print("No emails found matching the criteria")
            return 0
            
        print(f"Found {len(messages)} emails")
        
        for i, message_info in enumerate(messages):
            if args.early and i > 0:
                print(f"Early stop after processing {i} emails")
                break
                
            try:
                message = service.users().messages().get(userId="me", id=message_info["id"]).execute()
                
                email_data = extract_email_data(message)
                
                print(f"\nProcessing email {i+1}/{len(messages)}:")
                print(f"From: {email_data['from_email']}")
                print(f"Subject: {email_data['subject']}")
                
                thread_id, run = await ingest_email_to_langgraph(
                    email_data, 
                    args.graph_name,
                    url=args.url
                )
                
                processed_count += 1
                print(f"✅ Successfully processed email {i+1}")
                
            except Exception as e:
                print(f"❌ Error processing email {i+1}: {str(e)}")
                continue
            
        print(f"\n✅ Processed {processed_count}/{len(messages)} emails successfully")
        return 0
        
    except Exception as e:
        print(f"❌ Error processing emails: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

def parse_args():
    parser = argparse.ArgumentParser(description="Simple Gmail ingestion for LangGraph with reliable tracing")
    
    parser.add_argument(
        "--email", 
        type=str, 
        required=True,
        help="Email address to fetch messages for"
    )
    parser.add_argument(
        "--minutes-since", 
        type=int, 
        default=120,
        help="Only retrieve emails newer than this many minutes"
    )
    parser.add_argument(
        "--graph-name", 
        type=str, 
        default="email_assistant_hitl_memory_gmail",
        help="Name of the LangGraph to use"
    )
    parser.add_argument(
        "--url", 
        type=str, 
        default="http://127.0.0.1:2024",
        help="URL of the LangGraph deployment"
    )
    parser.add_argument(
        "--early", 
        action="store_true",
        help="Early stop after processing one email"
    )
    parser.add_argument(
        "--include-read",
        action="store_true",
        help="Include emails that have already been read"
    )
    parser.add_argument(
        "--rerun", 
        action="store_true",
        help="Process the same emails again even if already processed"
    )
    parser.add_argument(
        "--skip-filters",
        action="store_true",
        help="Skip filtering of emails"
    )
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    exit(asyncio.run(fetch_and_process_emails(args)))