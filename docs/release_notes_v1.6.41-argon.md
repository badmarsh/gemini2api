# Gemini2API v1.6.41-argon: Gemini 4 Argon Protocol & Autonomous Agent Hardening

This release introduces native support for Google's October 2026 Gemini Web protocol changes, with full compatibility for Gemini 4 Argon (`gemini-4-argon`), deep reasoning window expansions, zero-stall cookie filtering, and multi-turn context preservation for autonomous coding agents.

### 🌟 Key Highlights & Architectural Changes

- 🧠 **Gemini 4 Argon Protocol Compatibility**:
  - Implements the new 81-element StreamGenerate payload structure required by Google's backend.
  - Sets `inner[80] = 2` for mandatory extended thinking models.
  - Auto-mints and injects `f.sid` and `bl` parameters into RPC calls.
  - Generates the 15-element companion header `x-goog-ext-525005358-jspb: ["<UUID>", 1]`.

- ⏱️ **180-Second Reasoning Window**:
  - Gemini 4 Argon test-time compute regularly requires 30–75s TTFT (Time to First Token).
  - Raised default session and generation timeouts from 60s to 180s, preventing premature `Operation timed out` aborts.

- 🛡️ **Automated Google Web Error Interception & Warm Failover**:
  - Intercepts Google Web UI error templates (e.g. *"I seem to be encountering an error. Can I try something else for you?"*, *"Sorry, something went wrong"*).
  - Classifies rejections as `GoogleWebError`, places the degraded account on cooldown, and seamlessly fails over to the next healthy account in the pool without dropping the client stream.

- 🍪 **Zero-Stall Cookie Jar Whitelist**:
  - Eliminates chronic 168-byte stream disconnections by whitelisting only authentic credential cookies (`__Secure-1PSID`, `__Secure-1PSIDTS`).
  - Discards ephemeral telemetry cookies (`NID`, `AEC`, `SIDCC`, `OTZ`) that previously triggered Google anti-bot session invalidation.

- 🤖 **Preserved Context for Coding Agents**:
  - Full multi-turn conversation history is preserved across agent cycles (Codex, OpenCode, Antigravity, Open Hands, Cline).
  - Automatic normalization of OpenAI `developer` role messages into `System: <content>`.

- ⚡ **Account Pool Staggering & Immediate Reset**:
  - Staggers startup requests across multi-account pools with 4s intervals to evade Google `identity.hfcr` rate limits.
  - Hot-updating cookies via `/admin/accounts/{id}/cookies` immediately resets failure counters and marks the account `ACTIVE`.

### 📦 Deployment
Run via Docker:
```bash
docker pull ghcr.io/badmarsh/gemini2api:latest
# Or run with Docker Compose
docker compose up -d
```
