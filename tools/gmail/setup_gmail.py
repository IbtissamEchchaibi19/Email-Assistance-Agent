import os
import sys
import json
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../")))

from google_auth_oauthlib.flow import InstalledAppFlow

def main():
    secrets_dir = Path(__file__).parent.absolute() / ".secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    
    secrets_path = secrets_dir / "secrets.json"
    if not secrets_path.exists():
        print(f"Error: Client secrets file not found at {secrets_path}")
        print("Please download your OAuth client ID JSON from Google Cloud Console")
        print("and save it as .secrets/secrets.json")
        return 1
    
    print("Starting Gmail API authentication flow...")
    print("A browser window will open for you to authorize access.")
    
    try:
        SCOPES = [
            'https://www.googleapis.com/auth/gmail.modify',
            'https://www.googleapis.com/auth/calendar'
        ]
        
        flow = InstalledAppFlow.from_client_secrets_file(
            str(secrets_path),
            SCOPES
        )
        
        credentials = flow.run_local_server(port=0)
        
        token_path = secrets_dir / "token.json"
        token_data = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes,
            'universe_domain': 'googleapis.com',
            'account': '',
            'expiry': credentials.expiry.isoformat() + "Z"
        }
        
        with open(token_path, 'w') as token_file:
            json.dump(token_data, token_file)
            
        print("\nAuthentication successful!")
        print(f"Access token stored at {token_path}")
        return 0
    except Exception as e:
        print(f"Authentication failed: {str(e)}")
        return 1

if __name__ == "__main__":
    exit(main())