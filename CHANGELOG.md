# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Security
- `/docs`, `/redoc` and `/openapi.json` are now disabled by default (404). FastAPI serves them without checking the API key, and they listed every route including the admin API. Set `DOCS_ENABLED=true` to bring them back; they stay unauthenticated, so use it for local development only.

### Fixed
- `.env` robustness on Windows: a UTF-8 BOM at the start of the file (added by Notepad or PowerShell 5.1) no longer breaks the first key, and unknown keys (for example those used by the refresher, which shares the file) no longer crash startup. Startup logs one warning listing the unknown key names (never values) so typos are still noticed.
- The Gemini-to-third-party fallback toggle (`PATCH /admin/api-keys/fallback`) is now persisted in `data/settings-overrides.json`, like the settings panel fields. Under Docker Compose `env_file` the real environment variable beat the container-local `.env`, so the toggle silently reverted after a restart. The write is all-or-nothing: if any layer fails, memory and files are restored.

---

## [1.7.1] - 2026-10-14 (badmarsh/gemini2api v1.7.1)

> Security patch for the static-extension authentication bypass, failover and stream reliability fixes, and secret-handling and console hardening.

### Security
- **Static-extension authentication bypass (present in v1.7.0)**: the public-path check matched any path ending in a static extension regardless of method or prefix, so requests such as `DELETE /admin/accounts/test.png` skipped `verify_api_key` and `verify_admin_key`. The exemption now applies only to `GET`/`HEAD` and never under `/admin`, `/v1`, `/v1beta`, `/openai`, `/claude` or `/gemini`; the `/admin` prefix check is segment-based and `verify_admin_key` no longer exempts any path. Regression tests added.
- Removed `add_cookies.py` (sent no auth header) and `add_account_cookies.py` (printed full cookies); both relied on an undeclared `requests` dependency.
- Admin account status now masks `psidts` in addition to `psid`, and API key listings use the shared `mask_secret` helper.

### Fixed
- **Session lifecycle**: the shared HTTP session is leased per in-flight request; a session retired by a cookie reload or fingerprint rotation is closed by its last user instead of underneath running streams.
- **Timeout failover**: a hung request (builtin/asyncio timeout or libcurl code 28) fails over to the next account immediately instead of consuming further same-account retries. Retry exhaustion raises a dedicated `RetriesExhaustedError`.
- **Stall classification** matches exception types and codes only; free-text matching on error messages could misclassify ordinary 4xx responses.
- **Google error-template detection** is limited to a known sentence plus at most a short single-line tail, so genuine answers that begin with the same words are no longer discarded.
- **Cookie refresher**: reports an expired session when redirected to the Google login page, isolates per-account failures, and no longer passes `--single-process` / `--no-zygote` to the browser.
- `ROTATION_STRATEGY` accepts `round_robin`, `Round-Robin` and padded values, so the README value no longer raises at startup.
- `/admin/check-update` queries `badmarsh/gemini2api` instead of upstream.

### Added
- `WARNING` log with emitted and unparsed buffer sizes when the 120s chunk timeout ends a reply early (behavior unchanged).
- Web console: domain services under `static/app/services/` (account, chat, telemetry, storage, validation), centralized constants, per-section error boundaries with retry, `AbortController` timeouts, and 200-item ring buffers for logs and playground history.
- `safeStorage` wrapper with in-memory fallback and quota protection. The stored console token is obfuscated (XOR mask plus Base64), which is not encryption.
- Unit and integration tests for masking, account pool state transitions, model alias translation, thinking-block extraction, auth bypass, failover classification and in-flight stream reloads.

### Changed
- `mask_secret` now shows the first 6 and last 4 characters (previously 4 and 4); values of 8 characters or fewer are fully masked and values of 9-10 characters show 2 and 2.
- Admin endpoint messages (`/admin/restart`, web-chat cleanup) are in English.
- Bump `fastapi` 0.120.4 -> 0.143.0 and `starlette` 0.49.1 -> 1.8.0 (pinned together; the Starlette >= 0.49.1 floor for CVE-2025-62727 is preserved). Adds `opentelemetry-api` as a transitive dependency of FastAPI.
- Tests are isolated from the real `data/` directory via `DATA_DIR`; the saturated-pool test tolerates Windows timer granularity.
- CI: the `pip-audit` step is report-only and no longer produces red annotations.
- Docs: CHANGELOG, UI templates, stylesheets and scripts localized to English; clone instructions point to the `badmarsh/gemini2api` fork.

