# Verification of the external "October 17, 2026 snapshot" assessment

**Verifier branch:** `arena/28408e78-gemini2api-argon-proxy`
**Verified against:** working tree at `d963fff` + GitHub API + a real `ruff` run
**Date:** 2026-10-17 UTC
**Method:** same as `AUDIT_VERIFICATION.md` — every falsifiable claim re-derived independently.
Nothing accepted on the assessment's authority.

## Verdict

**The assessment is REAL and unusually accurate.** All 23 falsifiable claims I could test came back
**true**, several to the exact digit. It also correctly hedges the two things it could not verify
("I did not independently validate them against a live Google session"), and its calibration table is
fair.

More importantly: **its weakest-sounding criticism turned out to be its most valuable one.** The
remark that "Ruff is configured with a narrow set of checks, so a green run is not strong assurance"
is not a stylistic quibble — widening the lint set surfaced **three live defects in hot paths**, one
of which is a user-visible correctness bug. Details in §3.

Two of its claims are true but **softer than they read**, and are corrected in §4.

## 1. Claims verified true

### Strengths section

| Claim | Evidence | Result |
|---|---|---|
| Fork of `xwteam/gemini2api`; API surface/dashboard/pool inherited | GitHub API: `fork: true`, `parent: xwteam/gemini2api`, `source: xwteam/gemini2api` | **TRUE** |
| Adapts OpenAI / Anthropic / Gemini formats to Gemini Web `StreamGenerate` | `app/routers/{openai,claude,gemini,responses}.py` → `app/core/gemini_client.py` | **TRUE** |
| Documents a new positional payload / model-header format | `README.md:21,208`; `gemini_client.py:1428` `inner = [None]*81`, `:1431` `inner[2]=DEFAULT_METADATA`, `:1444` `inner[59]=uuid`, `:1448` `inner[80]=2`; `_build_model_header` `:358`; 15-element `x-goog-ext-525005358-jspb` | **TRUE** |
| Streaming parser handles partial frames while tracking JSON strings and escapes | `app/utils/tools.py:40-59` — `in_string` / `escaped` state machine with depth; `:62-118` `_repair_complete_unescaped_quotes`, a stack machine over `(pos, in_string, suspicious, changed, out)` that "preserves valid escape pairs"; `evals/sse.py::parse_sse` + per-protocol validators | **TRUE** |
| Tracks account health and cooldowns | `account_pool.py:177` `cooldown_until`, `:185` `heal_cooldown_until` | **TRUE** |
| Supports waiting for pool capacity | `account_pool.py:466-561` `acquire()` with `deadline = loop.time() + self._acquire_timeout`; queues on release rather than spinning; `acquire_timeout: float = 60.0` (`config.py:62`) | **TRUE** |
| Tries to recover unhealthy accounts | `account_pool.py:453-462` `_try_recover_expired()`, logged "recovered during acquire" | **TRUE** |
| **Session leases so a replaced HTTP session is not closed while requests still use it** | `gemini_client.py:650-653` `_http_leases` / `_http_retired`; `:732` `_lease_http`; `:738` `_release_http` ("close the session if it was retired and this was its last user"); `:753` `_retire_http`; `:784` "Streams in flight keep a lease on old; the last one closes it" | **TRUE — precisely as described** |
| Cancellation treated separately from an upstream account failure | `account_pool.py:402-433` — explicit `cancelled` flag, `except asyncio.CancelledError`, and `asyncio.shield(self._finish_heal(...))` only when `not cancelled` | **TRUE** |
| Docker runs as non-root | `Dockerfile:28` `useradd -m -u 1000 appuser`, `chown -R`; `:10` `gosu`; entrypoint drops privileges | **TRUE** |
| Compose binds loopback by default | `docker-compose.yml:17` `"127.0.0.1:5918:5918"` | **TRUE** |
| Uses TLS-fingerprint impersonation | `app/core/fingerprint/` (`header_builder`, `cookie_jar`, `jitter`, `version_sync`); `gemini_client.py:682,772,781` `AsyncSession(impersonate=target, ...)` | **TRUE** |

### Weaknesses section

