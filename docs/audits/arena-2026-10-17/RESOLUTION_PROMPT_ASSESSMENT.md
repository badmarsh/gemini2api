# Resolution prompt — close the critique from the external Oct-17 assessment (items K–R)

> Copy everything below the line into a fresh agent session in this repository.
> It composes with `RESOLUTION_PROMPT.md` (items A–J) in this same directory; the hard constraints
> there apply here unchanged and are restated only where they bind this work specifically.

---

## Mission

An external assessment of this repository was independently verified and found **accurate on every
falsifiable claim** (see `ASSESSMENT_VERIFICATION.md`). Do not re-litigate it. Act on it.

Verification also converted the assessment's sharpest point — "Ruff is configured with a narrow set
of checks, so a green run is not strong assurance" — into **three live defects in hot paths** that no
current gate catches. One is a P0 correctness bug.

Close items **K–R** in this order: **K → L → M → O → P → N → Q → R**.

K, L, M are real bugs: land them first, each with a test that fails before the fix. O and P are
user-facing documentation defects with safety implications. N is the systemic fix that prevents the
next K/L/M. Q is trivial. R is the honest answer to "CI green ≠ Google still works".

---

## Hard constraints

1. **Gates stay green and only grow.** Baseline is `1104 passed, 1 warning` (unit),
   `240 passed` (`pytest evals -q -p no:cacheprovider`), `ruff check .` clean. Run them with
   `API_KEY=sk-test-key` set. If no venv exists, build one from `requirements.txt` +
   `requirements-dev.txt`.
2. **Never weaken, skip, xfail or delete an existing test**, and never loosen an assertion to make a
   gate pass. Specifically keep intact:
   `test_tool_retry.py::test_truncated_json_is_never_repaired`, `test_env_example_sync.py`
   (bidirectional `.env.example` ↔ `Settings` drift guard), `test_argon_system_prompt.py`,
   `test_argon_request_uuid_pairing.py`, `test_security_headers.py`, `test_account_card_rendering.py`.
3. **Bug fixes must not become protocol changes.** Do not alter the 81-element thinking payload,
   `inner[80]=2`, the 15-element companion header, UUID pairing, pro-only routing, pool capacity 2,
   or `SERVER_SIDE_CONTINUATION=false`. Do not switch serving model after content has been streamed.
4. **Preserve the deliberate timeout asymmetry.** `_ARGON_STREAM_CHUNK_TIMEOUT = 240.0` (the
   mid-stream inter-chunk gap) is intentionally **not** shortened — there is an eval guard for the
   `gap200` scenario and a prior regression behind it. Item O is a **documentation** fix; it must not
   touch a single timeout constant.
5. **Fallback logic stays single-sourced** in `app/core/argon_fallback.py`. Item K must be fixed by
   making `/v1/responses` honour the existing shared predicate, **not** by adding a fourth copy of
   the rule.
6. **i18n parity is exact and enforced.** `static/app/i18n.js` has 411 keys in each of `zh-CN`,
   `en-US`, `ja-JP`, `ko-KR`, `zh-TW`, zero drift. Any new console string goes into all five in the
   same commit. Items O/P/Q concern **Markdown docs**, not the console — see their own locale rules.
7. **No new runtime dependencies.** A dev-only dependency for item N/R goes in
   `requirements-dev.txt` only, and must not break the CI 3.14 job's `sed` rewrite of the pydantic
   pin.
8. **Never log, persist or export** secrets, cookies, prompts, `accounts.json` contents,
   `Authorization` headers, or account IDs in visible text. This binds item R hardest: a live probe
   touches real credentials, so its output must be redacted by construction and its credentials must
   come from the environment, never the repo.
9. **Document every change** in `CHANGELOG.md` under `[Unreleased]` in house style, stating the
   behavioural consequence. Update `AUDIT_SK.md`'s open points where you close one.
10. **Stay on your assigned branch.** Keep `.venv*`, `data/`, `.env`, cookies, `accounts.json`,
    `api-keys.json` and any runtime state out of the diff.

---

## K — P0 — `/v1/responses` reports upstream soft-error phrases as successful replies

**Evidence.** `GOOGLE_WEB_ERROR_PHRASES` (`app/core/gemini_client.py:62-72`) enumerates Google's
soft-error strings (e.g. *"Sorry, something went wrong. Please try your request again."*).