---

## [1.7.0] - 2026-10-14 (badmarsh/gemini2api v1.7)

> Gemini 4 Argon Protocol Support, Autonomous Agent Hardening, Robust Simulated Tool Calling, and Default English Web Console.

### Fixed
- 🧰 **Simulated Tool Call Envelope Leak & Non-Strict JSON Parsing**:
  - Added `strict=False` in `json.loads` within `app/utils/tools.py` (`_try_parse` and `_normalize_tool_calls`), tolerating unescaped control characters (ASCII newlines `\n` and tabs `\t`) generated by models in string values.
  - Implemented `_unwrap_text_status_envelope` regex fallback to ensure model replies wrapped in `{"status": "text", "content": "..."}` envelopes never leak raw JSON syntax to Codex Desktop or OpenCode clients even if trailing syntax is broken.
  - Added action-forcing instruction in default tool-calling system prompts to deter models from hedging with plain text when actions or tool invocations are requested.
- 🌐 **Web Console & Documentation Localization (Zero Chinese Defaults)**:
  - Switched default UI language in `static/app/i18n.js` from `zh-CN` to `en-US`.
  - Fully translated `static/login.html`, `static/index.html`, `static/components/` (header, sidebar, dashboard, accounts, api-keys, config, gems, logs, playground, settings, usage-stats), and `SPONSORS.md` to English.
  - Routed dashboard QR cards and image assets locally through `/api-assets` to avoid pulling upstream Chinese cards from raw GitHub.

---

## [1.6.41-argon] - 2026-10-14 (badmarsh/gemini2api fork)

> Dedicated hardened release for **Gemini 4 Argon** mandatory thinking protocols, deep reasoning timeouts, Google Web error template interception, and multi-turn coding agent context retention.

### Added
- 🧠 **Gemini 4 Argon Protocol Adaptation (StreamGenerate 81-element payload)**: Supports the October 2026 Google Gemini Web UI protocol shift. Implements the 81-element thinking payload structure, `f.sid`/`bl` tracking parameters, and 15-element companion headers. Enforces active internal thinking (`inner[80]=2`) on Argon models, eliminating the silent black-hole stall from legacy 4-element payloads.
- 🛡️ **Google Web Error Template Interception & Seamless Failover**: When the Gemini Web interface returns boilerplate errors (such as `"I seem to be encountering an error. Can I try something else for you?"` or `"Sorry, something went wrong"`), they are intercepted as `GoogleWebError`, putting the account into cooldown and automatically failing over to the next healthy account.
- ⏱️ **Stall Detection & Self-Healing (`_is_stall`)**: Introduced connection hang and first-byte timeout detection in `account_pool.py`, quickly releasing and cooling down stalled accounts to prevent blocking subsequent requests.
- 🍪 **Cookie Jar Whitelisting (Eliminating 168-byte Stream Stalls)**: `cookie_jar.py` strictly whitelists `__Secure-1PSID` and `__Secure-1PSIDTS`, filtering out volatile anti-abuse cookies (`NID`, `SIDCC`, `AEC`, `OTZ`) and completely preventing cross-process volatile cookie replay stalls.
- 🚦 **Staggered Startup Rate-Limit Protection**: Adds a 4-second delay between account initializations during container boot, preventing simultaneous `/app` and `batchexecute` bursts from triggering Google's `identity.hfcr` rate limit on shared egress IPs.
- 🛠️ **OpenAI API `developer` Role Mapping**: Messages using the `developer` role (Codex, OpenCode, o1/o3-mini standards) are mapped to `System: <content>`.

### Changed
- ⌛ **180s Extended Reasoning Timeout**: `_GENERATE_TIMEOUT_DEFAULT` and the underlying `AsyncSession` timeout increased from 60s to 180s, accommodating Gemini 4 Argon deep reasoning passes (typically 30–75s) without triggering client or gateway disconnections.
- 💬 **Full Coding Agent Multi-Turn Context Retention**: When external coding agents (Codex, OpenCode, Antigravity, OpenAI SDK) provide full conversation history, the context is preserved in full and stale `gemini_conv_id` references are cleared, preventing cross-account session dropouts.
- 🔑 **Admin Cookie Reset Auto-Activation**: Updating credentials via `/admin/accounts/{id}/cookies` automatically resets account status to `ACTIVE`, clears failure counters, and persists changes immediately.
- 📦 **Dependency Upgrade**: Upgraded `curl_cffi` to `0.16.3`.

