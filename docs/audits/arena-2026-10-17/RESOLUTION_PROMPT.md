# Resolution prompt — close the substantial remaining critique in `gemini2api-argon-proxy`

> Copy everything below the line into a fresh agent session in this repository.
> It is self-contained: it states the verified starting point, the exact gaps with file/line
> evidence, the non-negotiable repo conventions, the acceptance gates, and the order of work.

> **Companion work order.** A second, external assessment of this repository was verified
> separately and produced items **K–R** in `RESOLUTION_PROMPT_ASSESSMENT.md` — including three live
> defects (K: `/v1/responses` reports upstream error phrases as success; L: dead `saw_frame`
> protocol-drift blind spot; M: image endpoint bypasses model resolution). **Treat K as P0 and
> sequence it with items A and B below** — K is a live correctness bug affecting every
> `/v1/responses` caller today, so it belongs in the same first wave. The two work orders share the
> same hard constraints and are designed to compose; L feeds item A's diagnostics work, and N (lint
> widening) should only happen after K/L/M land, since `F841` is what flags them.

---

## Mission

An independent re-verification of `AUDIT_SK.md` confirmed that report is **accurate** — all three
gates reproduce exactly (`1104 passed, 1 warning` / `240 passed` evals / `ruff` clean), all cited
files, endpoints, test counts, the CI run `39040306228` and commit `05e7604` are real. **Do not
redo, re-litigate, or "re-verify" that work.** Treat it as a trusted baseline.

Your job is to **fully close** the critique that verification proved is still open. Eight gaps,
labelled **A–H** below, plus one documentation correction (**I**) and one hygiene item (**J**), each with the evidence that it is genuinely absent. One item the previous
audit listed as open (**SSRF / file-serving**) was proven **already implemented** — you must close
it as a documentation/coverage correction, not re-implement it.

Work in this order: **K → A → B → C → D → H → E → F → G**, then the correction **I** and the hygiene
item **J**. **K** (from `RESOLUTION_PROMPT_ASSESSMENT.md`) joins **A** and **B** in the first wave:
all three are P0, and K is the only one that is already mis-serving live traffic today.

---

## Hard constraints (violating any of these fails the task)

These are existing, enforced repo invariants. Read them before writing code.

1. **Gates must stay green and may only grow.** After your work:
   - `pytest -q` ≥ **1104 passed**, 0 failed
   - `pytest evals -q -p no:cacheprovider` ≥ **240 passed**, 0 failed
   - `ruff check .` → `All checks passed!`
   Run them with the repo's venv and `API_KEY=sk-test-key` set, e.g.
   `API_KEY=sk-test-key .venv/bin/python -m pytest -q`. If no venv exists, create one from
   `requirements.txt` + `requirements-dev.txt`.

2. **Never weaken, skip, xfail, or delete an existing test** to make a gate pass. Never loosen an
   assertion. The prior audit explicitly pinned this and it still holds. In particular these
   contracts must keep passing unchanged:
   - `tests/unit/test_tool_retry.py::test_truncated_json_is_never_repaired` — truncated JSON is
     **never** repaired; no regex-rescue parser, no synthesised closing brackets/quotes.
   - `tests/unit/test_env_example_sync.py` — **bidirectional** drift guard. Every `Settings` field
     in `app/config.py` must have a matching `KEY=` line in `.env.example`, and every `.env.example`
     key must have a matching `Settings` field. Add both sides together, always.
   - `tests/unit/test_account_card_rendering.py`, `test_argon_system_prompt.py`,
     `test_argon_request_uuid_pairing.py`, `test_security_headers.py`.

3. **i18n parity is exact and enforced.** `static/app/i18n.js` currently has **411 keys in each of
   `zh-CN`, `en-US`, `ja-JP`, `ko-KR`, `zh-TW`** with zero drift. Any new console string must be
   added to **all five** locales in the same commit. Never hardcode UI text in a template or JS.

4. **Privacy boundaries are absolute.** Never log, persist, export, or render: prompt/message
   content (except through the explicitly gated body-logging path you are hardening in B), cookies,
   `accounts.json` contents, account IDs in visible text, `Authorization` headers, API keys, or
   `.env` values. Where an identifier is genuinely needed for correlation, use the existing
   short one-way account hash convention. Follow the redaction doctrine already written in
   `app/core/prometheus_metrics.py` (module docstring, "hard requirements, not preferences").

