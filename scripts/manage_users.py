import sys
import os
import argparse
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.auth.auth_service import AuthService

def main():
    parser = argparse.ArgumentParser(description="User Management CLI")
    parser.add_argument("action", choices=["create", "list"], help="Action to perform")
    
    args = parser.parse_args()
    
    auth = AuthService()
    
    if args.action == "create":
        username = input("Username: ")
        password = input("Password: ")
        roles = input("Roles (comma-separated, default: viewer): ") or "viewer"
        
        success = auth.register_user(username, password, roles.split(","))
        if success:
            print(f"✅ User {username} created successfully.")
        else:
            print(f"❌ Failed to create user. It might already exist.")

if __name__ == "__main__":
    main()
