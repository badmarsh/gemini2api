#!/usr/bin/env python3

import requests
import sys
import os

# Gateway server address
BASE_URL = "http://localhost:5918"

def add_cookies(account_id, psid, psidts):
    """
    Add or update cookies for the specified account via Admin API.
    """
    url = f"{BASE_URL}/admin/accounts/{account_id}/cookies"
    
    # Construct request payload
    payload = {
        "psid": psid,
        "psidts": psidts
    }
    
    # Send PUT request
    try:
        response = requests.put(url, json=payload)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        return response
    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python add_cookies.py <account_id> <__Secure-1PSID> <__Secure-1PSIDTS>")
        sys.exit(1)
    
    account_id = sys.argv[1]
    psid = sys.argv[2]
    psidts = sys.argv[3]
    
    print(f"Adding cookies for account {account_id}")
    print(f"PSID: {psid}")
    print(f"PSIDTS: {psidts}")
    
    add_cookies(account_id, psid, psidts)