5. **Label cardinality and bounded state.** Diagnostics stay bounded and in-memory (or in the
   existing `data/` persistence layer). No new long-term tracing backend. Prometheus label values
   remain capped the way `prometheus_metrics.py` already caps them (max 12 per label, remainder
   collapsed to `other`); never add a label carrying a request id, account hash, prompt, or client
   model.

6. **Do not change the public Argon protocol**: the 81-element thinking payload, pro-only routing,
   pool capacity 2, the accepted timeout/fallback rules, the 240 s mid-stream inter-chunk gap
   (deliberately preserved — there is an eval guard for the `gap200` scenario), and
   `SERVER_SIDE_CONTINUATION=false` default. Do not switch serving model after content has been
   streamed. Do not duplicate fallback logic outside `app/core/argon_fallback.py`.

7. **No new runtime dependencies** unless a gap genuinely cannot be closed without one. If you add
   a dev-only dependency (e.g. for G), it goes in `requirements-dev.txt` only, never
   `requirements.txt`, and the CI 3.14 job's `sed` rewrite of the pydantic pin must keep working.

8. **Admin surface stays admin-key protected** via `verify_admin_key` (`app/core/auth.py:109`).
   Unknown ids return JSON `404`, never a stack trace or echoed identifier. Attach
   `apply_security_headers` semantics to any new response path (the middleware at
   `app/main.py:506` already covers everything — do not bypass it).

9. **Persistence uses the existing atomic helpers** — `app/utils/atomic_io.py::atomic_write_json`,
   `os.replace` semantics, UTF-8 explicit, private file modes where the repo already does that.
   Never introduce a non-atomic write to `data/`.

10. **SSRF stays centralised.** All user-controlled outbound URLs must go through
    `app/utils/net_guard.py` (`assert_safe_url` / `is_safe_url` / `resolve_redirect_location`).
    Do not add a second, parallel SSRF check.

11. **Document everything you change** in `CHANGELOG.md` under `[Unreleased]`, in the existing
    house style (Fixed / Security / Changed / Added), stating the *behavioural* consequence, not
    just the file touched. Update `.env.example` comments for every new switch. Update
    `AUDIT_SK.md`'s open-points list to reflect what you closed, and correct the SSRF item per §I.

12. **Stay on your assigned branch.** Commit there; do not create or push other branches. Keep
    generated artifacts, `.venv*`, `data/`, `.env`, cookies, `accounts.json`, `api-keys.json`, and
    any secret-bearing or runtime-state file **out** of the git diff.

---

## The gaps to close

### A — P0 — Cap inbound request bodies (default-on unbounded memory buffering)

**Evidence.** `app/main.py:534-541`, inside `log_capture_middleware`:

```python
if is_api and request.method == "POST" and request.headers.get("content-type","").startswith("application/json"):
    request.state._body_cache = await request.body()
```

The full body is buffered with **no `Content-Length` check and no byte cap**, and this is **not**
gated by `LOG_BODIES_ENABLED` — only the later *logging* of the cache is (`app/main.py:576`). So
every JSON POST to `/v1/`, `/openai/`, `/claude/`, `/gemini/`, `/v1beta/` buffers unbounded bytes
in the **default** configuration. There is no size-limiting middleware at all (`app/main.py:211`,
`222`, `506`, `518` are CORS, SlowAPI, security-headers, log-capture).

**Required outcome.**
- Add a configurable maximum request-body size, defaulting to a value that cannot break legitimate
  traffic. Justify the default in a comment against real payloads: long agent conversations with
  base64 image parts are normal here, so the cap must be generous (think tens of MB, not 1 MB) —
  but it must be finite. Expose it as a `Settings` field **and** an `.env.example` line (rule 2).
- Enforce it **before** buffering: check `Content-Length` first and reject early; then enforce the
  real byte count while reading, so a lying or absent `Content-Length` (chunked transfer) cannot
  bypass it. Never trust the header alone.
- Reject with a structured `413` JSON error in the same envelope shape the API already uses for
  `400`s (`app/core/api_forwarder.py::_bad_request` is the model). Streaming and non-streaming must
  both fail cleanly — no half-sent SSE, no bare `500`.