---

## [1.6.40] - 2026-09-12

### Fixed
- 🔍 **Playground Error Visibility**: Stream error frames now properly display `Gateway Error (503): <reason>` instead of masking failures as "No response content". Error messages are sanitized to prevent stored XSS.
- 🪵 **Cookie Rotation Lifecycle Logging**: Explicit startup logs show whether the auto-refresh loop has started (`Auto-refresh loop started / NOT started (reason)`).

### Added
- 🩺 **Accurate Account Health Diagnostics**: Account cards export `is_healthy`, `consecutive_failures`, `last_success_at`, and `last_error` (sanitized exception type and HTTP code), ensuring account health is clear even when marked active.
- 📉 **Consecutive Empty Response Tracking**: Consecutive empty responses are tracked separately and trigger error increments only after 3 consecutive occurrences, preventing false-positive account degradation.

---

## [1.6.39] - 2026-09-01

### Changed
- ⚠️ **Settings Override Precedence**: Runtime settings modified via the Web Console are stored in `data/settings-overrides.json` and take precedence over environment variables across restarts.

### Added
- ⚙️ **`LOG_BODIES_ENABLED` Console Toggle**: Added request/response body logging switch directly to the settings dashboard.

### Fixed
- 🧊 **Static Assets Cache Validation**: Added `Cache-Control: no-cache` with ETags to `.js`, `.css`, `.html`, and `.json` static assets to prevent stale script caching.
- 🔢 **Decimal Float Preservations**: Fixed float truncation in cleanup intervals and duration inputs.
- 🛡️ **Invalid Float Rejection**: Rejects `NaN` and `±Infinity` from entering configuration fields.
- 💾 **All-or-Nothing Settings Persistence**: Transactional rollbacks across in-memory state, overrides file, and `.env`.

---

## [1.6.38] - 2026-09-01

### Fixed
- 🍪 **Multi-Domain Cookie Conflict**: Resolved `CookieConflict` exceptions when Google redirects across country domains (`.google.com.hk`, `.com.tw`, `.co.jp`) by reading individual cookie objects and prioritizing `*.google.com`.
- 🔁 **Automatic Retry for Malformed Tool Call JSON**: Automatically retries generation once with live SSE keepalives if simulated tool calling outputs malformed JSON.
- 🛡️ Non-blocking response logging: Logging failures no longer interrupt client responses.

### Added
- 🔍 **Optional Full Request/Response Body Logging (`LOG_BODIES_ENABLED`)**: Kept in-memory only (max 4KB, credentials scrubbed) for debugging tool interactions.

---

## [1.6.37] - 2026-08-30

### Fixed
- 🩺 **Account Pool Lockup Resolution**: Corrected pool selection logic where unhealthy accounts with expired sessions previously caused false `529 All accounts busy` errors. Added non-blocking self-healing reloads.
- 🛑 **Client Disconnect Accounting**: Early client disconnects no longer increment account failure counters or mark accounts as `EXPIRED`.
- 🔄 **Safe Cookie Reload Rollbacks**: Failed cookie reloads roll back cleanly without leaving orphan states.

---

## [1.6.36] - 2026-08-28

### Fixed
- 🚨 **OpenAI Streaming Error Frames**: Emits standard `data: {"error": {...}}` frames instead of disguised successful stops on upstream failures.
- 🚦 Standardized 4xx HTTP error mapping to `invalid_request_error`.
- 📐 Synchronized citation fields between streaming and non-streaming Anthropic endpoints.
- 🛡️ Tolerates non-string message contents without raising `TypeError`.

---

## [1.6.35] - 2026-08-28

### Fixed
- 🛠️ **Image Intent Detection Scope**: Scoped image intent detection strictly to the final user message with boundary matching and idiom exclusions (`draw a conclusion`, `draw a line`), preventing unintended suppression of tools in agent turns.
- 🚨 Standardized `event: error` frame emission on Anthropic streaming errors.
- 🚦 Standardized error mapping via `classify_error` (including 529 `overloaded_error` with `Retry-After`).
- 🌊 Added keepalive heartbeats to `/v1/responses` and native Gemini buffered streaming paths.
- 🔌 Supported camelCase parameters (`systemInstruction`, `generationConfig`, `inlineData`) from official Gemini SDKs.
- 🧰 Retained OpenAI `tool_calls` during flattening and mapped Anthropic tool roles for third-party forwarders.
- 📐 Aligned Anthropic responses with official schemas (Markdown images, token usage fields).

