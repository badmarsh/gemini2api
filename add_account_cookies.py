#!/usr/bin/env python3
"""
Script to add cookies to account-0 via the admin API
"""

import requests
import sys
import os
import json

# Gateway server address
BASE_URL = "http://localhost:5918"

def add_cookies_to_account(account_id, psid, psidts):
    """
    Add or update cookies for the specified account via Admin API.
    """
    # Construct request URL
    url = f"{BASE_URL}/admin/accounts/{account_id}/cookies"
    
    # Construct request payload
    payload = {
        "psid": psid,
        "psidts": psidts
    }
    
    # Read API key from environment variable or prompt
    api_key = os.environ.get("API_KEY", "")
    
    # Set request headers
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    print(f"Attempting to add cookies for account: {account_id}")
    print(f"PSID: {psid}")
    print(f"PSIDTS: {psidts}")
    print(f"Using API key: {api_key[:4]}****{api_key[-4:]}")
    print("\n⚠️  IMPORTANT: The cookies you provide must be fresh and not expired!")
    print("   - They must be copied from an Incognito window after signing in")
    print("   - Do NOT sign out after copying - leave the session open")
    print("   - If you get a 503 error about 'Cookie expired', you need new cookies")
    
    try:
        # Send PUT request
        response = requests.put(url, json=payload, headers=headers)
        print(f"\nStatus Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            print("\n✅ Successfully added cookies to account!")
            print("Please wait for the cooldown period (30-60 minutes) before testing generation.")
            print("Then run verification tests on all accounts at low rate.")
            return True
        else:
            print(f"\n❌ Failed to add cookies. Status: {response.status_code}")
            if "expired" in response.text.lower():
                print("💡 The cookies appear to be expired. Please provide fresh cookies.")
                print("   - Sign in to Google in an Incognito window")
                print("   - Copy both __Secure-1PSID and __Secure-1PSIDTS cookies")
                print("   - Close the window WITHOUT signing out")
            return False
            
    except Exception as e:
        print(f"\nError: {e}")
        return False

def main():
    if len(sys.argv) != 3:
        print("Usage: python add_account_cookies.py <__Secure-1PSID> <__Secure-1PSIDTS>")
        print("Example: python add_account_cookies.py abc123 xyz789")
        print("\n⚠️  IMPORTANT: The cookies must be fresh and not expired!")
        print("   - Copy from an Incognito window after signing in to Google")
        print("   - Do NOT sign out after copying")
        return
    
    psid = sys.argv[1]
    psidts = sys.argv[2]
    
    success = add_cookies_to_account("account-0", psid, psidts)
    
    if success:
        print("\n🎉 Cookies successfully added to account-0!")
        print("Please wait for the cooldown period (30-60 minutes) before testing generation.")
        print("Then run verification tests on all accounts at low rate.")
    else:
        print("\n💥 Failed to add cookies. Please check the error message above.")

if __name__ == "__main__":
    main()