- Make the middleware's `_body_cache` read respect the cap, and make body caching itself skippable
  when the body is over the cap even if the request is otherwise acceptable: a large legitimate
  request must still be **proxied**, it just must not be buffered for logging. Proxying must not
  regress for large payloads.
- Apply the same cap discipline to the file-upload path so "upload limits" is genuinely closed, not
  just the JSON path. `app/core/file_upload.py` already caps *remote download* size (VULN-005);
  extend the concept to inbound uploads without breaking `MAX_REMOTE_REDIRECTS` behaviour.
- Count rejections in the bounded diagnostics and expose them via `/admin/status`,
  `/admin/metrics` (as a capped-label counter) and the SLO view if it degrades the service.

**Tests.** New `tests/unit/test_request_size_limits.py`: over-cap `Content-Length` → `413`;
chunked body exceeding cap with no/lying `Content-Length` → `413`; just-under-cap → proxied
normally; large-but-legitimate request still proxied when body logging is off; streaming endpoint
returns a clean error frame, not a truncated 200; upload path enforced; default cap documented.
Plus an evals scenario if the fake upstream can express it.

---

### B — P0 — Scrub secrets from persisted log bodies

**Evidence.** `app/main.py::_add_debug_log` scrubs the **path** (`_scrub_sensitive(path)`,
`app/main.py:454`) but passes `request_body` / `response_body` **raw** into `create_log_record`
(`app/main.py:461-462`). `LogRecord.request` / `LogRecord.response` (`app/core/log_store.py`) are
then written verbatim to `data/logs.json` by `LogStore.flush()` — a bind-mounted persistent volume.
The existing scrubber `_scrub_sensitive` (`app/main.py:246`) already handles `sk-`, `sk-ant-`,
`sk-proj-`, URL-encoded `sk%2D`, and `?key=` / `?api_key=` / `?apikey=` / `?api-key=` / `?token=`
patterns, and the CHANGELOG states "the same scrubber backs the request log" — but it does **not**
reach persisted bodies.

**Required outcome.**
- Redact secret-bearing values in captured request/response bodies **before** they enter a
  `LogRecord`, so nothing unredacted ever reaches `data/logs.json`, the in-memory ring, the
  dashboard, `/admin/logs`, or the request explorer.
- Promote the scrubber to a shared, importable module (it currently lives inside `app/main.py`,
  which nothing can import cleanly). Reuse the **existing** regexes rather than writing new ones —
  `tests/unit/test_scrub_regex_gaps.py` and `test_access_log_scrub.py` pin their behaviour and must
  keep passing.
- Scrub recursively through nested JSON: message arrays, `content` parts, `tool_calls.arguments`
  (which are **strings containing JSON** — a key pasted inside an argument must still be caught),
  headers if ever captured, and string values at any depth. Guard against pathological nesting
  depth and against mutating the caller's objects (work on a copy).
- Preserve the diagnostic usefulness: redaction must not destroy structure, lengths, or field names.
  Keep the existing truncation/length behaviour intact.
- Redact **on the way in**, and additionally verify that an already-persisted `data/logs.json`
  containing a legacy secret is not re-served unredacted by the read paths.
- Keep `LOG_BODIES_ENABLED=false` as the default; this change makes the opt-in path safe, it does
  not turn it on.

**Tests.** New `tests/unit/test_log_body_redaction.py`: an `sk-...` key inside a user message body
is redacted in the persisted record; a key inside `tool_calls.arguments` (JSON-in-string) is
redacted; nested arrays/objects at depth are redacted; structure and field names survive; the
flushed file on disk contains no secret (assert on the written bytes, not just the object);
deep/pathological nesting does not hang or crash; existing scrub tests still pass unchanged.

---

### C — P1 — Per-key / per-tenant quotas and budgets

**Evidence.** `app/core/limiter.py` is slowapi keyed on **client IP only** (`_client_ip_key`), with
a single global `rate_limit_max` / `rate_limit_window`, and `rate_limit_enabled` defaults **False**.
`ApiKeyEntry` (`app/core/api_key_store.py`) has exactly
`id, provider, model, api_key, base_url, label, status, added_at, last_used_at, reasoning_effort`
— **no** quota, budget, tenant, or rate field. Consequence: one holder of the shared `API_KEY` can
exhaust the entire Argon account pool (capacity **2**) for every other caller, with no per-caller
ceiling, no daily budget, and no attribution.

