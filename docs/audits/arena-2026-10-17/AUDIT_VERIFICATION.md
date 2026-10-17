# Audit verification — `AUDIT_SK.md` vs. the actual codebase

**Verifier branch:** `arena/28408e78-gemini2api-argon-proxy`
**Verified against commit:** `d963fff01031a985dc479e27c0bd7abd2963f250`
**Verification date:** 2026-10-17 UTC
**Method:** every falsifiable claim in `AUDIT_SK.md` (and the earlier
`docs/audits/arena-2026-10-16/ARGON_PROXY_REVIEW.md`) was re-derived from the working tree,
from a real test run in a fresh venv, and from the GitHub API. Nothing was accepted on the
document's own authority.

## Verdict

**The audit is REAL.** It is not hallucinated. Every quantitative claim reproduced **exactly**,
including numbers that would be near-impossible to guess (per-file test counts after
`parametrize` expansion, a CI run ID, a commit SHA and its exact subject line, a report digest
path, eval summary percentiles).

One item in the audit's own "open points" list is **overstated** — SSRF/file-serving hardening
is largely already implemented. That is the only inaccuracy found, and it errs conservative
(claiming work remains that is mostly done), which is the safe direction.

Additionally, verification surfaced **one gap that is worse than the audit describes**: inbound
request-body buffering is unbounded *and unconditional*, not merely "missing upload limits".

## 1. Gates — reproduced, not trusted

A fresh venv was built from `requirements.txt` + `requirements-dev.txt` and the gates re-run:

| Claim in `AUDIT_SK.md` | Command actually run | Result | Match |
|---|---|---|---|
| `1104 passed, 1 warning` | `API_KEY=sk-test-key pytest -q -p no:cacheprovider` | `1104 passed, 1 warning in 15.45s` | **EXACT** |
| `240 passed` (evals) | `API_KEY=sk-test-key pytest evals -q -p no:cacheprovider` | `240 passed in 164.93s` | **EXACT** |
| ruff clean | `ruff check .` | `All checks passed!` | **EXACT** |

The single warning is the same `StarletteDeprecationWarning` from `fastapi/testclient.py` the
audit's "1 warning" implies.

## 2. Numeric claims that would be hard to fabricate

| Claim | Verified value | Source |
|---|---|---|
| T0 run `20261016T225213Z-30667a`, 52 records, no abort | `records: 52`, `aborted: false`, `abort_reason: ""` | `evals/state.json` |
| resume dict `{"run_id": ..., "tier": "t0", "records": 52, "aborted": false}` | identical | `evals/state.json` |
| 20 observed fallbacks | `"fallback_count": 20` | `evals/reports/20261016T225213Z-30667a.json` → `summary` |
| p95 1813 ms | `"p95": 1813.0` (p50 13.0, max 7219.0) | same |
| worker contract `gemini-3.8-flash` | `"model": "gemini-3.8-flash"` | same → `worker_contract` |
| caps: concurrency 2, argon cap, abort after 3 | `{"concurrency": 2, "argon_cap": 200, "abort_after_consecutive_upstream_errors": 3}` | same → `caps` |
| CI job `test-python-3-14`, run `39040306228`, green | run exists, all 4 jobs ✓, incl. `Test (Python 3.14, advisory)` | `gh run view 39040306228` |
| commit `05e7604 fix(gemini): accept literal control characters in wire frames` | SHA `05e76043f10d…`, subject **exact**; touches `gemini_client.py`, `test_gemini_wire_json.py`, `CHANGELOG.md` | GitHub API |
| base commit `0499f80`, prompt commit `2d06316` | both resolve; `2d06316`'s own message documents bug #1 ("a caller-supplied system/developer message wins") | GitHub API |

## 3. Per-file test counts — all exact after `parametrize` expansion

Naive `grep -c "def test_"` disagrees with the audit for two files; expanding `parametrize`
reconciles them **exactly**, which is a strong signal the counts were read from a real run:

| Audit claim | `def test_` | `parametrize` | Collected | Match |
|---|---:|---|---:|---|
| `test_prometheus_metrics.py` — 11 | 9 | 1 × 3 values | 8 + 3 = **11** | ✓ |
| `test_security_headers.py` — 9 | 8 | 1 × 2 paths | 7 + 2 = **9** | ✓ |
| `test_slo_overview.py` — 15 | 15 | — | **15** | ✓ |
| `test_request_explorer.py` — 16 | 16 | — | **16** | ✓ |
| `test_config_readiness.py` — 14 | 14 | — | **14** | ✓ |
| `test_dashboard_accessibility.py` — 9 | 9 | — | **9** | ✓ |
| `test_account_pool_operations.py` — 13 | 13 | — | **13** | ✓ |

## 4. Structural / behavioural claims — all present

| Claim | Evidence in tree |
|---|---|
| `RegenerationBudget` shared by **all four** router families, buffered **and** streaming | `app/core/regeneration_budget.py`; `openai.py:465,819`, `claude.py:164,414`, `gemini.py:249,402`, `responses.py:199,295` |
| Budget exposed at `/admin/status.regenerations` | `app/routers/admin.py:115` |
| `strict=False` on every client-side wire decode | 9 occurrences in `gemini_client.py`; the only `json.loads` without it is inside a **docstring** (line 1116) |
| `test_truncated_json_is_never_repaired` contract preserved | `tests/unit/test_tool_retry.py:121` |
| Endpoints `/admin/metrics`, `/admin/config`, `/admin/debug-metrics/{request_id}`, `/admin/debug/tool-json`, `/admin/argon/reset`, `POST /admin/accounts/{id}/status` | `app/routers/admin.py:185,149,162,208,226,333` |
| `build_slo()`, `AccountPool.set_account_enabled()` | `app/core/slo.py:31`, `app/core/account_pool.py:708` |
| Five locales with **identical key sets** | `static/app/i18n.js`: `zh-CN`/`en-US`/`ja-JP`/`ko-KR`/`zh-TW` = **411 keys each**, 0 missing, 0 extra |
| CSP "limited to the two CDNs the console really uses" | `security_headers.py` allows `cdn.tailwindcss.com` (script) + `cdnjs.cloudflare.com` (style/font); `grep -rhoE` over `static/` finds **exactly those two** origins and no others |
| Config switches + defaults | `.env.example:75,81,82,85,91` ↔ `app/config.py:136,140,141,120,126` = `merge`/`true`/`true`/`360`/`false` — all match |

## 5. The one inaccuracy: SSRF / file-serving is *already* hardened

Open point 5 lists "SSRF/file-serving hranice" as unresolved. The tree disagrees:

- `app/utils/net_guard.py` implements `assert_safe_url` / `is_safe_url` /
  `resolve_redirect_location`, rejecting private, loopback, link-local (incl. cloud metadata
  `169.254.169.254`) and reserved targets.
- It guards **every user-controlled outbound URL**: `api_forwarder._build_safe_target_url`
  (OpenAI-compatible `:62`, Anthropic `:363`; the Gemini path `_forward_gemini_api` is a `501`
  stub with no URL), `api_keys.py:255` (`fetch_models`, VULN-004), and
  `file_upload.py` with **per-hop redirect re-validation** and a `MAX_REMOTE_REDIRECTS = 5` cap
  (VULN-005).
