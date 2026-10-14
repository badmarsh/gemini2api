<div align="center">

<img src="docs/logo.png" width="128" height="128" alt="Gemini2API">

<h1>Gemini2API</h1>
<h3>Argon & Autonomous Agent Edition</h3>
<p>Lightweight, high-performance Gemini Web reverse proxy with native Gemini 4 Argon thinking support, automated account failover, and agentic multi-turn context preservation. Compatible with OpenAI, Claude, and Gemini SDKs.</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.12+-blue?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/curl__cffi-Chrome%20TLS-ff6b35?style=flat-square&logo=google-chrome&logoColor=white" alt="curl_cffi">
  <img src="https://img.shields.io/badge/Docker-20.10+-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Chrome%20%7C%20Edge-Latest-4285F4?style=flat-square&logo=googlechrome&logoColor=white" alt="Browser">
  <img src="https://img.shields.io/badge/License-Non--Commercial-red?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/version-v1.6.41--argon-success?style=flat-square" alt="Version">
</p>

<p>
  <a href="#-argon--agent-edition-highlights">Fork Highlights</a> &bull;
  <a href="#-tutorial--quickstart">Tutorial & Quickstart</a> &bull;
  <a href="#-autonomous-agent-setup">Agent Setup</a> &bull;
  <a href="#-technical-architecture--deep-dive">Technical Deep Dive</a> &bull;
  <a href="#-core-features">Core Features</a> &bull;
  <a href="#-api-endpoints">API Endpoints</a> &bull;
  <a href="#-configuration">Configuration</a>
</p>

<p>
  📖 Documentation Language: <strong>English</strong> | <a href="docs/zh-CN/README.md">简体中文</a> | <a href="docs/zh-TW/README.md">繁體中文</a> | <a href="docs/ja/README.md">日本語</a> | <a href="docs/ko/README.md">한국어</a>
</p>

<br>

<a href="https://github.com/badmarsh/gemini2api/issues"><img src="https://img.shields.io/github/issues/badmarsh/gemini2api?style=flat-square" alt="Issues"></a>
<a href="https://github.com/badmarsh/gemini2api/stargazers"><img src="https://img.shields.io/github/stars/badmarsh/gemini2api?style=flat-square" alt="Stars"></a>

</div>

---

