"""Helpers for the live Argon E2E suite (no app imports: everything goes over HTTP)."""
from __future__ import annotations

import base64
import json
import os
import struct
import zlib
from dataclasses import dataclass, field

import httpx

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:5918").rstrip("/")
API_KEY = os.environ.get("G2A_API_KEY") or os.environ.get("E2E_API_KEY") or ""
MODEL = os.environ.get("E2E_MODEL", "gemini-4-argon")
TIMEOUT = float(os.environ.get("E2E_TIMEOUT", "480"))  # Argon needs 2-6 min per call
LIVE = os.environ.get("E2E_LIVE", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- images (stdlib only)
def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def png_solid(rgb: tuple[int, int, int], size: int = 128) -> bytes:
    return _png(size, size, [bytes(rgb) * size] * size)


def png_stripes(colors: list[tuple[int, int, int]], size: int = 128) -> bytes:
    """Horizontal stripes, top to bottom."""
    per = size // len(colors)
    rows: list[bytes] = []
    for i in range(size):
        rows.append(bytes(colors[min(i // per, len(colors) - 1)]) * size)
    return _png(size, size, rows)


def data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def b64(png: bytes) -> str:
    return base64.b64encode(png).decode()


# ---------------------------------------------------------------- OpenAI chat/completions
@dataclass
class StreamResult:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{"name":..., "arguments":"..."}]
    finish_reason: str | None = None
    saw_done: bool = False
    chunks: int = 0


def chat(client: httpx.Client, messages: list[dict], **extra) -> dict:
    body = {"model": extra.pop("model", MODEL), "messages": messages, "stream": False, **extra}
    r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:800]}"
    return r.json()


def chat_stream(client: httpx.Client, messages: list[dict], **extra) -> StreamResult:
    body = {"model": extra.pop("model", MODEL), "messages": messages, "stream": True, **extra}
    res = StreamResult()
    calls: dict[int, dict] = {}
    with client.stream("POST", "/v1/chat/completions", json=body) as r:
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.read()[:800]!r}"
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                res.saw_done = True
                break
            obj = json.loads(payload)
            assert "error" not in obj, f"stream error: {obj}"
            res.chunks += 1
            for ch in obj.get("choices", []):
                d = ch.get("delta") or {}
                res.text += d.get("content") or ""
                res.reasoning += d.get("reasoning_content") or ""
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "arguments": ""})
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
                if ch.get("finish_reason"):
                    res.finish_reason = ch["finish_reason"]
    res.tool_calls = [calls[k] for k in sorted(calls)]
    return res


def message(resp: dict) -> dict:
    return resp["choices"][0]["message"]


def text_of(resp: dict) -> str:
    return message(resp).get("content") or ""


def tool_calls_of(resp: dict) -> list[dict]:
    return message(resp).get("tool_calls") or []


def tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required or list(properties)}}}


def args_of(tc: dict) -> dict:
    fn = tc.get("function", tc)
    raw = fn.get("arguments") or "{}"
    return json.loads(raw) if isinstance(raw, str) else raw


# ---------------------------------------------------------------- Anthropic /v1/messages
def claude(client: httpx.Client, body: dict) -> dict:
    body = {"model": MODEL, "max_tokens": 1024, "stream": False, **body}
    r = client.post("/v1/messages", json=body)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:800]}"
    return r.json()


def claude_stream(client: httpx.Client, body: dict) -> dict:
    """Returns {"text", "tool_uses", "stop_reason", "events"}; asserts a well-formed event sequence."""
    body = {"model": MODEL, "max_tokens": 1024, **body, "stream": True}
    events: list[str] = []
    text = ""
    tool_uses: dict[int, dict] = {}
    stop_reason = None
    with client.stream("POST", "/v1/messages", json=body) as r:
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.read()[:800]!r}"
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            obj = json.loads(line[5:].strip())
            events.append(obj.get("type", ""))
            t = obj.get("type")
            if t == "content_block_start" and obj["content_block"].get("type") == "tool_use":
                tool_uses[obj["index"]] = {"name": obj["content_block"]["name"], "json": ""}
            elif t == "content_block_delta":
                d = obj["delta"]
                if d.get("type") == "text_delta":
                    text += d.get("text", "")
                elif d.get("type") == "input_json_delta":
                    tool_uses[obj["index"]]["json"] += d.get("partial_json", "")
            elif t == "message_delta":
                stop_reason = obj["delta"].get("stop_reason")
    assert events and events[0] == "message_start", events[:3]
    assert events[-1] == "message_stop", events[-3:]
    return {"text": text, "tool_uses": [tool_uses[k] for k in sorted(tool_uses)],
            "stop_reason": stop_reason, "events": events}


def claude_text(resp: dict) -> str:
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


# ---------------------------------------------------------------- virtual filesystem + agent loop
class VirtualFS:
    """In-memory filesystem the model reaches ONLY through tool calls (the proxy has no real FS)."""

    def __init__(self, files: dict[str, str]):
        self.files = dict(files)
        self.trace: list[tuple[str, dict]] = []

    def run(self, name: str, args: dict) -> str:
        self.trace.append((name, args))
        path = str(args.get("path", "")).rstrip("/") or "/"
        if name == "list_directory":
            prefix = path.rstrip("/") + "/"
            entries = sorted({p[len(prefix):].split("/")[0] + ("/" if "/" in p[len(prefix):] else "")
                              for p in self.files if p.startswith(prefix)})
            return "\n".join(entries) if entries else f"ERROR: no such directory: {path}"
        if name == "read_file":
            return self.files.get(path, f"ERROR: no such file: {path}")
        if name == "write_file":
            self.files[path] = str(args.get("content", ""))
            return f"OK: wrote {len(self.files[path])} bytes to {path}"
        return f"ERROR: unknown tool {name}"


FS_TOOLS = [
    tool("list_directory", "List the entries of a directory. Directories end with '/'.",
         {"path": {"type": "string", "description": "Absolute directory path"}}),
    tool("read_file", "Read a text file and return its contents.",
         {"path": {"type": "string", "description": "Absolute file path"}}),
    tool("write_file", "Create or overwrite a text file.",
         {"path": {"type": "string"}, "content": {"type": "string"}}),
]


def run_agent(client: httpx.Client, fs: VirtualFS, task: str, max_steps: int = 8, **extra) -> tuple[str, int]:
    """Tool loop against the proxy. Returns (final_text, steps_used)."""
    messages: list[dict] = [
        {"role": "system", "content": "You are a file assistant. Use the tools to inspect or change files; "
                                      "never guess file contents. When done, answer briefly."},
        {"role": "user", "content": task},
    ]
    for step in range(1, max_steps + 1):
        resp = chat(client, messages, tools=FS_TOOLS, **extra)
        calls = tool_calls_of(resp)
        if not calls:
            return text_of(resp), step
        messages.append({"role": "assistant", "content": text_of(resp) or None, "tool_calls": calls})
        for tc in calls:
            out = fs.run(tc["function"]["name"], args_of(tc))
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
    raise AssertionError(f"agent did not finish in {max_steps} steps; trace={fs.trace}")