- Path traversal is guarded in `image_store.get_image_path` (rejects `/`, `\`, `..`), which backs
  `GET /images/{image_id}`.
- The only unguarded outbound call is `admin.py:461` (`/admin/check-update`), which uses a
  **hardcoded** `https://api.github.com/...` constant — no user input, so not an SSRF surface.

Conclusion: this open item should be **closed as already-done**, downgraded to "add a coverage
assertion + fix the doc". Not a hallucination — an over-conservative carry-over.

## 6. New finding the audit understates (worse than described)

Open point 5 mentions "request-size/upload limity" as open. The reality is sharper:

```python
# app/main.py:534-541  (log_capture_middleware)
if is_api and request.method == "POST" and content-type startswith application/json:
    request.state._body_cache = await request.body()
```

- The full body is buffered into memory with **no `Content-Length` check and no byte cap**.
- It runs **unconditionally** — *not* gated by `LOG_BODIES_ENABLED`. Only the *logging* of the
  cached body is gated (`app/main.py:576`). So the memory exposure exists in the **default
  configuration**, for every JSON POST to `/v1/`, `/openai/`, `/claude/`, `/gemini/`, `/v1beta/`.
- There is no size-limiting middleware anywhere (`app/main.py:211,222,506,518` are CORS,
  SlowAPI, security-headers and log-capture).

This is a default-on unbounded-memory DoS vector, and it is the highest-severity item found.

Related, same subsystem: `_add_debug_log` scrubs the **path** (`_scrub_sensitive(path)`,
`app/main.py:454`) but passes `request_body` / `response_body` **raw** into
`create_log_record`, and `LogStore.flush()` writes them verbatim to `data/logs.json` — a
bind-mounted, persistent volume. An `sk-...` key or `?token=` URL pasted into a message body is
persisted in cleartext. This is exactly the audit's open item "perzistencia secret redaction",
and it is confirmed real.

## 7. Confirmed-still-open critique (substantial, verified in code)

| # | Gap | Evidence it is genuinely absent | Severity |
|---|---|---|---|
| A | **Unbounded inbound body buffering** | `app/main.py:540`, no cap, no middleware, default-on | **P0** |
| B | **Persisted log bodies not secret-scrubbed** | `_scrub_sensitive` applied to path only; `LogRecord.request/response` → `data/logs.json` raw | **P0** |
| C | **No per-key / per-tenant quotas or budgets** | `limiter.py` keys on client IP only, one global `rate_limit_max/window`, `rate_limit_enabled` defaults False; `ApiKeyEntry` has **no** quota/budget/tenant/rate field | **P1** |
| D | **No admin audit trail, no alerts/webhooks** | zero matches for `webhook`/`alert` in `app/`; zero `logger.info`/audit records in `app/routers/admin.py` — enable/disable account, breaker reset, settings overrides, restart, API-key CRUD all mutate state unlogged | **P1** |
| E | **No redacted replay / fixture library** | no `fixtures/` directory anywhere in the repo; `evals/fake_upstream/` holds hand-written frames only | **P2** |
| F | **No configurable routing profiles** | no `profile` concept in `app/config.py`, `model_mapping.py`, `gem_mapping.py`; routing is a flat global env-switch set | **P2** |
| G | **No parser/stream fuzz or property-based coverage** | zero matches for `hypothesis`/`fuzz`/`atheris` in `app/`, `tests/`, `evals/`, `requirements*.txt` | **P2** |
| H | **Python 3.14 pin divergence unresolved** | CI's 3.14 job `sed`-rewrites `pydantic==2.11.3` → `pydantic>=2.12`, so **production pins are never tested on 3.14**; no decision recorded | **P3** |
| — | SSRF / file-serving | **already implemented** — see §5; close the doc item, add a coverage test | n/a |
| — | OTel push exporter | documented deliberate non-goal (open point 6); leave as-is | n/a |

## 8. Bottom line

`AUDIT_SK.md` is a faithful, evidence-backed report. Its baseline/final gate table, its
per-finding RED→GREEN claims, its cited file paths, its test counts and its CI/commit references
all hold up under independent re-execution. Its "Zámerné non-fixy" list is also consistent with
the tree (e.g. `test_account_card_rendering.py` still present with its original assertions plus
helpers; no regex-rescue parser was added).

The work is therefore worth continuing rather than re-litigating: the substantial remaining
critique is items **A–H** above, and `RESOLUTION_PROMPT.md` in this directory is a complete work
order to close them.