**Required outcome.**
- Introduce an identity concept for callers that is **not** the raw API key. Derive a stable,
  non-reversible caller/tenant id (reuse the existing short one-way hash convention used for
  `account_hash`) so quotas can be attributed and displayed without ever exposing a key.
- Add per-caller limits, all optional and all defaulting to **off/unlimited** so existing
  deployments see zero behaviour change: requests per window, concurrent in-flight requests, and a
  daily budget. Enforce them in one place, layered **after** authentication and **before** the
  expensive Argon call, so a rejected request never consumes an account-pool slot or a
  regeneration-budget token.
- Return a proper `429` with a structured error body and `Retry-After`, matching the API's existing
  error envelope. Distinguish quota exhaustion from the existing IP rate limit in the reason string.
- Make limits configurable per key/tenant and persist them atomically in `data/` via
  `atomic_write_json`. Persisted state must never contain a plaintext key.
- Surface usage and remaining quota in `/admin/status`, `/admin/metrics` (bounded labels only —
  **never** a per-key label value; aggregate or cap per the existing cardinality rule) and the SLO
  view. Add a console panel section consistent with the existing dashboard architecture,
  accessibility rules (focus management, `role="status"` `aria-live="polite"`, `aria-pressed` on
  toggles, `prefers-reduced-motion`) and **all five locales**.
- Keep the existing global IP limiter working exactly as before; this is additive.

**Tests.** New `tests/unit/test_per_key_quotas.py`: limit off by default → no behaviour change;
over-quota → `429` + `Retry-After` + reason; concurrency limit holds a slot correctly and releases
it on error and on client disconnect; daily budget resets on the UTC day boundary; a rejected
request consumed **no** pool slot and **no** regeneration token; persisted quota config round-trips
and contains no plaintext secret; admin endpoint is admin-key protected and 404s on unknown ids;
metrics carry no per-key label. Plus a node render test for the new panel section, following the
pattern in `tests/unit/test_dashboard_accessibility.py` and `test_config_readiness.py`.

---

### D — P1 — Admin audit trail + alerting hooks

**Evidence.** Zero matches for `webhook` / `alert` anywhere in `app/`. `app/routers/admin.py`
contains **no** `logger.info` and no audit record — yet it exposes state-changing operations:
`POST /admin/reload-cookies` (`:53`), `POST /admin/accounts/{id}/status` (`:333`),
`POST /admin/accounts` (`:283`), `POST /admin/argon/reset` (`:226`), `POST /admin/restart` (`:415`),
`POST /admin/update` (`:482`), `POST /admin/cleanup-web-chats` (`:509`),
`POST /admin/debug/tool-json` (`:208`), plus settings-override writes and API-key CRUD. None of
these mutations is recorded, and nothing notifies an operator when the Argon breaker opens, the SLO
goes `degraded`, or the pool saturates — they must be staring at the dashboard.

**Required outcome.**
- Add an append-only, bounded **admin audit trail**: who (admin-key holder, identified without
  echoing the key), what (action + target kind), when (UTC ISO-8601), and the outcome
  (success/failure + status). Record every state-changing admin operation, including failed
  authorisation attempts.
- Never record secret values, cookie contents, account credentials, prompts, or full request
  bodies. Target *kind* and a one-way hash are enough; an account id must not appear in visible
  text (mirror the existing account-card rule).
- Persist it atomically in `data/`, bounded (ring buffer with a configurable capacity), and expose
  it read-only through an admin-key-protected endpoint, integrated with the existing
  `/admin/logs` + console Logs architecture rather than bolted on as a parallel system.
