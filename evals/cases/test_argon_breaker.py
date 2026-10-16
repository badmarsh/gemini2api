"""T1 cases: the Argon stall circuit breaker (app/core/argon_breaker.py).

Contract: with ``argon_fallback_to_flash`` on and ``argon_stall_breaker_seconds`` > 0, a hung Argon request
(timeout) trips the breaker; for the next ``argon_stall_breaker_seconds`` Argon requests are served by Flash
directly (0 Argon calls, Flash latency). After the window the next request probes Argon again. TTL 0 disables
it; ``argon_fallback_to_flash=false`` makes it a no-op. All protocol routers honour it.
The breaker uses the real ``time.monotonic``, so cases use a large TTL and expire the window by rewinding
``_open_until`` instead of sleeping.
Run:  py -3.14 -m pytest evals/cases/test_argon_breaker.py -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from evals.cases.test_argon_fallback_other_endpoints import _anthropic_spec, _gemini_body, _gemini_stream
from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT
from evals.runner import RequestSpec, run_request

MODEL = "gemini-4-argon"
FLASH_TEXT = "flash fallback reply"
SCALE = 0.005
TTL = 600.0  # seconds, real: far longer than any case
FLASH_LATENCY_MAX_VIRTUAL_S = 120  # a hung Argon request costs >= 240 virtual s, so this cleanly separates them

PROTOS = ("openai", "anthropic", "responses", "gemini")


async def _send(client: httpx.AsyncClient, proto: str, stream: bool) -> dict:
    if proto == "openai":
        body = {"model": MODEL, "stream": stream, "messages": [{"role": "user", "content": "say hello"}]}
        spec = RequestSpec("/v1/chat/completions", body, stream=stream, timeout_s=60, label="openai")
    elif proto == "anthropic":
        spec = _anthropic_spec(stream)
    elif proto == "responses":
        body = {"model": MODEL, "stream": stream, "input": "say hello"}
        spec = RequestSpec("/v1/responses", body, stream=stream, timeout_s=60, label="responses")
    elif stream:
        return await _gemini_stream(client)
    else:
        spec = RequestSpec(f"/v1beta/models/{MODEL}:generateContent", _gemini_body(),
                           stream=False, timeout_s=60, label="gemini")
    return await run_request(client, spec)


async def _sequence(proto: str, stream: bool, *, ttl: float, fallback_1: bool = True, fallback_2: bool = True,
                    rewind_before_2: bool = False, trip_directly: bool = False) -> tuple[dict, dict]:
    """Request 1 hangs on Argon (or the breaker is tripped by hand), then request 2 follows. Returns both records,
    each carrying ``argon_calls`` / ``other_calls`` made *by that request*, ``virtual_s`` and ``breaker_open``."""
    async with InProcessSUT(accounts=2, time_scale=SCALE) as sut:
        sut.upstream.default("argon", sc.hang_before_first_frame())
        sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
        recs: list[dict] = []
        async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
            for i, fb in enumerate((fallback_1, fallback_2)):
                if i == 0 and trip_directly:
                    with sut.settings(argon_stall_breaker_seconds=ttl, argon_fallback_to_flash=True):
                        sut._breaker.trip()
                    recs.append({"skipped": True, "argon_calls": 0, "other_calls": 0})
                    continue
                if i == 1 and rewind_before_2:
                    sut._breaker._open_until = time.monotonic() - 1.0
                a0, o0 = len(sut.upstream.calls_for("argon")), len(sut.upstream.calls_for("other"))
                with sut.settings(argon_stall_breaker_seconds=ttl, argon_fallback_to_flash=fb):
                    rec = await _send(client, proto, stream)
                    rec["breaker_open"] = sut._breaker.is_open()
                await asyncio.sleep(0.05)
                rec["argon_calls"] = len(sut.upstream.calls_for("argon")) - a0
                rec["other_calls"] = len(sut.upstream.calls_for("other")) - o0
                rec["virtual_s"] = (rec.get("total_s") or 0) / SCALE
                rec["leaked_slots"] = sut.active_slots()
                recs.append(rec)
        return recs[0], recs[1]


def run(*a, **kw) -> tuple[dict, dict]:
    return asyncio.run(_sequence(*a, **kw))


def _assert_flash_200(r: dict) -> None:
    assert r["status"] == 200 and r["error_class"] == "", r
    assert r["violations"] == [], r
    assert r["content"].strip() == FLASH_TEXT, r
    assert r["leaked_slots"] == 0, r


def _assert_served_by_flash_directly(r: dict) -> None:
    _assert_flash_200(r)
    assert r["argon_calls"] == 0, f"breaker open but Argon was still called: {r}"
    assert r["other_calls"] >= 1, r
    assert r["virtual_s"] <= FLASH_LATENCY_MAX_VIRTUAL_S, r


# ---- (a) a hang trips the breaker; the next request skips Argon -------------------------------------------
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_hang_trips_breaker_and_next_request_skips_argon(stream: bool):
    first, second = run("openai", stream, ttl=TTL)
    _assert_flash_200(first)
    assert first["argon_calls"] == 1, first  # the stalled probe itself
    assert first["breaker_open"] is True, first
    _assert_served_by_flash_directly(second)


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_request_after_window_probes_argon_again(stream: bool):
    _, second = run("openai", stream, ttl=TTL, rewind_before_2=True)
    _assert_flash_200(second)
    assert second["argon_calls"] == 1, second  # window over: Argon probed (and stalled) again
    assert second["breaker_open"] is True, second  # ... which re-trips it


# ---- (b) ttl 0 disables ------------------------------------------------------------------------------------
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_ttl_zero_disables_breaker(stream: bool):
    first, second = run("openai", stream, ttl=0.0)
    _assert_flash_200(first)
    assert first["breaker_open"] is False, first
    _assert_flash_200(second)
    assert second["argon_calls"] >= 1, "ttl 0: every request must still try Argon first"
    assert second["virtual_s"] > FLASH_LATENCY_MAX_VIRTUAL_S, second


# ---- (c) argon_fallback_to_flash=false ignores it ------------------------------------------------------------
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_fallback_off_ignores_breaker(stream: bool):
    """Breaker tripped by hand, then a request with the fallback switched off must still go to Argon."""
    _, second = run("openai", stream, ttl=TTL, fallback_2=False, trip_directly=True)
    assert second["breaker_open"] is False, second
    assert second["argon_calls"] >= 1, f"fallback off: the breaker must not divert to Flash: {second}"
    assert second["other_calls"] == 0, second


# ---- (d) every protocol router honours it -----------------------------------------------------------------
@pytest.mark.parametrize("proto", PROTOS)
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_open_breaker_is_honoured_by_every_router(proto: str, stream: bool):
    _, second = run(proto, stream, ttl=TTL, trip_directly=True)
    _assert_served_by_flash_directly(second)


@pytest.mark.parametrize("proto", ["anthropic", "responses", "gemini"])
def test_hang_on_other_routers_trips_breaker(proto: str):
    """The trip happens in the account pool, so a stall seen through any router protects the next request."""
    first, second = run(proto, False, ttl=TTL)
    _assert_flash_200(first)
    assert first["breaker_open"] is True, first
    _assert_served_by_flash_directly(second)


def test_non_argon_model_unaffected_by_open_breaker():
    async def go():
        async with InProcessSUT(accounts=2, time_scale=SCALE) as sut:
            sut.upstream.default("argon", sc.ok("argon reply", n_frames=3))
            sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=60) as client:
                with sut.settings(argon_stall_breaker_seconds=TTL, argon_fallback_to_flash=True):
                    sut._breaker.trip()
                    body = {"model": "gemini-3-flash", "messages": [{"role": "user", "content": "say hello"}]}
                    resp = await client.post("/v1/chat/completions", json=body)
            return resp.status_code, resp.json()["choices"][0]["message"]["content"], \
                len(sut.upstream.calls_for("argon"))
    status, content, argon_calls = asyncio.run(go())
    assert status == 200 and content.strip() == FLASH_TEXT and argon_calls == 0
