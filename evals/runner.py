"""Async HTTP runner: fires one request at a base URL (in-process SUT or live) and records what happened.

Records never contain credentials: the Authorization header is applied by the caller's httpx client and is
not copied into the record, and response bodies are truncated before they are stored.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field

import httpx

from evals import sse

WEB_ERROR_MARKERS = (
    "required gmail settings are off",
    "turn on these settings to continue",
    "i can't use google",
    "unusual traffic",
    "hard time fulfilling your request",
)


@dataclass
class RequestSpec:
    endpoint: str  # path, e.g. /v1/chat/completions
    body: dict | None = None
    method: str = "POST"
    stream: bool = False
    timeout_s: float = 30.0
    headers: dict = field(default_factory=dict)
    disconnect_after_bytes: int | None = None  # close the connection after this many body bytes
    slow_read_s: float = 0.0  # sleep between chunk reads (slow client)
    label: str = ""


def protocol_of(endpoint: str) -> str:
    if endpoint.startswith("/v1/chat/completions") or endpoint.startswith("/openai/v1/chat/completions"):
        return "openai_chat"
    if endpoint.startswith("/v1/responses"):
        return "responses"
    if endpoint.startswith("/v1/messages") or endpoint.startswith("/claude/"):
        return "anthropic"
    if endpoint.startswith("/v1beta/") or endpoint.startswith("/gemini/"):
        return "gemini"
    return "other"


def body_hash(body) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _looks_like_web_error(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in WEB_ERROR_MARKERS)


def _extract_nonstream(proto: str, obj) -> tuple[str, list, str | None, dict | None]:
    """(content, tool_calls, finish_reason, usage) from a non-streaming JSON body."""
    if not isinstance(obj, dict):
        return "", [], None, None
    if proto == "openai_chat":
        ch = (obj.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        tcs = [
            {"id": t.get("id"), "name": (t.get("function") or {}).get("name"),
             "arguments": (t.get("function") or {}).get("arguments", "")}
            for t in (msg.get("tool_calls") or [])
        ]
        return msg.get("content") or "", tcs, ch.get("finish_reason"), obj.get("usage")
    if proto == "anthropic":
        text = "".join(b.get("text", "") for b in obj.get("content", []) if b.get("type") == "text")
        tcs = [{"id": b.get("id"), "name": b.get("name"), "arguments": json.dumps(b.get("input", {}))}
               for b in obj.get("content", []) if b.get("type") == "tool_use"]
        return text, tcs, obj.get("stop_reason"), obj.get("usage")
    if proto == "responses":
        text, tcs = "", []
        for item in obj.get("output", []) or []:
            if item.get("type") == "message":
                for c in item.get("content", []) or []:
                    text += c.get("text", "")
            elif item.get("type") == "function_call":
                tcs.append({"id": item.get("call_id"), "name": item.get("name"),
                            "arguments": item.get("arguments", "")})
        return text, tcs, obj.get("status"), obj.get("usage")
    if proto == "gemini":
        cand = (obj.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        tcs = [{"id": None, "name": p["functionCall"].get("name"),
                "arguments": json.dumps(p["functionCall"].get("args", {}))}
               for p in parts if isinstance(p, dict) and "functionCall" in p]
        return text, tcs, cand.get("finishReason"), obj.get("usageMetadata")
    return "", [], None, None


def classify(rec: dict) -> str:
    """Error taxonomy. Returns "" for a clean success."""
    if rec.get("transport_error"):
        return rec["transport_error"]
    st = rec.get("status", 0)
    if st >= 500:
        return "5xx"
    if st in (401, 403):
        return "auth_error" if not rec.get("expect_auth_error") else ""
    if st == 429:
        return "rate_limit"
    if 400 <= st < 500:
        return "client_4xx"
    if rec.get("violations"):
        return "sse_protocol_violation"
    if rec.get("stream_error"):
        msg = json.dumps(rec["stream_error"]).lower()
        if "timeout" in msg or "timed out" in msg:
            return "upstream_timeout"
        if "cookie" in msg or "session expired" in msg:
            return "cookie_invalid"
        return "stream_error"
    if rec.get("tool_args_invalid"):
        return "tool_json_invalid"
    if _looks_like_web_error(rec.get("content", "")):
        return "web_error_text"
    if rec.get("disconnected_by_client"):
        return ""
    if not rec.get("content") and not rec.get("tool_calls") and not rec.get("thoughts"):
        return "empty_result"
    return ""


async def run_request(client: httpx.AsyncClient, spec: RequestSpec) -> dict:
    proto = protocol_of(spec.endpoint)
    rec: dict = {
        "label": spec.label, "endpoint": spec.endpoint, "protocol": proto, "stream": spec.stream,
        "req_hash": body_hash(spec.body), "status": 0, "ttfb_s": None, "ttft_s": None, "total_s": None,
        "bytes": 0, "max_gap_s": 0.0, "n_chunks": 0, "content": "", "tool_calls": [], "thoughts": "",
        "finish_reason": None, "usage": None, "violations": [], "n_comments": 0, "transport_error": "",
        "model": None, "stream_error": None, "tool_args_invalid": False, "disconnected_by_client": False,
    }
    t0 = time.monotonic()
    last = t0
    raw = bytearray()
    try:
        async with asyncio.timeout(spec.timeout_s):
            async with client.stream(spec.method, spec.endpoint, json=spec.body, headers=spec.headers) as resp:
                rec["status"] = resp.status_code
                rec["content_type"] = resp.headers.get("content-type", "")
                async for chunk in resp.aiter_raw():
                    now = time.monotonic()
                    if rec["ttfb_s"] is None:
                        rec["ttfb_s"] = now - t0
                    else:
                        rec["max_gap_s"] = max(rec["max_gap_s"], now - last)
                    last = now
                    raw.extend(chunk)
                    rec["n_chunks"] += 1
                    if spec.slow_read_s:
                        await asyncio.sleep(spec.slow_read_s)
                    if spec.disconnect_after_bytes is not None and len(raw) >= spec.disconnect_after_bytes:
                        rec["disconnected_by_client"] = True
                        break
    except TimeoutError:
        rec["transport_error"] = "client_timeout"
    except httpx.HTTPError as e:
        rec["transport_error"] = f"conn_error:{type(e).__name__}"
    rec["total_s"] = time.monotonic() - t0
    rec["bytes"] = len(raw)
    text = bytes(raw).decode("utf-8", "replace")
    rec["body_head"] = text[:300]

    if rec["status"] == 200 and not rec["transport_error"]:
        if spec.stream and "text/event-stream" in rec.get("content_type", ""):
            _fill_stream(rec, proto, text, t0)
        else:
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                rec["violations"].append("body_not_json")
                obj = None
            if obj is not None:
                rec["model"] = obj.get("model") if isinstance(obj, dict) else None
                c, tcs, fr, usage = _extract_nonstream(proto, obj)
                rec["content"], rec["tool_calls"], rec["finish_reason"], rec["usage"] = c, tcs, fr, usage
                for tc in tcs:
                    try:
                        json.loads(tc.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        rec["tool_args_invalid"] = True
            rec["ttft_s"] = rec["ttfb_s"]
    elif rec["status"] and rec["status"] != 200:
        try:
            rec["error_body"] = json.loads(text)
        except json.JSONDecodeError:
            rec["error_body"] = None
    rec["error_class"] = classify(rec)
    return rec


def _fill_stream(rec: dict, proto: str, text: str, t0: float) -> None:
    checker = {"openai_chat": sse.check_openai_chat, "responses": sse.check_responses,
               "anthropic": sse.check_anthropic}.get(proto)
    if checker is None:
        rep = sse.StreamReport(proto)
        parsed = sse.parse_sse(text)
        rep.violations = list(parsed.violations)
        rep.n_comments = len(parsed.comments)
    elif rec["disconnected_by_client"]:
        rep = sse.StreamReport(proto)
        rep.n_comments = len(sse.parse_sse(text).comments)
    else:
        rep = checker(text)
    rec["violations"] = list(rep.violations)
    rec["content"], rec["thoughts"], rec["tool_calls"] = rep.content, rep.thoughts, rep.tool_calls
    rec["finish_reason"], rec["usage"], rec["stream_error"] = rep.finish_reason, rep.usage, rep.error
    rec["n_comments"] = rep.n_comments
    rec["ttft_s"] = rec["ttfb_s"]  # first byte; refined by callers that need first content delta
    for tc in rep.tool_calls:
        try:
            json.loads(tc.get("arguments") or "{}")
        except json.JSONDecodeError:
            rec["tool_args_invalid"] = True
