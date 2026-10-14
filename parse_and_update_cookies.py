#!/usr/bin/env python3
"""
Helper script to update account cookies in gemini2api.
Can accept:
  1) Raw cookie header string: python parse_and_update_cookies.py <account_id> "raw_cookie_header_or_curl"
  2) Direct values: python parse_and_update_cookies.py <account_id> <__Secure-1PSID> <__Secure-1PSIDTS>
"""

import sys, os, re, json, urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5918")
API_KEY = os.environ.get("API_KEY", "")


def parse_cookies(input_str):
    psid = None
    psidts = None

    # Try matching __Secure-1PSID and __Secure-1PSIDTS
    m_psid = re.search(r"__Secure-1PSID=([^;]+)", input_str)
    if m_psid:
        psid = m_psid.group(1).strip()
    m_psidts = re.search(r"__Secure-1PSIDTS=([^;]+)", input_str)
    if m_psidts:
        psidts = m_psidts.group(1).strip()

    return psid, psidts


def update_account(account_id, psid, psidts):
    url = f"{BASE_URL}/admin/accounts/{account_id}/cookies"
    data = json.dumps({"psid": psid, "psidts": psidts}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            print(f"[{account_id}] SUCCESS: {resp.status} {body}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"[{account_id}] FAILED: {e.code} {body}")
        return False
    except Exception as e:
        print(f"[{account_id}] ERROR: {e}")
        return False


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python parse_and_update_cookies.py <account_id> <cookie_string_or_psid> [<psidts>]")
        print("Example:")
        print("  python parse_and_update_cookies.py account-0 \"__Secure-1PSID=...; __Secure-1PSIDTS=...\"")
        print("  python parse_and_update_cookies.py account-0 <psid_val> <psidts_val>")
        sys.exit(1)

    account_id = sys.argv[1]
    arg1 = sys.argv[2]

    if len(sys.argv) >= 4:
        psid = arg1
        psidts = sys.argv[3]
    else:
        psid, psidts = parse_cookies(arg1)
        if not psid:
            print(f"Could not extract __Secure-1PSID from input string.")
            sys.exit(1)

    print(f"Updating {account_id} with psid={psid[:10]}... psidts={psidts[:10] if psidts else 'None'}...")
    ok = update_account(account_id, psid, psidts or "")
    if ok:
        print(f"Account {account_id} updated and active!")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