> [!NOTE]
> **🚀 badmarsh/gemini2api (Gemini 4 Argon & Agent Hardened Edition)**:
> This repository is an active, hardened public fork of [xwteam/gemini2api](https://github.com/xwteam/gemini2api).
> It brings full upstream protocol compatibility for **Gemini 4 Argon (`gemini-4-argon`)**, 180s deep reasoning timeouts, automated Google Web error template failover, zero-stall cookie jar filtering, and multi-turn context preservation tailored for autonomous coding agents (Codex, OpenCode, Antigravity, Open Hands).

> [!WARNING]
> This project is for research and personal educational use only. It reverse-engineers browser cookies to interact with the Gemini Web interface and is not affiliated with Google. Use responsibly.

---

## 🚀 Argon & Agent Edition Highlights

| Feature | Upstream (`xwteam/gemini2api`) | This Fork (`badmarsh/gemini2api`) |
| :--- | :--- | :--- |
| **Gemini 4 Argon Support** | ❌ Silent black-hole (legacy 4-element payload dropped by Google) | ✅ **Full Support**: 81-element thinking payload with `f.sid`/`bl` parameters and companion headers |
| **Extended Reasoning Timeout** | ⚠️ 60s hard timeout (drops during 30–75s thinking phase) | ✅ **180s Timeout**: Supports deep reasoning models without socket dropouts |
| **Google Web Error Interception** | ❌ Leaks UI error strings as valid AI output | ✅ **Auto-Failover**: Detects Google error templates, cools down account, and switches immediately |
| **Stream Stability** | ⚠️ Intermittent 168-byte stream hangs | ✅ **Zero Stalls**: Purifies cookie jar to authentication-only cookies (`__Secure-1PSID`, `__Secure-1PSIDTS`) |
| **Coding Agent Multi-Turn** | ⚠️ Flattens to single-turn on `gemini_conv_id`, breaking context | ✅ **Full Context Preservation**: Retains complete multi-turn message history for agent tool loops |
| **OpenAI `developer` Role** | ❌ Unhandled or dropped | ✅ Transparently mapped to `System: <content>` |
| **Staggered Initialization** | ❌ Simultaneous hits trigger `identity.hfcr` limit | ✅ 4-second inter-account delay prevents startup rate limiting |
| **Admin Cookie Reload** | ⚠️ State remained in failure/expired status | ✅ Resets error counters, clears failures, marks `ACTIVE`, and persists immediately |

---

## 📦 Tutorial & Quickstart

### Prerequisites
- Docker & Docker Compose (or Python 3.12+ with `curl_cffi`)
- One or more Google accounts signed into [Gemini Web](https://gemini.google.com)

---

### Step 1: Clone the Fork

```bash
git clone https://github.com/badmarsh/gemini2api.git
cd gemini2api
```

---

### Step 2: Extract Your Google Cookies

1. Open your browser in an **Incognito / Private Window** and navigate to [https://gemini.google.com](https://gemini.google.com).
2. Sign in with your Google account.
3. Open **Developer Tools** (`F12` or `Ctrl+Shift+I` / `Cmd+Option+I`) and go to the **Application** (or **Storage**) tab -> **Cookies** -> `https://gemini.google.com`.
4. Copy the values of the following two cookies:
   - `__Secure-1PSID` (starts with `g.a000...`)
   - `__Secure-1PSIDTS` (starts with `sidts-...`)
5. **Crucial Rule:** Close the incognito window **WITHOUT clicking "Sign Out"**. (Signing out invalidates the session tokens immediately).

---

### Step 3: Configure `accounts.json` and `.env`

Copy the template configuration files:

```bash
cp .env.example .env
cp accounts.json.example accounts.json
```

Edit `accounts.json` with your extracted cookies:

```json
[
  {
    "id": "account-0",
    "name": "Main Google Account",
    "psid": "g.a000YOUR_SECURE_1PSID_COOKIE_HERE",
    "psidts": "sidts-YOUR_SECURE_1PSIDTS_COOKIE_HERE"
  }
]
```

Edit `.env` to specify your preferred gateway port and access token:

```env
PORT=5918
API_KEY=sk-your-custom-gateway-api-key
ACCOUNTS_FILE=accounts.json
LOG_LEVEL=INFO
```

---

### Step 4: Run via Docker Compose

```bash
docker compose up -d
```

Verify that the service is running:
- Web Admin Panel: [http://localhost:5918](http://localhost:5918)
- Check container logs: `docker compose logs -f`

---

### Step 5: Test the API

#### 1. Quick cURL Test (Gemini 4 Argon)

```bash
curl http://localhost:5918/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-custom-gateway-api-key" \
  -d '{
    "model": "gemini-4-argon",
    "messages": [
      {"role": "user", "content": "Explain the concept of quantum superposition in one concise sentence."}
    ],
    "stream": false
  }'
```

#### 2. Streaming Request with Thinking

```bash
curl http://localhost:5918/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-custom-gateway-api-key" \
  -d '{
    "model": "gemini-4-argon",
    "messages": [
      {"role": "user", "content": "Solve 24 * 18 - 72 / 4 step by step."}
    ],
    "stream": true
  }'
```

---

## 🤖 Autonomous Agent Setup

This fork is explicitly optimized for autonomous coding agents (Codex, OpenCode, Antigravity, Open Hands, Cline) where reasoning latency, multi-turn history, and developer prompts are critical.

### OpenAI Python SDK Example

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:5918/v1",
    api_key="sk-your-custom-gateway-api-key",
    timeout=180.0  # Required for deep reasoning models
)

response = client.chat.completions.create(
    model="gemini-4-argon",
    messages=[
        {"role": "developer", "content": "You are an expert systems programmer."},
        {"role": "user", "content": "Write a thread-safe atomic lock in Python."}
    ],
    stream=True
)

for chunk in response:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()
```

### OpenCode / Codex `config.json` Example

```json
{
  "model": "gemini-4-argon",
  "api_base": "http://127.0.0.1:5918/v1",
  "api_key": "sk-your-custom-gateway-api-key",
  "request_timeout": 180,
  "max_retries": 3
}
```

---

## 🧠 Technical Architecture & Deep Dive

### 1. The October 2026 Protocol Rollout (Why Upstream Broke)

In October 2026, Google updated the internal Gemini Web RPC interface (`StreamGenerate`).
- **Legacy Interface**: Supported a compact 4-element JSON payload sent over `POST /_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate`.
- **New Interface**: Requests utilizing the legacy 4-element structure are silently discarded by Google (returning empty bodies or hanging connections).
- **The Fix**: The request must supply an **81-element thinking array structure** where `inner[80] = 2` for mandatory reasoning models (such as `gemini-4-argon`). Furthermore, the URL requires minting and passing `f.sid` and `bl` parameters along with a 15-element companion header `x-goog-ext-525005358-jspb: ["<UUID>", 1]`.

```mermaid
sequenceDiagram
    autonumber
    actor Client as Agent / Client
    participant Proxy as Gemini2API Gateway
    participant Pool as AccountPool (Failover)
    participant Google as Gemini Web (StreamGenerate)

    Client->>Proxy: POST /v1/chat/completions (model: gemini-4-argon)
    Proxy->>Pool: Acquire healthy warm account
    Pool->>Proxy: Return Account (shared AsyncSession)
    Proxy->>Google: POST StreamGenerate (81-element payload, inner[80]=2, 180s timeout)
    Note over Google: Google Argon Reasoning (30-75s)
    
    alt Normal Streaming
        Google-->>Proxy: SSE Event Chunks (thoughts + answer)
        Proxy-->>Client: data: {"choices": [{"delta": {"content": "..."}}]}
    else Google Web Error Template Rejection
        Google-->>Proxy: "I seem to be encountering an error..."
        Proxy->>Pool: Raise GoogleWebError -> Put Account on Cooldown
        Pool->>Proxy: Failover to Next Healthy Account
        Proxy->>Google: Resubmit Request on Alternate Account
        Google-->>Proxy: Successful SSE Generation
        Proxy-->>Client: Clean Streaming Response
    end
```

### 2. 180-Second Reasoning Window
Gemini 4 Argon employs extensive test-time compute (extended thinking). TTFT (Time to First Token) regularly spans **30 to 75 seconds** before the first output chunk is emitted. The default 60-second timeouts in `curl_cffi` and FastAPI clients caused frequent `Operation timed out` crashes. Raising default session and generation timeouts to 180s ensures uninterrupted reasoning.

### 3. Eradicating 168-Byte Stream Hangs
A chronic issue with Gemini reverse proxies has been the "168-byte stall," where the stream disconnects after reading exactly 168 bytes.
- **Root Cause**: Upstream saved *all* cookies returned by Google into `cookie_jar.py`, including anti-abuse telemetry cookies (`NID`, `SIDCC`, `AEC`, `OTZ`). Replaying these ephemeral cookies across different process sessions caused Google's server-side session validator to detect an inconsistent browser context and silently drop the HTTP/2 stream.
- **Remediation**: `cookie_jar.py` now enforces a strict whitelist:
  ```python
  PERSISTED_COOKIE_NAMES = {"__Secure-1PSID", "__Secure-1PSIDTS"}
  ```
  Only authentic credential cookies are retained and rotated; volatile session cookies are discarded, eliminating 168-byte hangs permanently.

### 4. Multi-Turn Context Retention for Agents
Upstream collapsed multi-turn prompts into just the latest single user message when `gemini_conv_id` was active. For coding agents that maintain conversation state locally across iterations, this caused prompt amnesia and failed multi-round tasks whenever account failover occurred. This fork detects multi-turn history (`len(messages) > 1`) and preserves the full formatted conversation.

---

## 🌟 Core Features

- **Triple Protocol Compatibility**: Simultaneous support for OpenAI (`/v1/chat/completions`), Anthropic Claude (`/v1/messages`), and Gemini Native (`/v1beta/models/...`).
- **Web Admin Dashboard**: Real-time monitoring of account statuses, model testing playground, error history, and dynamic settings.
- **TLS Fingerprint Impersonation**: Uses `curl_cffi` with Chrome 124+ TLS signatures to avoid bot detection.
- **Automatic Quota & Health Tracking**: Tracks per-account error rates and automatically cycles unhealthy or rate-limited accounts.
- **Zero Official Key Requirement**: Uses standard Google web sessions without Google Cloud billing dependencies.

---

## 📑 API Endpoints Reference

| Protocol | Method | Endpoint | Description |
| :--- | :--- | :--- | :--- |
| **OpenAI** | `POST` | `/v1/chat/completions` | Standard chat completion (streaming & non-streaming) |
| **OpenAI** | `GET` | `/v1/models` | List all available Gemini models and aliases |
| **Anthropic** | `POST` | `/v1/messages` | Claude format endpoint with tool calling support |
| **Gemini** | `POST` | `/v1beta/models/{model}:generateContent` | Native Google GenAI SDK compatibility |
| **Gemini** | `POST` | `/v1beta/models/{model}:streamGenerateContent` | Native Google GenAI streaming compatibility |
| **Admin** | `GET` | `/admin/accounts` | List accounts and live health metrics |
| **Admin** | `PUT` | `/admin/accounts/{id}/cookies` | Hot-update account credentials without downtime |

---

## ⚙️ Configuration Reference (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `5918` | Listening port for the gateway service |
| `API_KEY` | Auto-generated | Bearer token required for clients calling the proxy |
| `ACCOUNTS_FILE` | `accounts.json` | Path to the multi-account cookie configuration file |
| `ROTATION_STRATEGY` | `round_robin` | Account rotation mode (`round_robin` or `failover`) |
| `FALLBACK_ENABLED` | `false` | Enable fallback to third-party providers when accounts exhaust |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## 📄 License & Disclaimer

- **License**: [Non-Commercial License](LICENSE). Free for personal, educational, and research use.
- **Disclaimer**: This project is not affiliated with Google. Gemini is a registered trademark of Google LLC.