The OpenAI router computes `is_err_text` and **acts on it at all three sites**:
`app/routers/openai.py:420`→`:421`→`:425`, `:666`→`:667`→`:674`, `:791`→`:792`→`:799`.

The responses router computes it **twice and never reads it**:
- `app/routers/responses.py:179` → the only guard at `:182` is `if is_blank_answer(result):`
- `app/routers/responses.py:274` → the only guard at `:275` is `if is_blank_answer(result):`

`is_blank_answer` (`app/core/fallback.py:78-85`) returns `False` whenever `is_empty_result` is
`False`, and an error phrase is non-empty text. So the phrase reaches the client as a normal
assistant message with HTTP 200.

`argon_fallback.unusable()` (`app/core/argon_fallback.py:99-104`) already rejects error-phrase text,
which masks the bug while Argon→Flash fallback is active. It surfaces when fallback cannot rescue the
request: `ARGON_FALLBACK_TO_FLASH=false`, a non-Argon model, or fallback already exhausted — precisely
the branch the code comment at `responses.py:180-181` says it handles.

Note the previous fix was scoped to "an answer with **no text**, no images and no reasoning"
(`CHANGELOG.md`, `[Unreleased] → Fixed`); the error-phrase-as-text case was never covered.

**Required outcome.**
- Both `/v1/responses` paths (buffered `:179`, streamed-buffered `:274`) must treat an error-phrase
  reply exactly as `/v1/chat/completions` does: a retryable failure, not content.
  - Buffered → `_error(502, EMPTY_RESPONSE_MESSAGE, ...)` (or a more specific retryable message),
    matching the shape `openai.py:421-425` produces.
  - Streaming → `enc.failed(...)` and stop, matching `openai.py:667-674`.
- Use the **existing shared predicate** — `argon_fallback.is_web_error(result)`
  (`argon_fallback.py:113-116`) already implements exactly this test. Delete the two dead local
  `is_err_text` assignments and call the shared helper. **Do not** introduce a second implementation
  of the rule (constraint 5).
- Audit the remaining routers for the same class of asymmetry — `claude.py` and `gemini.py` must be
  checked for error-phrase handling too. If they have the same gap, fix them the same way, through
  the same shared predicate. Report what you found for each of the four routers explicitly, including
  "already correct".
- Where a fallback *is* available, prefer falling back over erroring, consistent with the OpenAI
  router's ordering. Do not change that precedence.

**Tests.** New `tests/unit/test_responses_web_error_phrases.py`:
- upstream returns a `GOOGLE_WEB_ERROR_PHRASES` string as `text`, fallback disabled → buffered
  `/v1/responses` yields a retryable error, **not** 200-with-content (this test must **fail** against
  the current code — capture that RED output);
- same for the streaming path → `response.failed`, no assistant content;
- non-Argon model → same behaviour;
- a legitimate reply that merely *contains* an error phrase mid-text is **not** misclassified (the
  predicate is `startswith`, pin that boundary);
- parity assertion: for the same upstream result, `/v1/chat/completions` and `/v1/responses` now
  produce the same success/failure classification — a regression test for the asymmetry itself, so it
  cannot silently return;
- `claude.py` / `gemini.py` equivalents for whatever you found.

---

## L — P1 — `saw_frame` is dead: "bytes but zero parsed frames" is undetectable

**Evidence.** `app/core/gemini_client.py:1581-1582` initialises `saw_bytes` and `saw_frame`.
`saw_bytes` **is** read at `:1604` to select `after_chunk_timeout` vs `first_chunk_timeout`.
`saw_frame` is set at `:1631` inside the parsed-frame loop and **never read** (confirmed: the only
three occurrences in the file are `:1582`, `:1631`, and nothing else).

**Why it matters.** The stream cannot distinguish "Google sent bytes that parsed into frames" from
"Google sent bytes and `_scan_complete_wrb_frames` matched nothing". If Google changes WRB framing,
the request burns the inter-chunk budget and ends with no text — the silent black-hole failure mode
this project exists to survive. The instrument that would detect it is computed and thrown away.

**Required outcome.**
- Decide deliberately and document the decision: either **(a)** use `saw_frame` to make the failure
  diagnosable and correctly classified, or **(b)** delete it as genuinely redundant — but only after
  proving `saw_bytes` plus the existing empty-result classification already covers the case.
  **(a) is strongly preferred**; (b) requires a written justification in the commit message.
