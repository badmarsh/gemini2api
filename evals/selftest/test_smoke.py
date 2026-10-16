"""Smoke tests: the in-process SUT boots, serves real HTTP, and the runner classifies results correctly."""
from __future__ import annotations

import asyncio

import httpx

from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT
from evals.runner import RequestSpec, run_request


def _chat(model: str, stream: bool, text: str = "ping") -> RequestSpec:
    return RequestSpec(
        "/v1/chat/completions",
        {"model": model, "stream": stream, "messages": [{"role": "user", "content": text}]},
        stream=stream,
        timeout_s=20,
        label=f"{model}:{'s' if stream else 'ns'}",
    )


async def _run(specs: list[RequestSpec], program) -> tuple[list[dict], InProcessSUT]:
    async with InProcessSUT(accounts=2) as sut:
        program(sut.upstream)
        async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=30) as client:
            recs = [await run_request(client, s) for s in specs]
        recs.append({"leaked_slots": sut.active_slots()})
        return recs, sut


def test_flash_nonstream_and_stream_ok():
    def program(up):
        up.default("other", sc.ok("pong", n_frames=3))

    recs, _ = asyncio.run(_run([_chat("gemini-3-flash", False), _chat("gemini-3-flash", True)], program))
    ns, st, meta = recs
    assert ns["status"] == 200 and ns["content"] == "pong" and ns["error_class"] == "", ns
    assert st["status"] == 200 and st["content"] == "pong", st
    assert st["violations"] == [] and st["error_class"] == "", st
    assert meta["leaked_slots"] == 0


def test_upstream_500_is_reported_not_hidden():
    def program(up):
        up.default("other", sc.http_error(500, "boom"))

    recs, _ = asyncio.run(_run([_chat("gemini-3-flash", False)], program))
    r = recs[0]
    # whatever the app does (error status or error body), it must not look like a clean success
    assert r["error_class"] != "" or r["status"] >= 400, r
    assert recs[1]["leaked_slots"] == 0


def test_argon_ok_stream():
    def program(up):
        up.default("argon", sc.ok("argon says hi", n_frames=4, thoughts="thinking"))
        up.default("other", sc.ok("flash fallback"))

    recs, sut = asyncio.run(_run([_chat("gemini-4-argon", True)], program))
    r = recs[0]
    assert r["status"] == 200 and r["violations"] == [], r
    assert r["content"] in ("argon says hi", "flash fallback"), r
    assert sut.upstream.calls, "fake upstream was never reached"
