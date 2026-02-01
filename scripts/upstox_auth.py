import sys
import os
import json
import requests
from pathlib import Path
from urllib.parse import urlencode

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import argparse
CONFIG_PATH = ROOT / "config" / "credentials.json"

def generate_auth_url(api_key: str, redirect_uri: str):
    """Generates the Upstox login URL for the operator."""
    base_url = "https://api.upstox.com/v2/login/authorization/dialog"
    params = {
        "response_type": "code",
        "client_id": api_key,
        "redirect_uri": redirect_uri
    }
    return f"{base_url}?{urlencode(params)}"

def exchange_code_for_token(api_key: str, api_secret: str, redirect_uri: str, code: str):
    """Exchanges the authorization code for a persistent access token."""
    url = "https://api.upstox.com/v2/login/authorization/token"
    headers = {
        'accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    data = {
        'code': code,
        'client_id': api_key,
        'client_secret': api_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }
    
    response = requests.post(url, headers=headers, data=data)
    response.raise_for_status()
    return response.json()

def save_credentials(data: dict):
    """Saves the access token and API keys to config/credentials.json."""
    from core.auth.credentials import credentials
    credentials.save(data)
    print(f"✅ Credentials saved.")

def main():
    parser = argparse.ArgumentParser(description="Upstox OAuth2 Authentication Tool")
    parser.add_argument("--api-key", help="Upstox API Key")
    parser.add_argument("--api-secret", help="Upstox API Secret")
    parser.add_argument("--redirect-uri", help="Upstox Redirect URI")
    parser.add_argument("--code", help="Authorization code received after login")
    
    args = parser.parse_args()
    
    if not args.code:
        if not all([args.api_key, args.redirect_uri]):
            print("❌ Error: --api-key and --redirect-uri are required to generate the Auth URL.")
            return
        url = generate_auth_url(args.api_key, args.redirect_uri)
        print("\nStep 1: Open the following URL in your browser and log in:")
        print(f"\n{url}\n")
        print("Step 2: After logging in, you will be redirected. Copy the 'code' parameter from the URL.")
        print("Step 3: Run this script again with --code, --api-key, --api-secret, and --redirect-uri.")
    else:
        if not all([args.api_key, args.api_secret, args.redirect_uri]):
            print("❌ Error: --api-key, --api-secret, and --redirect-uri are required to exchange the code.")
            return
            
        print("🔄 Exchanging code for access token...")
        try:
            token_data = exchange_code_for_token(args.api_key, args.api_secret, args.redirect_uri, args.code)
            
            # Combine keys and token data
            final_creds = {
                "api_key": args.api_key,
                "api_secret": args.api_secret,
                "redirect_uri": args.redirect_uri,
                **token_data
            }
            save_credentials(final_creds)
        except Exception as e:
            print(f"❌ Failed to exchange code: {e}")

if __name__ == "__main__":
    main()
