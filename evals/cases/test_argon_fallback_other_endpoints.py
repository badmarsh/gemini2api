"""T1 cases: the Argon -> Flash fallback on /v1/messages (Anthropic) and the native Gemini endpoints.

These two routers had no Argon fallback; they now share app/core/argon_fallback.py. Same contract as the
OpenAI endpoint (see test_argon_fallback.py): with ``argon_fallback_to_flash`` on, an Argon failure / hang /
empty reply / web-error text degrades to Flash and the client sees one clean 200 answer.
Run:  py -3.14 -m pytest evals/cases/test_argon_fallback_other_endpoints.py -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from evals.cases.test_argon_fallback import FLASH_TEXT, MODEL, SITUATIONS
from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT
from evals.runner import RequestSpec, run_request

# Content already streamed before the stall: not part of the "failed before any content" contract.
NOT_APPLICABLE = {("hang_after_first", True)}


def _anthropic_spec(stream: bool) -> RequestSpec:
    body = {"model": MODEL, "max_tokens": 64, "stream": stream,
            "messages": [{"role": "user", "content": "say hello"}]}
    return RequestSpec("/v1/messages", body, stream=stream, timeout_s=30, label="anthropic")


def _gemini_body() -> dict:
    return {"contents": [{"role": "user", "parts": [{"text": "say hello"}]}]}


async def _gemini_stream(client: httpx.AsyncClient) -> dict:
    """streamGenerateContent is NDJSON (application/json), which run_request does not parse: do it here."""
    async with client.stream("POST", f"/v1beta/models/{MODEL}:streamGenerateContent", json=_gemini_body()) as resp:
        raw = (await resp.aread()).decode("utf-8", "replace")
    text, errors = "", []
    for line in raw.splitlines():
        if not line.strip():
            continue  # blank-line keepalive
        obj = json.loads(line)
        if "error" in obj:
            errors.append(obj["error"])
        for cand in obj.get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                text += part.get("text", "")
    return {"status": resp.status_code, "content": text, "stream_errors": errors,
            "error_class": "stream_error" if errors else "", "violations": []}


async def _one(proto: str, scenario, *, stream: bool, fallback: bool) -> dict:
    async with InProcessSUT(accounts=2) as sut:
        sut.upstream.default("argon", scenario)
        sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
        async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=60) as client:
            with sut.settings(argon_fallback_to_flash=fallback):
                if proto == "anthropic":
                    rec = await run_request(client, _anthropic_spec(stream))
                elif stream:
                    rec = await _gemini_stream(client)
                else:
                    spec = RequestSpec(f"/v1beta/models/{MODEL}:generateContent", _gemini_body(),
                                       stream=False, timeout_s=30, label="gemini")
                    rec = await run_request(client, spec)
        await asyncio.sleep(0.05)
        rec["other_calls"] = len(sut.upstream.calls_for("other"))
        rec["leaked_slots"] = sut.active_slots()
        return rec


def run(proto: str, scenario, **kw) -> dict:
    return asyncio.run(_one(proto, scenario, **kw))


def _params():
    for proto in ("anthropic", "gemini"):
        for name in SITUATIONS:
            for stream in (False, True):
                if (name, stream) in NOT_APPLICABLE:
                    continue
                yield pytest.param(proto, name, stream,
                                   id=f"{proto}-{name}-{'stream' if stream else 'nonstream'}")


@pytest.mark.parametrize("proto,name,stream", list(_params()))
def test_fallback_on_serves_flash_cleanly(proto: str, name: str, stream: bool):
    r = run(proto, SITUATIONS[name](), stream=stream, fallback=True)
    assert r["status"] == 200, r
    assert r["error_class"] == "", r
    assert r["violations"] == [], r
    assert r["content"].strip() == FLASH_TEXT, r
    assert r["other_calls"] >= 1, "Flash was never asked"
    assert r["leaked_slots"] == 0, r


@pytest.mark.parametrize("proto", ["anthropic", "gemini"])
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_fallback_off_never_calls_flash(proto: str, stream: bool):
    r = run(proto, sc.empty(), stream=stream, fallback=False)
    assert r["other_calls"] == 0, "fallback is off, Flash must not be called"
    assert r["leaked_slots"] == 0, r


@pytest.mark.parametrize("proto", ["anthropic", "gemini"])
def test_non_argon_models_are_untouched(proto: str):
    """A plain Flash request must not get an Argon fallback: its failure is reported, not retried elsewhere."""
    async def go():
        async with InProcessSUT(accounts=2) as sut:
            sut.upstream.default("argon", sc.ok("argon reply", n_frames=3))
            sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=60) as client:
                if proto == "anthropic":
                    body = {"model": "gemini-3-flash", "max_tokens": 64,
                            "messages": [{"role": "user", "content": "say hello"}]}
                    resp = await client.post("/v1/messages", json=body)
                else:
                    resp = await client.post("/v1beta/models/gemini-3-flash:generateContent", json=_gemini_body())
            return resp.status_code, len(sut.upstream.calls_for("argon")), len(sut.upstream.calls_for("other"))
    status, argon_calls, other_calls = asyncio.run(go())
    assert status == 200
    assert argon_calls == 0 and other_calls >= 1