- If (a): when a stream ends with `saw_bytes and not saw_frame`, that is **protocol drift**, not an
  empty answer. Classify it distinctly — a dedicated error class / failure reason, a WARNING log with
  byte count and elapsed time (never body content), a counter in the bounded diagnostics, and
  visibility in `/admin/status`, `/admin/metrics` (bounded labels only) and the SLO reasons list in
  `app/core/slo.py`. It must also drive the existing fallback decision correctly rather than being
  mistaken for a successful-but-empty reply.
- Do **not** change any timeout constant (constraint 4).

**Tests.** New `tests/unit/test_stream_frame_drift.py`: feed the parser bytes that never form a
complete WRB frame → assert the distinct drift classification, the counter increment, the WARNING
(without body content in the log record), and that fallback/error propagation treats it as a failure.
Add an `evals/fake_upstream/` scenario expressing "bytes, no parseable frames" so the offline T0 tier
covers it, following the existing scenario/frames conventions.

---

## M — P1 — image-generation endpoint bypasses model resolution

**Evidence.** `app/routers/openai.py:902` computes `model = _resolve_model(req.model or "gemini-pro")`,
then `:904` calls `gemini_client.generate(prompt, req.model or "gemini-pro")` — passing the **raw**
string. `model` is never used again, not even in the response body. `openai.py:278` and
`responses.py:13` use `_resolve_model` correctly, so this site is the outlier.

**Required outcome.**
- Pass the **resolved** model to `generate()`, and use it consistently for the response `model` field
  and any logging/diagnostics on that path.
- Confirm the served-model marker semantics are unaffected (the serving model must still be reported
  accurately, and must never change mid-stream).
- Sweep for the same pattern elsewhere: any call site that resolves a model and then passes the
  unresolved original. Report each.

**Tests.** New `tests/unit/test_image_endpoint_model_resolution.py`: a request using a configured
alias/mapping reaches the client with the resolved model (assert on what `generate` was called with);
the response echoes the resolved model; an unmapped model behaves as before. Must fail RED against
current code.

---

## O — P1 — Root README timeout documentation contradicts the code

**Evidence.** Code: session `timeout=180` (`gemini_client.py:781,2030`), `_GENERATE_TIMEOUT_ARGON=360`
(`:29`), `argon_timeout: int = 360` (`app/config.py:120`, `.env.example:85`),
`_ARGON_FIRST_CHUNK_TIMEOUT=150.0` (`:35`), `_ARGON_STREAM_CHUNK_TIMEOUT=240.0` (`:36`),
non-Argon generation 180 (`:27,28,:1580`).

README contradicts it four times: `:22` "180s timeout **across HTTP session and generation**";
`:221` mermaid "…**180s timeout**"; `:237` heading "**### 2. 180-Second Reasoning Window**";
`:238` "Raising default session and **generation** timeouts to 180s". The root README never mentions
`ARGON_TIMEOUT` (documented only at `docs/en/API.md:1442`).

**Required outcome.**
- Correct all four places to describe the real, layered timeout model: HTTP session 180 s; Argon
  generation 360 s (configurable via `ARGON_TIMEOUT`, `0` = built-in default); streaming first-chunk
  150 s; streaming inter-chunk gap 240 s; non-Argon generation 180 s.
- Rename/rewrite the "180-Second Reasoning Window" section so the heading is true, and explain *why*
  first-chunk and mid-stream differ (before the first byte there is nothing to lose; cutting a
  mid-stream gap truncates a reply and corrupts tool-call JSON). That rationale already exists in the
  code comments and prior audits — surface it in the README rather than inventing new prose.
- Document `ARGON_TIMEOUT` and `ARGON_STALL_BREAKER_SECONDS` in the root README's configuration
  discussion, consistent with `docs/en/API.md`.
- **Documentation only.** Change no constant, no default, no code path (constraint 4).
- Add a drift guard so this cannot rot again: a test that asserts the timeout numbers stated in
  `README.md` match the constants in `gemini_client.py` / `app/config.py`. Follow the existing
  drift-guard pattern in `tests/unit/test_env_example_sync.py`.

**Tests.** New `tests/unit/test_readme_timeout_sync.py` — parse the documented numbers out of
`README.md` and compare against the imported constants. Must fail RED against the current README.

---

## P — P1 — Root README dropped upstream's ToS / account-risk disclosure

**Evidence.** Genuine ToS / account-penalty / ban warnings per README (the single case-insensitive
"ban" hit in the root README is the substring in "`ban`ner.png"):

