"""T1 cases: an Argon stall goes straight to Flash instead of burning a timeout per account first.

Contract (app/core/account_pool.py ``_may_fail_over``, app/config.py ``argon_failover_on_stall``): with
``argon_fallback_to_flash`` on and ``argon_failover_on_stall`` off (default), a hung Argon request is not retried
on the next Argon account; it is surfaced at once so the router can degrade to Flash. Setting the flag to true
restores failover-first (one extra full timeout per account).
Run:  py -3.14 -m pytest evals/cases/test_argon_stall_failover.py -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio

import httpx

from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT
from evals.runner import RequestSpec, run_request

MODEL = "gemini-4-argon"
FLASH_TEXT = "flash fallback reply"
SCALE = 0.005  # InProcessSUT default time_scale: real seconds * (1 / SCALE) = virtual seconds


async def _one(*, stream: bool, failover_on_stall: bool) -> dict:
    async with InProcessSUT(accounts=2, time_scale=SCALE) as sut:
        sut.upstream.default("argon", sc.hang_before_first_frame())
        sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
        body = {"model": MODEL, "stream": stream, "messages": [{"role": "user", "content": "say hello"}]}
        spec = RequestSpec("/v1/chat/completions", body, stream=stream, timeout_s=60, label="stall")
        async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
            with sut.settings(argon_fallback_to_flash=True, argon_failover_on_stall=failover_on_stall):
                rec = await run_request(client, spec)
        await asyncio.sleep(0.05)
        rec["argon_calls"] = len(sut.upstream.calls_for("argon"))
        rec["other_calls"] = len(sut.upstream.calls_for("other"))
        rec["leaked_slots"] = sut.active_slots()
        rec["virtual_s"] = rec["total_s"] / SCALE
        return rec


def _run(**kw) -> dict:
    return asyncio.run(_one(**kw))


def _assert_clean_flash(r: dict) -> None:
    assert r["status"] == 200 and r["error_class"] == "", r
    assert r["violations"] == [], r
    assert r["content"] == FLASH_TEXT, r
    assert r["leaked_slots"] == 0, r


def test_nonstream_stall_goes_straight_to_flash():
    r = _run(stream=False, failover_on_stall=False)
    _assert_clean_flash(r)
    assert r["argon_calls"] == 1, r
    assert r["virtual_s"] <= 400, r  # one 360 s Argon timeout + Flash, not 2 x 360


def test_stream_stall_goes_straight_to_flash():
    r = _run(stream=True, failover_on_stall=False)
    _assert_clean_flash(r)
    assert r["argon_calls"] == 1, r
    assert r["virtual_s"] <= 260, r  # one 240 s first-chunk timeout + Flash, not 2 x 240


def test_flag_true_restores_failover_to_second_argon_account():
    r = _run(stream=False, failover_on_stall=True)
    _assert_clean_flash(r)
    assert r["argon_calls"] == 2, r
    assert r["virtual_s"] > 400, r
