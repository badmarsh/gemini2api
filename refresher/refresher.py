"""
Gemini Cookie Refresher - Playwright automated renewal

Periodically visits the Gemini web page using a headless Chromium browser,
triggering Google's client-side JS to automatically refresh __Secure-1PSIDTS,
then writes updated cookies to the shared data file and notifies gemini2api.
"""
import os
import sys
import json
import time
import requests as http_requests
from playwright.sync_api import sync_playwright

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
STATE_DIR = os.path.join(DATA_DIR, "browser_states")
COOKIES_OUTPUT = os.path.join(DATA_DIR, "refreshed_cookies.json")
GEMINI2API_URL = os.environ.get("GEMINI2API_URL", "http://gemini2api:5918")
API_KEY = os.environ.get("API_KEY", "")
# /admin/* routes are authenticated via verify_admin_key: uses ADMIN_API_KEY if set, falls back to API_KEY,
# matching the server-side auth priority.
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
ADMIN_KEY = ADMIN_API_KEY or API_KEY
# Refresh interval: prefer REFRESH_INTERVAL in minutes, fall back to REFRESH_INTERVAL_SECONDS in seconds.
_interval_seconds = os.environ.get("REFRESH_INTERVAL_SECONDS")
if _interval_seconds is not None:
    REFRESH_INTERVAL = int(_interval_seconds)
else:
    REFRESH_INTERVAL = int(float(os.environ.get("REFRESH_INTERVAL", "8")) * 60)
SINGLE_RUN = os.environ.get("SINGLE_RUN", "false").lower() == "true"


def load_accounts():
    accounts_file = os.path.join(DATA_DIR, "refresher_accounts.json")
    if os.path.exists(accounts_file):
        with open(accounts_file, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    psid = os.environ.get("GEMINI_PSID", "")
    psidts = os.environ.get("GEMINI_PSIDTS", "")
    if psid:
        return [{"id": "account-0", "psid": psid, "psidts": psidts, "label": "Default"}]
    return []


def ensure_state_dir(account_id):
    path = os.path.join(STATE_DIR, account_id)
    os.makedirs(path, exist_ok=True)
    return path


def _state_psid(state_file):
    """Read current __Secure-1PSID in state.json to determine if credentials have rotated."""
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        for c in state.get("cookies", []):
            if c.get("name") == "__Secure-1PSID":
                return c.get("value")
    except Exception:
        return None
    return None


def inject_cookies_to_state(state_dir, psid, psidts):
    state_file = os.path.join(state_dir, "state.json")
    cookies = [
        {"name": "__Secure-1PSID", "value": psid, "domain": ".google.com", "path": "/", "secure": True, "httpOnly": True, "sameSite": "None"},
    ]
    # Only write psidts when non-empty to avoid polluting browser state.
    if psidts:
        cookies.append({"name": "__Secure-1PSIDTS", "value": psidts, "domain": ".google.com", "path": "/", "secure": True, "httpOnly": True, "sameSite": "None"})
    state = {"cookies": cookies, "origins": []}
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f)
    print(f"  [init] Injected cookies from config into state")


def refresh_account(browser, account):
    account_id = account["id"]
    label = account.get("label", account_id)
    state_dir = ensure_state_dir(account_id)
    state_file = os.path.join(state_dir, "state.json")

    # Inject on first run or when PSID has changed in refresher_accounts.json.
    if not os.path.exists(state_file) or _state_psid(state_file) != account["psid"]:
        inject_cookies_to_state(state_dir, account["psid"], account.get("psidts", ""))

    print(f"  [{label}] Opening browser context...")
    context = browser.new_context(
        storage_state=state_file,
        locale="en-US",
        timezone_id="America/New_York",
    )
    page = context.new_page()

    try:
        page.goto("https://gemini.google.com/app", timeout=90000, wait_until="domcontentloaded")
        time.sleep(15)

        if "accounts.google.com" in page.url or "ServiceLogin" in page.url:
            print(f"  [{label}] FAILED - Session expired, redirected to login ({page.url[:60]})")
            return {"id": account_id, "label": label, "status": "expired", "updated_at": time.time()}

        # Gemini serves a signed-OUT landing page at /app (no redirect to the login page), so a
        # URL check alone reports success for cookies Google did not accept - the injected cookies
        # then simply read back unchanged. A signed-in page carries the SNlM0e token (the same
        # marker gemini2api itself mints its session from); the guest page does not.
        if '"SNlM0e"' not in page.content():
            print(f"  [{label}] NOT SIGNED IN - Gemini served the signed-out page ({page.url[:60]}); "
                  "the browser did not accept the injected cookies. Not pushing them to gemini2api.")
            return {"id": account_id, "label": label, "status": "signed_out", "updated_at": time.time()}

        cookies = context.cookies()
        psid = next((c["value"] for c in cookies if c["name"] == "__Secure-1PSID"), None)
        psidts = next((c["value"] for c in cookies if c["name"] == "__Secure-1PSIDTS"), None)

        if psid and psidts:
            context.storage_state(path=state_file)
            print(f"  [{label}] OK - PSIDTS: {psidts[:20]}...")
            return {"id": account_id, "label": label, "psid": psid, "psidts": psidts, "status": "active", "updated_at": time.time()}
        else:
            print(f"  [{label}] FAILED - Cookie not found, may need re-login")
            return {"id": account_id, "label": label, "status": "expired", "updated_at": time.time()}
    except Exception as e:
        print(f"  [{label}] ERROR - {e}")
        return {"id": account_id, "label": label, "status": "error", "error": str(e), "updated_at": time.time()}
    finally:
        context.close()


