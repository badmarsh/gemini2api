"""Small request helpers shared by the T1 case files (not a test module: pytest only collects ``test_*.py``)."""
from __future__ import annotations

import json

import httpx

from evals.runner import RequestSpec, run_request

PROTOS = ("chat", "responses", "anthropic", "gemini")


def plain_request(proto: str, model: str, stream: bool, text: str = "say hello") -> tuple[str, dict]:
    """(path, body) of a plain one-turn text request on the given protocol."""
    if proto == "chat":
        return "/v1/chat/completions", {"model": model, "stream": stream,
                                        "messages": [{"role": "user", "content": text}]}
    if proto == "responses":
        return "/v1/responses", {"model": model, "stream": stream, "input": text}
    if proto == "anthropic":
        return "/v1/messages", {"model": model, "stream": stream, "max_tokens": 64,
                                "messages": [{"role": "user", "content": text}]}
    if proto == "gemini":
        method = "streamGenerateContent" if stream else "generateContent"
        return f"/v1beta/models/{model}:{method}", {"contents": [{"role": "user", "parts": [{"text": text}]}]}
    raise ValueError(proto)


async def gemini_stream(client: httpx.AsyncClient, path: str, body: dict) -> dict:
    """streamGenerateContent is NDJSON (application/json), which ``run_request`` does not parse.

    Returns a record with the same keys the assertions use: text, functionCall parts, in-stream errors.
    """
    async with client.stream("POST", path, json=body) as resp:
        raw = (await resp.aread()).decode("utf-8", "replace")
    text, calls, errors = "", [], []
    for line in raw.splitlines():
        if not line.strip():
            continue  # blank-line keepalive
        obj = json.loads(line)
        if "error" in obj:
            errors.append(obj["error"])
        for cand in obj.get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                text += part.get("text", "")
                if "functionCall" in part:
                    fc = part["functionCall"]
                    calls.append({"id": None, "name": fc.get("name"), "arguments": json.dumps(fc.get("args", {}))})
    return {"status": resp.status_code, "content": text, "tool_calls": calls, "finish_reason": None,
            "stream_error": errors or None, "error_class": "stream_error" if errors else "", "violations": [],
            "tool_args_invalid": False, "thoughts": "", "transport_error": "", "disconnected_by_client": False,
            "n_comments": 0}


async def send(client: httpx.AsyncClient, proto: str, path: str, body: dict, stream: bool, *,
               timeout_s: float = 60, label: str = "") -> dict:
    """Fire one request and return its record, whatever the protocol (native Gemini streaming included)."""
    if proto == "gemini" and stream:
        return await gemini_stream(client, path, body)
    return await run_request(client, RequestSpec(path, body, stream=stream, timeout_s=timeout_s, label=label or proto))