- Add **alerting hooks**: when a condition transitions (breaker open/closed, SLO `ok`→`degraded`,
  pool saturated, regeneration budget exhausted, error-rate threshold crossed), emit an alert event
  into the audit trail and, optionally, deliver it to a configured webhook.
  - Webhook delivery must be **opt-in**, disabled by default, and go through
    `app/utils/net_guard.py` (rule 10) so an operator cannot be talked into pointing it at cloud
    metadata or the loopback admin port. Enforce a timeout, a retry cap, and a circuit breaker so a
    dead webhook can never block or slow the request path. Deliver **asynchronously**, off the
    request path.
  - Payload must be redacted by construction: no secrets, no prompts, no cookies, no account ids.
    Reuse the redaction doctrine from `prometheus_metrics.py` and the scrubber promoted in B.
  - Alert on **transitions**, not on every sample, to avoid flooding. Include dedup/cooldown.
- Console: show the audit trail and alert status, accessible per rule 3, in all five locales.

**Tests.** New `tests/unit/test_admin_audit_trail.py` and `tests/unit/test_alerting.py`: every
state-changing admin route writes exactly one audit entry; a rejected (401/403) attempt is recorded
without leaking the presented key; audit entries contain no secret/cookie/prompt/account-id (assert
on serialised output); persistence is atomic and bounded (evicts oldest); webhook disabled by
default → nothing sent; enabled + unsafe URL (loopback / private / `169.254.169.254`) → blocked by
`net_guard` and recorded as a blocked delivery; webhook failure never propagates to the client
response and never blocks the request path; alerts fire once per transition with cooldown respected.

---

### H — P3 — Resolve the Python 3.14 pin divergence

**Evidence.** `.github/workflows/ci.yml`, job `test-python-3-14`, rewrites the production pin:
`sed 's/^pydantic==.*/pydantic>=2.12/' requirements.txt > /tmp/requirements-314.txt`. So **CI never
tests the shipped pin set on 3.14**, while `requirements.txt` still pins `pydantic==2.11.3`
(whose `pydantic-core 2.33.2` has no cp314 wheel). `AUDIT_SK.md` open point 1 records this as
unresolved, including the undecided question of whether to lift the production pin.

**Required outcome.** Pick one and implement it fully, documenting the decision and its rationale
in `CHANGELOG.md` and in the workflow comments:
- **(preferred)** establish a supported-Python matrix that is honest: state which interpreters the
  shipped pins support, keep `requirements.txt` as the single source of truth for production, and
  make the 3.14 job test a **declared, committed** 3.14-compatible constraint set (e.g. a
  `requirements-py314.txt` or an extras/constraints file) instead of an inline `sed` — so what CI
  tests is reviewable and reproducible, not a shell substitution hidden in a workflow step; **or**
- lift the production `pydantic` pin to a version with cp314 wheels after confirming the full suite
  passes on 3.12 **and** 3.14 with it, and record the compatibility check you performed.
- Either way: the divergence must be **explicit and tested**, not a silent per-job rewrite. Also
  note that `pyproject.toml` sets `target-version = "py312"` for ruff — reconcile that with
  whatever interpreter support you declare.
- The `actions/setup-python@v5` / `actions/checkout@v4` Node-20 deprecation annotations surfaced by
  run `39040306228` are worth fixing in passing (bump the action versions) since you are in the file.

**Tests.** CI-level change; verify locally on the interpreter you have and confirm both gates still
pass. Do not claim a 3.14 result you did not actually run — the prior audit was scrupulous about
this and you must be too.

---

### E — P2 — Redacted replay / fixture library

**Evidence.** No `fixtures/` directory exists anywhere in the repo. `evals/fake_upstream/`
(`frames.py`, `scenarios.py`, `session.py`) contains hand-written scenario frames only;
`evals/baselines/` holds four T0–T3 baseline JSONs. There is no way to capture a real incident as a
redacted, replayable artifact, so debugging a production tool-JSON or streaming failure requires
re-triggering it.

**Required outcome.**
- Add a capture path that records an upstream interaction (frames, timings, status, headers of
  interest) into a **redacted** fixture file, and a replay path that feeds such a fixture into the
  existing fake-upstream harness so a captured incident becomes a deterministic offline test.
- Redaction must run **at capture time** and be fail-closed: if the scrubber cannot prove a value is
  safe, the capture is refused rather than written. Reuse the module promoted in B — do not write a
  third scrubber.
- Fixtures must contain no cookies, no `Authorization`, no account ids, no prompts beyond what an
  operator explicitly chose to include, and must be safe to commit. Provide a size/count bound.