def notify_gemini2api(account_id, psid, psidts):
    headers = {"Content-Type": "application/json"}
    # Use ADMIN_KEY for /admin/* endpoints
    if ADMIN_KEY:
        headers["Authorization"] = f"Bearer {ADMIN_KEY}"

    # Prefer updating by account ID
    try:
        resp = http_requests.put(
            f"{GEMINI2API_URL}/admin/accounts/{account_id}/cookies",
            json={"psid": psid, "psidts": psidts},
            headers=headers,
            timeout=10
        )
        if resp.status_code == 200:
            print(f"  [notify] {account_id} cookies updated via PUT")
            return True
        elif resp.status_code == 404:
            # Fall back to global reload if account is not found
            resp2 = http_requests.post(
                f"{GEMINI2API_URL}/admin/reload-cookies",
                json={"psid": psid, "psidts": psidts},
                headers=headers,
                timeout=10
            )
            if resp2.status_code == 200:
                print(f"  [notify] cookies reloaded via POST (account not in pool)")
                return True
            elif resp2.status_code == 401:
                print(f"  [notify] auth rejected (401) — set ADMIN_API_KEY/API_KEY to match the server's admin key")
                return False
            else:
                print(f"  [notify] reload failed: {resp2.status_code} {resp2.text[:100]}")
                return False
        elif resp.status_code == 401:
            print(f"  [notify] auth rejected (401) — set ADMIN_API_KEY/API_KEY to match the server's admin key")
            return False
        else:
            print(f"  [notify] PUT failed: {resp.status_code} {resp.text[:100]}")
            return False
    except Exception as e:
        print(f"  [notify] Failed to reach gemini2api: {e}")
        return False


def refresh_all():
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"[{ts}] Starting cookie refresh cycle...")
    print(f"{'='*50}")

    accounts = load_accounts()
    if not accounts:
        print("  [ERROR] No accounts configured!")
        print("  Set GEMINI_PSID/GEMINI_PSIDTS env vars or create data/refresher_accounts.json")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-extensions",
            ]
        )

        for i, account in enumerate(accounts):
            try:
                result = refresh_account(browser, account)
            except Exception as e:
                print(f"  [{account.get('label', account.get('id'))}] Unexpected error: {e}")
                result = {"id": account["id"], "label": account.get("label", account["id"]), "status": "error", "error": str(e), "updated_at": time.time()}
            results.append(result)
            if i < len(accounts) - 1:
                time.sleep(5)

        browser.close()

    with open(COOKIES_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    active = [r for r in results if r.get("status") == "active"]
    for acc in active:
        notify_gemini2api(acc["id"], acc["psid"], acc["psidts"])

    print(f"\n  Summary: {len(active)}/{len(results)} accounts active")


if __name__ == "__main__":
    if SINGLE_RUN:
        refresh_all()
        print("\n[Single run mode] Done, exiting.")
        sys.exit(0)

    print(f"Gemini Cookie Refresher started (interval: {REFRESH_INTERVAL}s; set REFRESH_INTERVAL in minutes)")
    while True:
        try:
            refresh_all()
        except Exception as e:
            print(f"[FATAL] {e}")
        print(f"\nSleeping {REFRESH_INTERVAL}s until next refresh...")
        time.sleep(REFRESH_INTERVAL)