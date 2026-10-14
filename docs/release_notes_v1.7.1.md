# Gemini2API v1.7.1: Security Fix, Failover Reliability & Console Hardening

This is a patch release on top of v1.7.0. It closes an authentication bypass that is present in the published v1.7.0 image, makes multi-account failover and long-running streams more reliable, and tightens secret handling in the admin API and web console. **Upgrading from v1.7.0 is recommended.**

---

### Highlights in v1.7.1

#### 1. Security: Static-Extension Authentication Bypass Closed
- **The bug (present in v1.7.0)**: the public-path check treated any request whose path ended in a static extension (`.png`, `.js`, `.css`, ...) as public, regardless of HTTP method or route prefix. For example, `DELETE /admin/accounts/test.png` skipped both `verify_api_key` and `verify_admin_key`.
- **The fix**: the static-asset exemption now applies only to `GET` and `HEAD`, and never under `/admin`, `/v1`, `/v1beta`, `/openai`, `/claude` or `/gemini`. The `/admin` prefix check is segment-based (`/administrator.png` is not treated as an admin path), and `verify_admin_key` no longer exempts any path.
- **Regression tests** cover the bypass and the prefix matching.
- **Housekeeping**: removed `add_cookies.py` (sent no auth header) and `add_account_cookies.py` (printed full cookies); both depended on an undeclared `requests` package. `parse_and_update_cookies.py` is unchanged. `/admin/check-update` now queries `badmarsh/gemini2api` instead of upstream.

#### 2. Failover and Stream Reliability
- **Session leases**: the shared HTTP session is now leased per in-flight request. When cookies are reloaded or the impersonation fingerprint rotates, the old session is closed by its last user instead of being closed underneath running streams.
- **Timeout failover**: a hung request (builtin/asyncio timeout or libcurl timeout code 28) now fails over to the next account immediately instead of burning further same-account retries. Same-account retry exhaustion has its own exception type.
- **Structured stall detection**: stall classification matches exception types and codes only. Previously it matched free text in error messages, which could misclassify ordinary 4xx responses whose bodies contained words like "timeout".
- **Stricter Google error-template detection**: a reply is treated as Google's error boilerplate only when it is one of the known sentences with at most a short single-line tail. A genuine answer that merely starts with the same words is no longer discarded.
- **Truncation visibility**: when the 120s chunk timeout ends a reply early, a `WARNING` with the emitted and unparsed buffer sizes is logged. Behavior is unchanged.
- **Cookie refresher**: detects a session that redirected to the Google login page and reports it as expired, isolates per-account failures so one account cannot abort a refresh run, and no longer passes `--single-process` / `--no-zygote` to the browser.
- **`ROTATION_STRATEGY`** accepts `round_robin`, `Round-Robin` and padded values, so the value documented in the README no longer raises at startup.
- Admin endpoint messages (`/admin/restart`, web-chat cleanup) are now in English.

#### 3. Secret Masking and Web Console Hardening
- **Admin API masking**: `psidts` is now masked in account status responses alongside `psid`, and API key listings use the shared `mask_secret` helper. Masked values now show the first 6 and last 4 characters (previously 4 and 4); short values are masked more tightly.
- **Domain services**: the console's account, chat, telemetry, storage and validation logic moved into `static/app/services/`, with constants centralized in `static/app/config/constants.js`.
- **Resilience**: per-section error boundaries with retry in the component loader, `AbortController` timeouts on async calls and streams, and 200-item ring buffers for logs and playground history.
- **`safeStorage`**: a `localStorage` wrapper with in-memory fallback and quota protection. The stored console token is **obfuscated** (XOR mask plus Base64), which keeps it from being stored as plain text but is **not encryption** and should not be relied on as a security boundary.
- New unit and integration tests cover masking, account pool state transitions, model alias translation and thinking-block extraction.

#### 4. Dependencies, CI and Tests
- **Dependencies**: `fastapi` 0.120.4 to 0.143.0 and `starlette` 0.49.1 to 1.8.0, pinned together. The Starlette >= 0.49.1 floor for CVE-2025-62727 is preserved. `opentelemetry-api` is added as a transitive dependency of FastAPI.
- **Tests**: test runs are isolated from the real `data/` directory via `DATA_DIR`; added coverage for the persisted-cookie whitelist, full-history prompts for missing or stale `conversation_id`, failover classification and in-flight stream reloads; the saturated-pool test tolerates Windows timer granularity.
- **CI**: the `pip-audit` step is report-only and no longer produces red annotations.
- **Docs**: the CHANGELOG, UI templates, stylesheets and scripts are localized to English, and clone instructions point to the `badmarsh/gemini2api` fork.
- **Verification**: Checked on a clean export of the release commit in a fresh virtual environment built from `requirements.txt` and `requirements-dev.txt`: 785 passed, 1 skipped (a POSIX-permission test that only runs on Linux/CI), `ruff check .` clean, `pip check` clean.

---

### Upgrade Notes
- Pull the new image (`ghcr.io/badmarsh/gemini2api:1.7.1` or `latest`) and restart. No configuration changes are required.
- Masked key and cookie values shown in the console and admin API now use the 6/4 format described above.
- The Docker image pins the new FastAPI/Starlette stack. The test suite does not exercise the app lifespan, `StaticFiles` or SSE under a live server, so a local smoke test was run before release: the app was started with uvicorn from a clean export with a temporary `DATA_DIR`. `/health`, the console, a static file requested with a `Range` header (206 with `Content-Range`), authenticated and unauthenticated API and admin requests, the auth-bypass regression case (`DELETE /admin/accounts/test.png` returns 401) and an SSE chat stream (`data:` chunks ending in `[DONE]`) all behaved correctly. Streaming of real model output from Google was not exercised, because it needs live account cookies.