| Claim | Evidence | Result |
|---|---|---|
| Tests avoid real network and account-pool startup, so cannot prove live protocol works | `tests/conftest.py` docstring, verbatim: "Fixtures exercise the FastAPI app **WITHOUT** running the lifespan startup, so tests **never touch the network or the account pool**"; background loops disabled (`VERSION_SYNC_ENABLED`, `USAGE_STATS_ENABLED`, `CHAT_CLEANUP_ENABLED`, `RATE_LIMIT_ENABLED` all forced false); `DATA_DIR` redirected to a temp dir | **TRUE** |
| Ruff configured with a narrow set of checks | `pyproject.toml`: `select = ["E9", "F63", "F7", "F82"]`, with an in-file TODO to "expand progressively … (e.g. add F401, I, B, UP)" | **TRUE** — and consequential, see §3 |
| Dependency audit explicitly report-only | `ci.yml` job `dependency-audit`: `continue-on-error: true` at job **and** step level, plus `\|\| true`, named "(report-only)" | **TRUE** |
| An October 17 workflow run succeeded | `gh run view 39040616721` → all 4 jobs ✓. Note the 3.14 job is now titled **"Test (Python 3.14)"** with no "advisory" suffix, confirming the promotion to a hard gate | **TRUE** |
| Recent history includes XSS and secret-handling fixes | Commit `fix(gui): close stored XSS in the logs table, verify login against /admin/verify, harden playground streaming` — "escapeHtml now escapes quotes; before, request-controlled values (model, path) broke out of `title=…` / `data-id=…` attributes in the logs table (stored XSS in the admin console)"; plus multiple secret-redaction commits | **TRUE** |
| 2 stars, no forks | GitHub API: `stargazers_count: 2`, `forks_count: 0` | **TRUE — exact** |
| Non-commercial license | `LICENSE` = **PolyForm Noncommercial License 1.0.0**; GitHub reports `NOASSERTION` (not SPDX-recognised); `README.md:283` "Free for personal, educational, and research use" | **TRUE** |
| Upstream warns cookie-based RE may conflict with Google's ToS | `docs/en/README.md:19` — "uses reverse-engineered browser cookies … **which may violate Google's Terms of Service. Use at your own risk.** The author is not responsible for any account penalties or data loss." Also `:25` (≈2 h cookie expiry), `:498` (CAPTCHA/ban risk) | **TRUE** — and understated, see §5 |
| README names `badmarsh/gemini2api`, repo is `…-argon-proxy` | `README.md:60` `git clone https://github.com/badmarsh/gemini2api.git`; `README.md:19` table header "This Fork (`badmarsh/gemini2api`)" | **TRUE** — but see §4 |
| Timeout documentation doesn't match the code | See §2 | **TRUE — exact** |

## 2. The timeout claim, verified digit by digit

The assessment said: README summarises Argon timeouts as 180 s; code sets a 180 s HTTP-session
timeout but a **360 s** default Argon generation timeout, with separate **150 s** first-chunk and
**240 s** later-chunk waits. Every number is correct:

| Value | Location |
|---|---|
| HTTP session `timeout=180` | `gemini_client.py:781`, `:2030` (also `:685`) |
| `_GENERATE_TIMEOUT_ARGON = 360` | `gemini_client.py:29` |
| `argon_timeout: int = 360` (env/panel override) | `app/config.py:120`; `.env.example:85` `ARGON_TIMEOUT=360` |
| `_ARGON_FIRST_CHUNK_TIMEOUT = 150.0` | `gemini_client.py:35` |
| `_ARGON_STREAM_CHUNK_TIMEOUT = 240.0` | `gemini_client.py:36` |
| Non-Argon generation stays 180 | `gemini_client.py:27,28`, `:1580` |

And the README contradicts it in **four** places:

- `README.md:22` — "180s timeout **across HTTP session and generation**"
- `README.md:221` — mermaid: "POST StreamGenerate (81-element payload, `inner[80]=2`, **180s timeout**)"
- `README.md:237` — heading "**### 2. 180-Second Reasoning Window**"
- `README.md:238` — "Raising default session and **generation** timeouts to 180s"

