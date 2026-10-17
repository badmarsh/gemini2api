"""
Gemini Cookie Refresher - Playwright automated renewal

Periodically visits the Gemini web page using a headless Chromium browser,
triggering Google's client-side JS to automatically refresh __Secure-1PSIDTS,
then writes updated cookies to the shared data file and notifies gemini2api.
"""
import hashlib
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


def _fp(value):
    """Short non-reversible fingerprint for logs. Cookie values are never logged."""
    if not value:
        return "none"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _state_pair(state_file):
    """(PSID, PSIDTS) currently stored in the persisted browser state; ("", "") when absent."""
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return ("", "")
    found = {c.get("name"): c.get("value") for c in state.get("cookies", []) if isinstance(c, dict)}
    return (found.get("__Secure-1PSID") or "", found.get("__Secure-1PSIDTS") or "")


def load_pool_cookies(account_id):
    """The cookie pair the gemini2api pool holds for this account right now, or None.

    refresher_accounts.json is a one-off seed: the pool rotates PSIDTS (and sometimes the PSID
    itself) on its own, so the seed goes stale and Google then serves the signed-out page for it.
    data/accounts.json is the pool's own store on the shared ./data volume, so it tracks that
    rotation. The pool's cookie jar is written on every Set-Cookie and can hold a newer PSIDTS
    than accounts.json, so it wins when it belongs to the same PSID.
    """
    try:
        with open(os.path.join(DATA_DIR, "accounts.json"), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    items = data.get("accounts", []) if isinstance(data, dict) else data
    entry = next((a for a in items if isinstance(a, dict) and a.get("id") == account_id), None)
    if not entry or not entry.get("psid"):
        return None
    psid, psidts = entry["psid"], entry.get("psidts") or ""
    jar_name = hashlib.sha256(psid.encode("utf-8")).hexdigest()[:16] + ".json"
    try:
        with open(os.path.join(DATA_DIR, "cookies", jar_name), "r", encoding="utf-8") as f:
            stored = {c.get("name"): c.get("value") for c in json.load(f) if isinstance(c, dict)}
        if stored.get("__Secure-1PSID") == psid and stored.get("__Secure-1PSIDTS"):
            psidts = stored["__Secure-1PSIDTS"]
    except (OSError, ValueError, TypeError):
        pass
    return (psid, psidts)


def _cookie_list(psid, psidts):
    base = {"domain": ".google.com", "path": "/", "secure": True, "httpOnly": True, "sameSite": "None"}
    cookies = [dict(name="__Secure-1PSID", value=psid, **base)]
    # Only add PSIDTS when non-empty to avoid polluting browser state.
    if psidts:
        cookies.append(dict(name="__Secure-1PSIDTS", value=psidts, **base))
    return cookies


PAGE_SETTLE_SECONDS = 15   # let Google's page JS run so it can rotate __Secure-1PSIDTS
SIGNED_IN_DETECT_SECONDS = 10


def _attempt(browser, state_file, seed=None):
    """Open one browser session and report whether Gemini accepted its cookies.

    seed=None reuses the persisted browser state; otherwise seed is a (psid, psidts) pair injected
    into a fresh context. Returns {"outcome": "ok" | "login" | "signed_out" | "no_cookie", ...};
    only "ok" carries psid/psidts, and only "ok" rewrites state_file.
    """
    options = {"locale": "en-US", "timezone_id": "America/New_York"}
    if seed is None:
        options["storage_state"] = state_file
    context = browser.new_context(**options)
    try:
        if seed is not None:
            context.add_cookies(_cookie_list(*seed))
        page = context.new_page()
        started = time.time()
        page.goto("https://gemini.google.com/app", timeout=90000, wait_until="domcontentloaded")

        # Gemini serves a signed-OUT landing page at /app (no redirect to the login page), so a
        # URL check alone reports success for cookies Google did not accept. A signed-in page
        # carries the SNlM0e token (the same marker gemini2api itself mints its session from);
        # the guest page does not. Poll instead of sleeping blind: a valid page has it at once.
        signed_in = False
        deadline = time.time() + SIGNED_IN_DETECT_SECONDS
        while True:
            if "accounts.google.com" in page.url or "ServiceLogin" in page.url:
                return {"outcome": "login"}
            try:
                if '"SNlM0e"' in page.content():
                    signed_in = True
                    break
            except Exception:
                pass  # page is mid-navigation; look again
            if time.time() >= deadline:
                break
            time.sleep(1)
        if not signed_in:
            return {"outcome": "signed_out"}

        time.sleep(max(0.0, PAGE_SETTLE_SECONDS - (time.time() - started)))
        cookies = context.cookies()
        psid = next((c["value"] for c in cookies if c["name"] == "__Secure-1PSID"), None)
        psidts = next((c["value"] for c in cookies if c["name"] == "__Secure-1PSIDTS"), None)
        if psid and psidts:
            context.storage_state(path=state_file)
            return {"outcome": "ok", "psid": psid, "psidts": psidts}
        return {"outcome": "no_cookie"}
    finally:
        context.close()


def refresh_account(browser, account):
    account_id = account["id"]
    label = account.get("label", account_id)
    state_dir = ensure_state_dir(account_id)
    state_file = os.path.join(state_dir, "state.json")

    # Cookie sources, best first, de-duplicated by cookie pair:
    #   1. the persisted browser state (keeps Google's companion cookies),
    #   2. the pair the gemini2api pool holds now (source of truth, it rotates PSID/PSIDTS itself),
    #   3. the one-off seed in refresher_accounts.json (stale once anything has rotated).
    # The state file is only rewritten by a source that Gemini accepted, so a stale seed can no
    # longer clobber a working browser state.
    state_pair = _state_pair(state_file)
    pool_pair = load_pool_cookies(account_id)
    candidates = []
    seen = {}  # cookie pair -> name of the first source that offered it
    if state_pair[0]:
        candidates.append(("browser state", None, state_pair))
        seen[state_pair] = "browser state"
    else:
        print(f"  [{label}] skipped source browser state: no usable cookie pair (no persisted state)")
    seeds = (
        ("pool", pool_pair),
        ("refresher_accounts.json", (account.get("psid", ""), account.get("psidts") or "")),
    )
    for source, pair in seeds:
        # Every skipped source says why. A silent de-duplication hid the reason for a 0/4 outage:
        # nobody could tell an empty pool source from one identical to the browser state.
        if not (pair and pair[0]):
            print(f"  [{label}] skipped source {source}: no usable cookie pair")
        elif pair in seen:
            print(f"  [{label}] skipped source {source}: same cookie pair as {seen[pair]} "
                  f"(psid={_fp(pair[0])} psidts={_fp(pair[1])})")
        else:
            candidates.append((source, pair, pair))
            seen[pair] = source

    if not candidates:
        print(f"  [{label}] ERROR - no cookies available (no browser state, pool entry or seed)")
        return {"id": account_id, "label": label, "status": "error", "error": "no cookies available", "updated_at": time.time()}

    outcomes = []
    errors = []
    for source, seed, pair in candidates:
        print(f"  [{label}] Opening browser context (source: {source}, psid={_fp(pair[0])} psidts={_fp(pair[1])})...")
        try:
            result = _attempt(browser, state_file, seed)
        except Exception as e:
            print(f"  [{label}] {source}: ERROR - {e}")
            errors.append(str(e))
            continue
        if result["outcome"] == "ok":
            print(f"  [{label}] OK (source: {source}) - PSIDTS fp {_fp(result['psidts'])}")
            # "unchanged": the pool already holds exactly this pair, so pushing it again would only
            # make the proxy reload a healthy client for nothing (see notify_active).
            unchanged = bool(pool_pair) and (result["psid"], result["psidts"]) == tuple(pool_pair)
            return {"id": account_id, "label": label, "psid": result["psid"], "psidts": result["psidts"],
                    "status": "active", "source": source, "unchanged": unchanged,
                    "updated_at": time.time()}
        print(f"  [{label}] {source}: rejected ({result['outcome']})")
        outcomes.append(result["outcome"])

    if "login" in outcomes or "no_cookie" in outcomes:
        print(f"  [{label}] FAILED - Session expired, redirected to login or cookie missing")
        return {"id": account_id, "label": label, "status": "expired", "updated_at": time.time()}
    if outcomes:
        print(f"  [{label}] NOT SIGNED IN - Gemini served the signed-out page for every cookie source "
              f"tried ({', '.join(src for src, _, _ in candidates)}). Not pushing anything to gemini2api.")
        return {"id": account_id, "label": label, "status": "signed_out", "updated_at": time.time()}
    return {"id": account_id, "label": label, "status": "error", "error": "; ".join(errors), "updated_at": time.time()}


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


def pool_session_ready(account_id):
    """True only when the proxy confirms this account is active and its session is healthy.

    Any doubt (proxy unreachable, auth rejected, account missing) answers False so the caller
    pushes the cookies as before.
    """
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"} if ADMIN_KEY else {}
    try:
        resp = http_requests.get(f"{GEMINI2API_URL}/admin/accounts", headers=headers, timeout=5)
        if resp.status_code != 200:
            return False
        data = resp.json()
    except Exception:
        return False
    items = data.get("accounts", []) if isinstance(data, dict) else data
    entry = next((a for a in items if isinstance(a, dict) and a.get("id") == account_id), None)
    return bool(entry) and entry.get("status") == "active" and entry.get("is_healthy") is True


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
    notify_active(active)

    print(f"\n  Summary: {len(active)}/{len(results)} accounts active")


def notify_active(active):
    """Push only the accounts whose cookies changed (or whose pool session is not healthy).

    Each push makes the proxy reload that account's client; doing it for an unchanged pair every
    cycle needlessly opened an unhealthy window on all four accounts. Defined after refresh_all so
    the source-level guard in test_refresher_signed_in_check keeps seeing the notify call below it.
    """
    for acc in active:
        if acc.get("unchanged") and pool_session_ready(acc["id"]):
            print(f"  [notify] {acc['id']} skipped: cookies unchanged and the pool session is healthy")
            continue
        notify_gemini2api(acc["id"], acc["psid"], acc["psidts"])


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