| File | Real warnings |
|---|---:|
| `README.md` (fork's primary README) | **0** |
| `docs/en/README.md` | 4 |
| `docs/zh-CN/README.md` | 3 |
| `docs/ja/README.md` | 2 |
| `docs/ko/README.md` | 2 |
| `docs/zh-TW/README.md` | 2 |

`README.md:281-284`, the entire "License & Disclaimer" section, says only "not affiliated with
Google. Gemini is a registered trademark of Google LLC." Missing, versus upstream and every localised
doc: the ToS-violation warning and use-at-your-own-risk / no-liability-for-account-penalties
disclaimer (`docs/en/README.md:19`), the ≈2-hour cookie-expiry caveat under Google risk control
(`docs/en/README.md:25,236`), and the CAPTCHA / temporary-ban warning about request frequency
(`docs/en/README.md:498`).

This matters disproportionately: the root README is the front page, it is the document that instructs
a reader to paste **live Google session cookies** into `.env`, and it is the only one of the six that
carries no risk disclosure. It is a regression introduced by the fork.

**Required outcome.**
- Restore a clear, prominent risk section to `README.md`, placed **before** the quickstart/cookie
  instructions, not buried at the bottom — a reader must see it before they paste credentials.
  Cover: unofficial reverse-engineered protocol; possible conflict with Google's ToS; cookies are
  account credentials, not API keys; account penalties/bans are possible and the author is not
  liable; cookie sessions expire quickly (~2 h) under risk control; high request frequency can
  trigger CAPTCHA or temporary bans; keep the service bound to loopback (which `docker-compose.yml:17`
  already does by default — say so, and warn explicitly about what changes if a user rebinds to
  `0.0.0.0`).
- Base the wording on the existing upstream text so the five localised docs and the root README say
  the same thing. Do not invent new legal claims; the assessment was careful to frame this as a risk
  observation, not a legal conclusion, and the docs must be too.
- Keep the PolyForm Noncommercial terms accurate and visible (they already are at `README.md:283`).
- If the localised `docs/*/README.md` files are missing any of these points relative to
  `docs/en/README.md`, bring them into parity. All five locales must carry the same disclosures.

**Tests.** New `tests/unit/test_readme_risk_disclosure.py` — assert the root README contains the
required disclosure elements (ToS, account penalty, cookie sensitivity, loopback guidance), and that
all five localised READMEs carry them too. Must fail RED against the current root README.

---

## N — P1 — Widen the lint gate and make the dependency gate honest

**Evidence.** `pyproject.toml`: `select = ["E9", "F63", "F7", "F82"]` — which excludes `F841`, the
rule that would have caught K, L and M on sight. The file's own comment already says "Expand
progressively in follow-ups (e.g. add `F401`, `I`, `B`, `UP`)". `ci.yml` `dependency-audit` is
`continue-on-error: true` at both job and step level plus `|| true`.

Measured violation counts over the current tree (`ruff 0.8.6`, the pinned version):

| Ruleset | Violations | Note |
|---|---:|---|
| `F401` unused-import | 37 | 7 in `api_forwarder.py`, 6 in `routers/gemini.py`, 5 in `fingerprint/__init__.py` |
| `F841` unused-variable | 5 | **3 are the live bugs K/L/M** |
| `F541` empty f-string | 1 | autofixable |
| `B904` raise-without-from | 29 | degrades exception chaining in an error-classification-heavy codebase |
| `B007` / `B017` / `B905` | 5 | mostly autofixable |
| `RUF100` unused-noqa | 33 | stale suppressions |
| `I` (isort) | 90 | autofixable |
| `UP` | 183 | autofixable |
| `SIM` | 36 | |
| `C4` | 5 | |
| `RUF` total | **4077** | **see caveat — 4024 are false positives** |

**Critical caveat.** `RUF002` (2109) + `RUF003` (1792) + `RUF001` (123) = **4024** "ambiguous unicode
character" findings in docstrings/comments/strings. This codebase is extensively commented in Chinese
and uses full-width punctuation (`，（）：`), which those rules flag wholesale. **Never enable `RUF`
without ignoring `RUF001`, `RUF002`, `RUF003`** — doing so buries ~53 genuine findings under 4 000
false positives and the gate becomes meaningless.

**Required outcome.**
- Land K, L and M **first**, so `F841` is clean before it is enabled.
- Widen `select` in stages, each stage its own commit with the full suite green:
  1. `F` (all pyflakes) — the highest-value step; it is the rule family that catches discarded-value
     bugs like K/L/M and like the previously-fixed discarded-UUID bug (`encoded, _ = …`).
  2. `B` (bugbear) — fix the 29 `B904`s properly with `raise … from e` so exception chains survive;
     do not blanket-`noqa` them.
  3. `RUF` **with `RUF001`/`RUF002`/`RUF003` in `ignore`** — clears the 33 stale `noqa`s.
  4. `I`, `UP`, `SIM`, `C4` — autofix, then review the diff for behaviour changes (`UP` can rewrite
     semantics in rare cases; read every hunk).
- Use `ruff check --fix` for the autofixable sets, but **review the resulting diff** — an autofix that
  removes an "unused" import which was actually a re-export will break runtime. `fingerprint/__init__.py`
  (5 × `F401`) is exactly that risk: check whether those are deliberate re-exports and, if so, add them
  to `__all__` or mark them explicitly rather than deleting them.
- Record the final `select`/`ignore` in `pyproject.toml` with a comment explaining the `RUF001-003`
  exclusion, so nobody "fixes" it later.
- **Dependency gate:** run `pip-audit --requirement requirements.txt` and triage every finding. Then
  either (a) fix/upgrade until clean and promote the job to a hard gate by removing
  `continue-on-error`, or (b) if a CVE has no available fix, keep it report-only but commit an
  explicit, dated baseline file listing the accepted finding(s), the reason, and a review date — so
  "report-only" becomes a recorded decision instead of a permanently ignored signal. Note the
  `starlette==1.8.0` pin already carries a CVE comment in `requirements.txt`; keep that convention.
- The CI 3.14 job's `sed 's/^pydantic==.*/pydantic>=2.12/'` must keep working after your changes.

**Tests.** No new tests required for the lint widening itself, but the gates must pass at every stage
and the final counts must be reported. If you add a `pip-audit` baseline file, add a test that the
baseline matches the declared format and contains no secret material.

---

## Q — P2 — Stale repository slug in the README

**Evidence.** `README.md:60` — `git clone https://github.com/badmarsh/gemini2api.git`;
`README.md:19` table header — "This Fork (`badmarsh/gemini2api`)". The repository is
`badmarsh/gemini2api-argon-proxy`. `gh api repos/badmarsh/gemini2api` resolves to
`full_name: badmarsh/gemini2api-argon-proxy`, i.e. this was a **rename** and GitHub currently serves a
redirect — so the URL works today but is stale.

**Required outcome.**
- Update the clone URL and the fork-name references to the real slug. Sweep the whole repo for the
  stale slug (`README.md`, `docs/**`, `SPONSORS.md`, `docker-compose.yml`, `Dockerfile`,
  `.github/workflows/*`, `CHANGELOG.md` historical entries excluded) and fix live references.
- **Exception:** `app/routers/admin.py:461` calls
  `https://api.github.com/repos/badmarsh/gemini2api/releases/latest` for `/admin/check-update`.
  Decide deliberately whether the update check should track this fork or upstream `xwteam/gemini2api`,
  state the choice in a comment, and make the URL correct for that choice. A version check pointed at
  a renamed-away slug is a silent-no-op risk. Do **not** add an SSRF guard here — the URL is a
  hardcoded constant with no user input; note that in the comment so nobody "fixes" it later.
- Leave historical `CHANGELOG.md` entries and the archived `docs/audits/**` reports untouched; they
  are records of what was true at the time.

**Tests.** Extend the existing docs-drift test approach: assert no live (non-audit, non-changelog)
file references the stale slug, with an explicit allowlist for the historical paths.

---

## R — P2 — Make "does the live protocol still work?" answerable

**Evidence.** `tests/conftest.py` states the suite runs "WITHOUT running the lifespan startup, so
tests never touch the network or the account pool". The offline eval suite uses a fake upstream. So
all 1104 + 240 tests can be green while Google's live wire protocol is already broken — the exact
brittleness the assessment identifies as the project's core risk, and the same limitation
`AUDIT_SK.md` open point 2 records (T1/T2 live spot checks never run).

**Required outcome.** Build the missing rung between "offline green" and "user reports it broke":

- **Opt-in, credentialed live smoke probe** — a separately invoked entry point (never part of
  `pytest`'s default collection, never part of CI's unauthenticated jobs) that performs a minimal
  real request and asserts the protocol invariants that actually break:
  the `bl` / `f.sid` page tokens can still be minted (without them Google black-holes the POST);
  the 81-element payload is still accepted; at least one parseable WRB frame comes back; the
  companion header UUID is still paired; a first chunk arrives within the first-chunk budget.
- **Credentials from the environment only.** Never read, write, copy, log or commit cookies,
  `.env`, `accounts.json` or `api-keys.json`. Refuse to run if credentials are absent, with a clear
  message — do not guess a URL or fall back to a fixture silently.
- **Redacted-by-construction output.** The probe writes a pass/fail report with timings and protocol
  markers only: no prompt text, no response body, no cookies, no account IDs. Reuse the redaction
  doctrine already written in `app/core/prometheus_metrics.py` and the scrubber promoted by item B of
  `RESOLUTION_PROMPT.md` if that has landed; otherwise reuse `_scrub_sensitive`. Fail closed: if a
  value cannot be proven safe, do not write it.
- **Loud drift detection.** Reuse item L's `saw_frame` classification so the probe distinguishes
  "protocol changed" from "account unhealthy" from "rate limited" from "network down" — four different
  operator responses. Emit a distinct, documented exit status per class so it can be wired to cron or
  to item D's alerting later.
- **Respect the existing harness rules** in `evals/tune.py`: single-process lock, atomic state writes,
  a hard cap on real Argon requests, abort after consecutive upstream errors, and **no implicit
  hosted-model calls** (the worker contract stays `gemini-3.8-flash`, proposal-only).
- **Honesty requirement:** you almost certainly cannot run this probe in a sandbox with no Google
  credentials. That is expected. Deliver it as working, reviewed, offline-tested code (the harness
  itself must be unit-tested against the fake upstream), and state plainly in your final report that
  the live execution is **unverified** because no credentials were available. Do not imply a green
  live run you did not perform — the prior audits were scrupulous about this and you must be too.

**Tests.** New `tests/unit/test_live_probe_harness.py`: the probe refuses to run without credentials;
its report contains no prompt/body/cookie/account-id when fed a synthetic result; each drift class
maps to its documented exit status; the request cap and abort-after-N logic hold; the harness passes
against `evals/fake_upstream/` without any real network access.

---

## Deliverables and final report

1. Code + tests for K–R, committed incrementally — one logical item per commit, each with its tests
   in the **same** commit, so no commit turns a gate red. Land K, L, M before enabling `F841` in N.
2. `CHANGELOG.md` `[Unreleased]` entries for every behavioural change (K, L, M, N) and every
   documentation change (O, P, Q).
3. For K, L, M, O and P: an explicit **RED → GREEN** demonstration — the new test failing against the
   pre-change code, then passing. Paste the real output.
4. `AUDIT_SK.md` updated: close open point 2 partially via R (the harness now exists; live execution
   still credential-gated), and record O/P/Q as documentation fixes.
5. A final report in the evidence-first house style containing:
   - exact final gate output (unit count, evals count, ruff result) — pasted, not claimed;
   - the final `ruff` `select`/`ignore` set and the before/after violation counts per stage of N;
   - the `pip-audit` triage result and which of (a) hard gate / (b) documented baseline you chose, with reasons;
   - per item K–R: what changed, files touched, and the test names that prove it;
   - the four-router parity audit result from K, including any router you found already correct;
   - anything you decided **not** to do, with the reason (model: `AUDIT_SK.md`'s "Zámerné non-fixy");
   - every claim you could **not** verify locally — above all R's live execution — stated plainly as
     unverified.

## Definition of done

All three gates green with counts ≥ baseline (`1104` / `240` / ruff clean, now under a **wider** ruff
`select`); K, L, M fixed with RED→GREEN evidence and no fourth copy of the fallback rule; the
`/v1/responses` ↔ `/v1/chat/completions` classification parity pinned by a regression test; root README
timeout text matches the constants and is pinned by a drift test; root README carries the full risk
disclosure before the cookie instructions, with all five localised docs at parity; lint gate widened
with `RUF001-003` explicitly ignored and the reason recorded; dependency gate either hard or backed by
a committed dated baseline; stale slug fixed in live references with the `check-update` URL decision
documented; live probe harness merged, offline-tested, credential-gated, redacted by construction, and
its live execution honestly reported as unverified; no existing test weakened; no secret, cookie,
prompt, account id or runtime-state file in the diff.