- Wire it into `evals/` so replayed fixtures run in the offline T0 tier and are covered by the
  existing `pytest evals` CI gate. Respect the harness rules already in `evals/tune.py`
  (single-process lock, atomic `evals/state.json`, `--resume`, no implicit hosted-model calls,
  worker contract `gemini-3.8-flash`).
- Never auto-capture in production. Capture must be an explicit, admin-key-protected, opt-in action.

**Tests.** New `tests/unit/test_fixture_capture_replay.py` plus an `evals/` case: capture → redact →
replay round-trip reproduces the same observable behaviour; a fixture containing a planted secret is
refused or scrubbed at capture; replay is deterministic across two runs (stable digest, following
the `stable_digest` convention in `evals/reports/`); capture is admin-protected and off by default.

---

### F — P2 — Configurable routing profiles

**Evidence.** No `profile` concept in `app/config.py`, `app/core/model_mapping.py`, or
`app/core/gem_mapping.py`. Routing is a flat, global set of env switches, so you cannot express
"caller A → Argon with Flash fallback; caller B → Flash only; caller C → pro-only off".

**Required outcome.**
- Introduce named routing **profiles** that bundle the existing routing decisions (Argon vs Flash,
  fallback enabled, pro-only routing, stall-guard/reminder toggles, timeout selection) into one
  selectable unit. A profile must only compose **existing** knobs — do not fork the fallback logic
  (rule 6: `app/core/argon_fallback.py` stays the single implementation).
- Bind a profile to a caller using the identity concept from C. Default profile = today's exact
  behaviour, so nothing changes for existing deployments.
- Persist profiles atomically in `data/`; validate on load; an invalid or unknown profile must fall
  back to the default **loudly** (logged, surfaced in `/admin/config` drift diagnostics alongside
  the existing `unknown_override` / `invalid_override` / `override_not_applied` / `env_overridden`
  classes) rather than silently.
- Expose the effective profile per request in the existing diagnostics (timeline, `/admin/status`,
  metrics with bounded labels) and make it editable in the console settings panel, accessible and
  localised per rules 3.
- Extend `app/core/config_diagnostics.py` so profile drift is visible in `/admin/config`.

**Tests.** New `tests/unit/test_routing_profiles.py`: default profile reproduces current behaviour
byte-for-byte on the existing router tests; a Flash-only profile never calls Argon; profile binding
resolves per caller; unknown profile → default + logged + reported in `/admin/config`; persisted
profiles round-trip atomically; profile selection never changes the serving-model marker semantics
or switches model mid-stream.

---

### G — P2 — Property-based / fuzz coverage for the parser and stream paths

**Evidence.** Zero matches for `hypothesis`, `fuzz`, or `atheris` in `app/`, `tests/`, `evals/`, or
`requirements*.txt`. The tool-JSON repair and SSE parsers are correctness- and security-critical
(bounded state-machine repair, "truncation is never repaired", `strict=False` wire tolerance) but
are covered only by enumerated unit cases.

**Required outcome.**
- Add property-based tests (dev-only dependency, rule 7) for the **invariants**, not for specific
  strings:
  - the repairer never turns a truncated object into a parseable one;
  - repair only ever fires on a complete object and only inside an argument string;
  - any input, however hostile, terminates — no catastrophic backtracking, no unbounded recursion,
    no hang (assert on step/time bounds);
  - the SSE frame parser never emits a malformed frame and never loses the terminal frame;
  - `strict=False` decoding accepts literal control characters in strings without corrupting
    surrounding structure;
  - round-trip properties: parse → repair → parse is idempotent.
- Keep generated tests **deterministic** (fixed seeds) so CI is reproducible, and bound the example
  count so the suite stays fast — the current unit gate runs in ~15 s and the eval gate in ~2m45 s;
  do not blow that up. Put slow/heavy fuzzing behind an opt-in marker excluded from the default run.
- Any real bug these find must be **fixed**, and the failing input reduced and committed as a
  regression case. Do not paper over a finding by narrowing the property.

**Tests.** New `tests/unit/test_parser_properties.py` (and a stream-frame counterpart). Add
`requirements-dev.txt` entry. Ensure `pytest -q` and `ruff check .` stay green and the runtime
increase is documented in your final report.

---

### I — Correction, not implementation: SSRF / file-serving