---

## [1.6.34] - 2026-08-28

### Fixed
- 🧹 Anthropic protocol compliance: Added `stop_sequence` field support and gracefully handled null values in tool calling blocks.
- 🧪 Added unit test coverage for tool formatting and streaming cancellations.

---

## [1.6.33] - 2026-08-28

### Fixed
- 🔌 **Claude Code Compatibility**:
  - Supported array-of-blocks format with `cache_control` in `/v1/messages` system prompts.
  - Retained `tool_use` and `tool_result` content blocks in conversation turns.
  - Standardized Anthropic SSE events using `event:` and `data:` frames.
- 🌊 Added SSE keepalive heartbeats to Claude buffered streaming routes.

---

## [1.6.32] - 2026-08-14

### Fixed
- 🧠 **Incremental Streaming of Thoughts**: Streams reasoning thoughts frame-by-frame as `reasoning_content` before final text answers on `/v1/chat/completions`.

---

## [1.6.31] - 2026-08-14

### Fixed
- 🌊 **Streaming Keepalive Heartbeats**: Added periodic keepalive pings (`: ping` and NDJSON blank lines) across all streaming endpoints to prevent gateway timeouts during silent generation phases.
- 📝 Improved network error diagnostics in the Playground.

---

## [1.6.30] - 2026-08-14

### Added
- 🧠 **Playground Thinking Toggle**: Added toggle to enable extended reasoning in Playground sessions.

---

## [1.6.29] - 2026-08-14

### Added
- 🧠 **Extended Thinking (`reasoning_effort`)**: Supported reasoning effort parameters mapped to `reasoning_content` for Gemini models.

---

## [1.6.28] - 2026-07-30

### Added
- 🆕 **`gemini-flash-lite` Model Support**: Added dynamic routing for Gemini 3.5 Flash-Lite models.

---

## [1.6.27] - 2026-07-25

### Changed
- 🎨 **Branding Assets**: Optimized console brand logo and browser favicons.

---

## [1.6.26] - 2026-07-07

### Added
- 🔌 **OpenAI Responses API (`/v1/responses`)**: Supported OpenAI Responses protocol for compatibility with Codex CLI and next-generation agents.

---

## [1.6.25] - 2026-06-23

### Added
- 🎚️ **Third-Party Failover Switch**: Added interactive toggle in the API Keys console for enabling or disabling Gemini-to-third-party failovers.

---

## [1.6.24] - 2026-06-22

### Added
- 🧩 **Custom Gems as Models**: Manage, create, and expose Google Gemini Gems as OpenAI-compatible model names.

---

## [1.6.23] - 2026-06-22

### Added
- 🧠 **Per-Model Reasoning Effort**: Configurable reasoning levels for third-party models in the API Key manager.

---

## [1.6.22] - 2026-06-22

### Added
- 🔁 **Multi-Provider Failover for Identical Models**: Automatic failover across multiple configured providers sharing the same model identifier.

---

## [1.6.21] - 2026-06-21

### Added
- 🔀 **Gemini-to-Third-Party Failover Chain**: Automatically fallback to configured third-party models when upstream Gemini requests fail.

---

## [1.6.20] - 2026-06-19

### Fixed
- 🐳 **Non-Root Docker Permissions**: Re-engineered `docker-entrypoint.sh` to initialize with root, fix `/app/data` volume permissions, and drop privileges to `appuser` (UID 1000) using `gosu`.

---

## [1.6.19] - 2026-06-19

### Security
- 🔒 **Admin Console XSS Hardening**: Escaped all dynamic fields, converted inline event handlers to `data-*` listeners, and utilized `escapeAttr`.
- 🔒 **SSRF Protection**: Blocked private, loopback, and cloud metadata addresses in attachment downloads and third-party Base URLs.
- 🔒 **Input Validation**: Strict validation for runtime settings and path traversal protection for conversation files.

---

## [1.6.18] - 2026-06-19

### Fixed
- 🔧 Extended timeout for image generation requests to 180s and reduced SSE keepalive intervals.

---

## [1.6.17] - 2026-06-19

