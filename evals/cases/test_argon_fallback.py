"""T1 asserting cases: the Argon -> Flash fallback contract on /v1/chat/completions.

Contract source (app/config.py, ``argon_fallback_to_flash``): when Argon fails / times out / has no usable
account / errors / returns an EMPTY reply, the gateway transparently degrades to Gemini Flash. The client
must see one clean 200 answer, not an error and not an empty completion.

Known gaps are encoded as ``xfail(strict=True)``: the suite stays green today, and goes red the moment the
app is fixed (flip the marker off then). Each reason points at the app/ code that explains it.
Run:  py -3.14 -m pytest evals/cases -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT
from evals.runner import RequestSpec, is_silent_empty, run_request

MODEL = "gemini-4-argon"
FLASH_TEXT = "flash fallback reply"

SITUATIONS = {
    "hang_before_first": sc.hang_before_first_frame,
    "hang_after_first": sc.hang_after_first_frame,
    "empty_reply": sc.empty,
    "no_frames": sc.no_frames,
    "web_error_text": sc.web_error,
    "http_429_captcha": sc.captcha,
    "http_500": lambda: sc.http_error(500, "internal"),
    "cookie_expired": sc.cookie_expired_redirect,
    "malformed_frame": sc.malformed_frame,
    "truncated_frame": sc.truncated_frame,
}

# (situation, stream) pairs that do not belong to the "failed before any content" contract.
NOT_APPLICABLE = {("hang_after_first", True)}  # content already streamed: see test_stall_after_first_frame_*

# Open defects go here as {(situation, stream): reason}; they run as xfail(strict=True).
# History: F1 (non-stream timeout escaped the router's except tuple -> 500, no Flash) and F2 (empty Argon
# stream skipped the Flash step) were fixed on this branch; their cases now run as plain assertions.
KNOWN_GAPS: dict = {}

# Open defects of the fallback-OFF contract (``test_fallback_off_never_a_silent_empty_success``), same xfail idea.
# History: these cases used to pass VACUOUSLY (``classify`` reports empty content as "empty_result", so the old
# assertion could never fire); ``is_silent_empty`` fixed that and exposed a real gap (HTTP 200 with empty content when
# the Flash fallback is off). The routers now answer 502 / an error event (app.core.fallback.is_blank_answer).
KNOWN_GAPS_FALLBACK_OFF: dict = {}


def _spec(stream: bool, label: str) -> RequestSpec:
    body = {"model": MODEL, "stream": stream, "messages": [{"role": "user", "content": "say hello"}]}
    return RequestSpec("/v1/chat/completions", body, stream=stream, timeout_s=30, label=label)


async def _one(argon_scenario, *, stream: bool, fallback: bool = True, label: str = "case") -> dict:
    async with InProcessSUT(accounts=2) as sut:
        sut.upstream.default("argon", argon_scenario)
        sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
        async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=60) as client:
            with sut.settings(argon_fallback_to_flash=fallback):
                rec = await run_request(client, _spec(stream, label))
        await asyncio.sleep(0.05)  # let server-side cleanup run before counting slots
        rec["argon_calls"] = len(sut.upstream.calls_for("argon"))
        rec["other_calls"] = len(sut.upstream.calls_for("other"))
        rec["leaked_slots"] = sut.active_slots()
        return rec


def run(scenario, **kw) -> dict:
    return asyncio.run(_one(scenario, **kw))


def _params():
    for name in SITUATIONS:
        for stream in (False, True):
            if (name, stream) in NOT_APPLICABLE:
                continue
            marks = []
            if (name, stream) in KNOWN_GAPS:
                marks.append(pytest.mark.xfail(strict=True, reason=KNOWN_GAPS[(name, stream)]))
            yield pytest.param(name, stream, id=f"{name}-{'stream' if stream else 'nonstream'}", marks=marks)


@pytest.mark.parametrize("name,stream", list(_params()))
def test_fallback_on_serves_flash_cleanly(name: str, stream: bool):
    r = run(SITUATIONS[name](), stream=stream, fallback=True, label=f"{name}|fb")
    assert r["status"] == 200, r
    assert r["error_class"] == "", r
    assert r["violations"] == [], r
    assert r["content"] == FLASH_TEXT, r
    assert r["other_calls"] >= 1, "Flash was never asked"
    assert r["leaked_slots"] == 0, r


def _off_params():
    for name in SITUATIONS:
        for stream in (False, True):
            if (name, stream) in NOT_APPLICABLE:
                continue
            marks = []
            if (name, stream) in KNOWN_GAPS_FALLBACK_OFF:
                marks.append(pytest.mark.xfail(strict=True, reason=KNOWN_GAPS_FALLBACK_OFF[(name, stream)]))
            yield pytest.param(name, stream, id=f"{name}-{'stream' if stream else 'nonstream'}", marks=marks)


@pytest.mark.parametrize("name,stream", list(_off_params()))
def test_fallback_off_never_a_silent_empty_success(name: str, stream: bool):
    r = run(SITUATIONS[name](), stream=stream, fallback=False, label=f"{name}|nofb")
    assert not is_silent_empty(r), r
    assert r["other_calls"] == 0, "fallback is off, Flash must not be called"
    assert r["leaked_slots"] == 0, r


def test_stall_after_first_frame_is_a_clean_stop_documented():
    """CHARACTERIZATION (F3): generate_stream() deliberately ends cleanly when upstream goes silent after
    content started (comment: rt=c keep-alive). The client sees finish_reason "stop" + partial text, with no
    error signal; a connection reset in the same spot is reported as an error. Pinned so a change is noticed."""
    r = run(sc.hang_after_first_frame("partial answer"), stream=True, fallback=True, label="stall")
    assert r["status"] == 200 and r["error_class"] == "", r
    assert r["finish_reason"] == "stop" and r["content"] == "partial answer", r
    assert r["leaked_slots"] == 0, r


def test_disconnect_mid_stream_is_reported():
    r = run(sc.disconnect_mid_stream(), stream=True, fallback=True, label="reset")
    assert r["error_class"] != "", r
    assert r["leaked_slots"] == 0, r


def test_long_silence_is_covered_by_keepalive():
    r = run(sc.silent_gap("late argon reply", gap=200), stream=True, fallback=True, label="gap200")
    assert r["status"] == 200 and r["error_class"] == "" and r["violations"] == [], r
    assert r["content"] == "late argon reply", r
    assert r["n_comments"] > 0, "no keepalive during a 200 s upstream silence"