**Evidence that this is already done** (do **not** re-implement):
- `app/utils/net_guard.py` → `assert_safe_url`, `is_safe_url`, `resolve_redirect_location`,
  rejecting private / loopback / link-local (incl. `169.254.169.254`) / reserved targets.
- Guards every user-controlled outbound URL: `api_forwarder._build_safe_target_url` (OpenAI-compatible
  `:62`, Anthropic `:363`; `_forward_gemini_api` is a `501` stub with no URL),
  `api_keys.py:255` (VULN-004), `file_upload.py` with per-hop redirect re-validation and
  `MAX_REMOTE_REDIRECTS = 5` (VULN-005).
- Path traversal guarded in `image_store.get_image_path` (rejects `/`, `\`, `..`), backing
  `GET /images/{image_id}`.
- Only unguarded outbound call is `admin.py:461`, a **hardcoded** `api.github.com` constant.

**Required outcome.**
- Add a **coverage assertion** test that enumerates outbound-HTTP call sites and fails if a new one
  takes a user-controlled URL without going through `net_guard`. This converts today's correct state
  into an enforced invariant.
- Add a path-traversal test suite for the file-serving surface (`/images/{id}`, the
  `RevalidatingStaticFiles` mount at `app/main.py:753`, `/api-assets` at `:752`), including encoded
  traversal (`%2e%2e%2f`), absolute paths, null bytes, and symlink escape if applicable.
- **Correct `AUDIT_SK.md` open point 5**: remove "SSRF/file-serving hranice" from the unresolved
  list and record that verification found it already implemented, with the file references above.
  Leaving a done item on an open list is how the next audit wastes a cycle.

### J — Trivial hygiene: `evals/state.json` is tracked but declared ignored

**Evidence.** `.gitignore` lists `evals/state.json` under "Local evaluation runs and tuning state",
yet `git ls-files --error-unmatch evals/state.json` succeeds — the file is tracked, so the ignore
rule is inert. Every future `evals/tune.py --resume` run will dirty the diff.

**Required outcome.** Decide deliberately and do exactly one: `git rm --cached evals/state.json`
(keep the ignore rule, stop tracking local tuning state), or remove the misleading `.gitignore`
line (accept that tuning state is versioned). State the choice and its reason in `CHANGELOG.md`.
Leave `evals/reports/20261016T225213Z-30667a.json` and `.md` **tracked** — the audit cites them as
evidence and they are the reproducible artifact, unlike the mutable resume state.

---

## Deliverables and final report

1. Code + tests for A–J, committed incrementally (one logical gap per commit, message stating the
   behavioural fix). Each gap lands with its tests **in the same commit** — no commit that turns a
   gate red.
2. `CHANGELOG.md` `[Unreleased]` entries for every behavioural change.
3. `.env.example` + `app/config.py` updated in lockstep for every new setting (rule 2).
4. `static/app/i18n.js` updated in all five locales for every new console string (rule 3).
5. `AUDIT_SK.md` updated: move closed items out of the open list, correct the SSRF item (I), and
   refresh the Baseline/Final gate table with the **exact** output of your final three gate runs.
6. A final report, in the same evidence-first style as the existing audits, containing:
   - the exact final gate output (unit count, evals count, ruff result) — actual pasted output, not
     a claim;
   - for each gap A–J: what was changed, the files touched, and the test names that prove it;
   - an explicit **RED → GREEN** demonstration for at least A, B, C and D (show the new test failing
     against the pre-change code, then passing);
   - anything you decided **not** to do, with the reason — the existing "Zámerné non-fixy" section is
     the model;
   - any claim you could **not** verify locally (e.g. a real 3.14 run), stated plainly as unverified
     rather than implied as done.

## Definition of done

All three gates green with counts ≥ the baseline (`1104` / `240` / ruff clean); gaps A, B, C, D
closed with tests and RED→GREEN evidence; H decided and implemented; E, F, G closed or, if you make
a principled scoping call to defer one, the deferral is explicitly justified in the final report
rather than silently dropped; SSRF item corrected in the docs and pinned by a coverage test (I);
`evals/state.json` tracking resolved deliberately (J); no existing test weakened; no secret, prompt,
cookie, account id, or runtime-state file in the diff; five-locale i18n parity preserved exactly.