### Fixed
- 🔧 Streamlined image generation streaming with immediate SSE headers and periodic keepalives.

---

## [1.6.16] - 2026-06-19

### Fixed
- 🔧 Resolved parameter naming error in Deep Research endpoint.
- 🔧 Fixed connection lifecycle handling in third-party streaming forwarders.
- 🔒 Added optional `ADMIN_API_KEY` for dedicated admin authentication.
- 🔒 Masked credentials and API keys in logs and exports.

---

## [1.6.15] - 2026-06-06

### Added
- 🧹 **Automated Gemini Web Chat Cleanup**: Background loop cleans up old web conversations (skipping pinned chats) to prevent account clutter.

---

## [1.6.14] - 2026-06-02

### Fixed
- 🖼️ Enhanced image intent recognition for desire verbs (`want`, `need`).

---

## [1.6.13] - 2026-06-02

### Changed
- 🖼️ Formatted image generation responses with images placed at the top followed by descriptions.

---

## [1.6.12] - 2026-06-02

### Fixed
- 🛠️ Fixed tool call interference during image generation in coding agents.
- 🔧 Proper part formatting for native Gemini `/v1beta` function calls.

---

## [1.6.11] - 2026-06-02

### Added
- 🔁 **Smart 503 Failover**: Instant failover to alternative accounts in the pool upon receiving Google Web 503 rate limits.

---

## [1.6.10] - 2026-06-01

### Added
- ⚡ **True Incremental Streaming**: Incremental streaming directly from Google's `StreamGenerate` RPC for real-time typing feel.
- 🚀 Increased account concurrency and added request queuing.

---

## [1.6.9] - 2026-06-01

### Fixed
- 🖼️ Downloads generated images at full resolution (`=s0`).

---

## [1.6.8] - 2026-06-01

### Fixed
- 🖼️ Filtered raw placeholder URLs from generated image responses.

---

## [1.6.7] - 2026-06-01

### Fixed
- 🖼️ Rendered images directly inside the Playground chat console.

---

## [1.6.6] - 2026-05-31

### Added
- 🖼️ Local image hosting: Generated images served via `/images/{id}` with scheduled cleanup.

---

## [1.6.5] - 2026-05-31

### Added
- 🎨 Image generation support via `POST /v1/images/generations` and multimodal conversation endpoints.

---

## [1.6.4] - 2026-05-31

### Added
- Standard un-prefixed endpoints (`/v1/chat/completions`, `/v1/messages`, `/v1beta/models`).

---

## [1.6.3] - 2026-05-31

### Added
- Multimodal image and file uploads via base64 and remote URLs.
- Dynamic model discovery using Google Web RPCs.

---

## [1.6.2] - 2026-05-19

### Added
- Inactivity session timeout for the web console.

---

## [1.6.1] - 2026-05-18

### Added
- Multi-account failover load balancing strategy.
- Multi-language localization system with dynamic UI translation.
- Third-party model API Key management and aggregation into `/openai/v1/models`.

---

## [0.7.0] - 2026-05-16

### Added
- Structured in-memory LogStore ring buffer (2000 items) with pagination, search, and direction filtering.

---

## [0.6.0] - 2026-05-16

### Added
- Persistent usage statistics, time-series snapshots, and interactive SVG charts.

---

## [0.5.2] - 2026-05-16

### Added
- Batchexecute heartbeat RPC (`otAQ7b`) and enhanced cookie jar response header parsing.

---

## [0.5.1] - 2026-05-16

### Fixed
- Handled cross-domain cookie accumulation and clipboard fallback in non-HTTPS environments.

---

## [0.5.0] - 2026-05-15

### Added
- Anti-detection and browser fingerprinting system (Chrome TLS fingerprinting, dynamic headers, request timing jitter).

---

## [0.4.0] - 2026-05-15

### Changed
- Switched HTTP transport from httpx to `curl_cffi` for realistic TLS fingerprint emulation.

---

## [0.3.0] - 2026-05-15

### Added
- Multi-account load balancing and health check management.

---

## [0.2.0] - 2026-05-15

### Added
- Scheduled account health checks and hot cookie reloading.

---

## [0.1.0] - 2026-05-14

### Added
- Function calling across OpenAI, Claude, and Gemini formats.
- SSE streaming and initial admin console.

---

## [0.0.1] - 2026-05-13

### Added
- Initial release of the Gemini Web reverse proxy.