The root README **never mentions `ARGON_TIMEOUT`** at all — it is documented only in
`docs/en/API.md:1442`. So the one knob that controls the very number the section is about is absent
from the section. (`README.md:169` `timeout=180.0` and `:194` `"request_timeout": 180` are
*client-side SDK* examples and are fine — the assessment did not misattribute them.)

## 3. New: three live defects that the narrow lint gate is hiding

The assessment's point about Ruff is materially correct, not cosmetic. The gate selects
`E9, F63, F7, F82` — which **excludes `F841` (unused variable)**. Running the excluded ruleset over
the tree produced five `F841` hits, three of which are real bugs in hot paths:

### K — P0 — `/v1/responses` returns upstream soft-error phrases as *successful* replies

`GOOGLE_WEB_ERROR_PHRASES` (`gemini_client.py:62-72`) lists Google's soft-error strings, e.g.
*"Sorry, something went wrong. Please try your request again."* The OpenAI router computes
`is_err_text` and **acts on it every time**:

- `openai.py:420` → `:421` `if is_empty_result(result) or is_err_text:` → `:425` retryable error
- `openai.py:666` → `:667` → `:674`
- `openai.py:791` → `:792` → `:799`

The responses router computes it **twice and reads it never**:

- `responses.py:179` — `is_err_text = bool(...)`, then `:182` checks only `if is_blank_answer(result):`
- `responses.py:274` — identical, then `:275` checks only `if is_blank_answer(result):`

`is_blank_answer` (`app/core/fallback.py:78-85`) returns `False` whenever `is_empty_result` is
`False` — and a result whose `text` *is* an error phrase is not empty. So the phrase is delivered to
the client as a normal assistant message with HTTP 200.

`argon_fallback.unusable()` (`argon_fallback.py:99-104`) *does* reject error-phrase text, so this is
masked while Argon→Flash fallback is active. It surfaces whenever fallback cannot rescue it:
`ARGON_FALLBACK_TO_FLASH=false`, a non-Argon model (`should_fallback` → False), or fallback already
exhausted — which is exactly the branch the comment at `responses.py:180-181` claims to handle
("answer with a retryable error instead of an HTTP 200 with empty content"). The previous audit's
CHANGELOG entry scoped that fix to "an answer with **no text**, no images and no reasoning"; the
error-phrase-as-text case on `/v1/responses` was never covered.

**Impact:** a known upstream failure is reported as success, no fallback is attempted, and the client
has no signal to retry. This is the same cross-router asymmetry the earlier refactor claimed to have
eliminated ("one implementation, four routers").

### L — P1 — `saw_frame` is dead: no detection of "bytes arrived, zero frames parsed"

`gemini_client.py:1581-1582` initialises `saw_bytes` and `saw_frame`. `saw_bytes` **is** read at
`:1604` to choose `after_chunk_timeout` vs `first_chunk_timeout`. `saw_frame` is set at `:1631`
inside the parsed-frame loop and **never read**.

**Impact:** the stream cannot distinguish "Google sent bytes that parsed into frames" from "Google
sent bytes and our framing parser matched nothing". If Google changes WRB framing so
`_scan_complete_wrb_frames` stops matching, the request burns the inter-chunk budget and ends with no
text — the silent black-hole failure mode that is this project's central risk. The instrument that
would detect it is computed and discarded.

### M — P1 — image-generation endpoint bypasses model resolution

`openai.py:902` — `model = _resolve_model(req.model or "gemini-pro")`, then `:904` calls
`gemini_client.generate(prompt, req.model or "gemini-pro")` with the **raw** string. `model` is never
used again, not even in the response body.

**Impact:** model aliasing/mapping (`app/core/model_mapping.py`, `MODEL_ALIASES`) is silently
bypassed on this endpoint. `openai.py:278` and `responses.py:13` use `_resolve_model` correctly, so
this call site is the outlier.

### Also real, lower severity

- `F401` unused imports: **37** (7 in `app/core/api_forwarder.py`, 6 in `app/routers/gemini.py`,
  5 in `app/core/fingerprint/__init__.py`, rest across tests).
- `B904` raise-without-`from`-inside-except: **29** — degrades exception chaining, which matters in a
  codebase whose diagnostics depend on error classification.
