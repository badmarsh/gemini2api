"""T1 cases: the Argon -> Flash fallback contract on /v1/responses (the endpoint the Codex CLI uses).

Same contract as /v1/chat/completions (see test_argon_fallback.py): with ``argon_fallback_to_flash`` on, an Argon
failure / hang / empty reply / web-error text degrades to Flash and the client sees one clean 200 answer; with it
off Flash is never called and the failure is reported, not turned into an empty completion.

Run:  py -3.14 -m pytest evals/cases/test_responses_endpoint.py -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from evals.cases.test_argon_fallback import FLASH_TEXT, MODEL, NOT_APPLICABLE, SITUATIONS
from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT
from evals.runner import RequestSpec, is_silent_empty, run_request

# Open gap, same xfail(strict) convention as test_argon_fallback.KNOWN_GAPS_FALLBACK_OFF.
SILENT_EMPTY_REASON = (
    "argon_fallback_to_flash off: /v1/responses finishes with status 'completed' and no output text. Only the Argon "
    "fallback in app/routers/responses.py inspects is_empty_result(); nothing turns an empty result into an error."
)
KNOWN_GAPS_FALLBACK_OFF: dict = {
    (name, stream): SILENT_EMPTY_REASON
    for name in ("empty_reply", "no_frames", "cookie_expired", "malformed_frame", "truncated_frame")
    for stream in (False, True)
}


def _spec(stream: bool, label: str = "responses", model: str = MODEL) -> RequestSpec:
    body = {"model": model, "stream": stream, "input": "say hello"}
    return RequestSpec("/v1/responses", body, stream=stream, timeout_s=30, label=label)


async def _one(argon_scenario, *, stream: bool, fallback: bool = True, model: str = MODEL,
               other=None) -> dict:
    async with InProcessSUT(accounts=2) as sut:
        sut.upstream.default("argon", argon_scenario)
        sut.upstream.default("other", other or sc.ok(FLASH_TEXT, n_frames=3))
        async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=60) as client:
            with sut.settings(argon_fallback_to_flash=fallback):
                rec = await run_request(client, _spec(stream, model=model))
        await asyncio.sleep(0.05)  # let server-side cleanup run before counting slots
        rec["argon_calls"] = len(sut.upstream.calls_for("argon"))
        rec["other_calls"] = len(sut.upstream.calls_for("other"))
        rec["leaked_slots"] = sut.active_slots()
        return rec


def run(scenario, **kw) -> dict:
    return asyncio.run(_one(scenario, **kw))


def _sid(stream: bool) -> str:
    return "stream" if stream else "nonstream"


def _params(gaps: dict | None = None):
    for name in SITUATIONS:
        for stream in (False, True):
            if (name, stream) in NOT_APPLICABLE:
                continue
            marks = []
            if gaps and (name, stream) in gaps:
                marks.append(pytest.mark.xfail(strict=True, reason=gaps[(name, stream)]))
            yield pytest.param(name, stream, id=f"{name}-{_sid(stream)}", marks=marks)


@pytest.mark.parametrize("name,stream", list(_params()))
def test_fallback_on_serves_flash_cleanly(name: str, stream: bool):
    r = run(SITUATIONS[name](), stream=stream, fallback=True)
    assert r["status"] == 200, r
    assert r["error_class"] == "", r
    assert r["violations"] == [], r
    assert r["content"] == FLASH_TEXT, r
    if not stream:
        assert r["finish_reason"] == "completed", r
    assert r["other_calls"] >= 1, "Flash was never asked"
    assert r["leaked_slots"] == 0, r


@pytest.mark.parametrize("name,stream", list(_params()))
def test_fallback_off_never_calls_flash(name: str, stream: bool):
    r = run(SITUATIONS[name](), stream=stream, fallback=False)
    assert r["other_calls"] == 0, "fallback is off, Flash must not be called"
    assert r["leaked_slots"] == 0, r


@pytest.mark.parametrize("name,stream", list(_params(KNOWN_GAPS_FALLBACK_OFF)))
def test_fallback_off_never_a_silent_empty_success(name: str, stream: bool):
    r = run(SITUATIONS[name](), stream=stream, fallback=False)
    assert not is_silent_empty(r), r


def test_stall_after_first_frame_is_a_clean_stop_documented():
    """CHARACTERIZATION (F3, as on chat): once content started, an upstream stall ends the stream cleanly with the
    partial text and no error, and Flash is not asked (it would replace content the client already has)."""
    r = run(sc.hang_after_first_frame("partial answer"), stream=True, fallback=True)
    assert r["status"] == 200 and r["error_class"] == "" and r["violations"] == [], r
    assert r["content"] == "partial answer", r
    assert r["other_calls"] == 0, r
    assert r["leaked_slots"] == 0, r


def test_long_silence_is_covered_by_keepalive():
    """Argon's first token takes 120-260 s: an idle SSE stream must carry keepalives or a proxy will cut it."""
    r = run(sc.silent_gap("late argon reply", gap=200), stream=True, fallback=True)
    assert r["status"] == 200 and r["error_class"] == "" and r["violations"] == [], r
    assert r["content"] == "late argon reply", r
    assert r["n_comments"] > 0, "no keepalive during a 200 s upstream silence"


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
def test_non_argon_model_gets_no_argon_fallback(stream: bool):
    """A Flash request whose upstream fails must be reported, not retried on Argon (and Argon is never called)."""
    r = run(sc.ok("argon reply", n_frames=3), stream=stream, model="gemini-3-flash",
            other=sc.http_error(500, "internal"))
    assert r["argon_calls"] == 0, r
    assert r["content"] != "argon reply", r
    assert r["leaked_slots"] == 0, r