- `RUF100` unused-`noqa`: **33** — stale suppression comments.
- `I` (isort) 90, `UP` 183, `SIM` 36, `C4` 5 — mostly autofixable cosmetics.

### Critical caveat for anyone widening the gate

`--select RUF` reports **4077** violations, which looks catastrophic and is not: **4024 of them are
`RUF002` (2109) / `RUF003` (1792) / `RUF001` (123)** — "ambiguous unicode character" in
docstrings/comments/strings. This codebase is heavily commented in Chinese and uses full-width
punctuation (`，（）：`), which those rules flag wholesale. Enabling `RUF` without ignoring
`RUF001`/`RUF002`/`RUF003` would bury the 53 genuine findings under 4 thousand false positives. Any
widening must exclude them.

## 4. Two claims that are true but softer than they read

1. **Clone target.** `gh api repos/badmarsh/gemini2api` resolves to
   `full_name: badmarsh/gemini2api-argon-proxy` — the repository was **renamed**, and GitHub serves a
   redirect. So the README's clone URL still works today; it is *stale*, not *wrong*. It is still
   worth fixing: redirects break on re-rename or if the old slug is claimed, and the stale slug
   confuses `gh`, CI, and humans. The assessment's phrasing ("verify the intended clone target") is
   appropriately cautious and not misleading.
2. **"Signs of active iteration, not yet broad validation."** Fair, and the 2-stars/0-forks datum is
   exact. Worth noting for calibration that the repo was **created 2026-10-14** — three days before
   this assessment. Low adoption is a function of age, not necessarily of quality.

## 5. One place the assessment undersells the problem

It says the *upstream* project warns about ToS conflict. True — but the sharper finding is that
**the fork's own root README dropped that warning entirely.**

Counting genuine ToS / account-penalty / ban mentions per README (excluding the false hit on
"`ban`ner.png"):

| File | Real ToS / account-risk warnings |
|---|---:|
| `README.md` (the fork's primary README) | **0** |
| `docs/en/README.md` | 4 |
| `docs/zh-CN/README.md` | 3 |
| `docs/ja/README.md` | 2 |
| `docs/ko/README.md` | 2 |
| `docs/zh-TW/README.md` | 2 |

`README.md:281-284` — the whole "License & Disclaimer" section — says only: "not affiliated with
Google. Gemini is a registered trademark of Google LLC." No ToS-violation warning, no
account-penalty disclaimer, no cookie-expiry caveat (~2 h per `docs/en/README.md:25`), no
CAPTCHA/ban note (`docs/en/README.md:498`).

So a reader who lands on the repository front page — the one document almost everyone reads, and the
one that tells them to paste live Google session cookies into a `.env` — gets **none** of the risk
disclosure that upstream and all five localised docs carry. Given the assessment's own point 3
("cookies should be treated as highly sensitive account credentials"), this is the single most
consequential documentation gap in the repo, and it is a regression introduced by the fork.

## 6. Claims I did not dispute (judgement, not fact)

"Not an ML or inference project"; "advanced power-user/self-hosted tool, not production";
"production readiness low to moderate"; "protocol work is strong"; the calibration table. These are
opinions, they are reasonable, and the underlying facts they rest on all verified.

## 7. Bottom line

The assessment is trustworthy and should be acted on. Of its five stated weaknesses, four are
accurate-but-inert observations about inherent risk (brittle upstream, sensitive credentials, limited
adoption, CI ≠ live proof). The fifth — **narrow lint, so green ≠ assured** — was a live ammunition
dump: it pointed straight at three real defects (K, L, M) that no current gate catches, including a
P0 correctness bug where `/v1/responses` reports an upstream error as success.

Combined with the documentation gaps (O: four stale 180 s claims, `ARGON_TIMEOUT` undocumented in the
root README; P: the dropped ToS/account-risk warning; Q: stale repo slug) and the assurance gaps
(N: lint + dependency gates; R: no live-protocol conformance check), the substantial critique is
fully enumerated in `RESOLUTION_PROMPT_ASSESSMENT.md` as work items **K–R**, ordered to compose with
the **A–J** items in `RESOLUTION_PROMPT.md`.
