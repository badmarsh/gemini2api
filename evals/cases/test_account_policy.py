"""T1 cases: account-pool policy ACROSS requests: captcha / 429 streaks and cookie expiry.

Single-request behaviour is covered elsewhere (test_argon_fallback*.py). These cases fire several requests at one
in-process server and look at what the pool learns from them, because the pool keeps state between requests:
``consecutive_failures`` (3 strikes -> EXPIRED), cooldowns, ``is_healthy``.

CHARACTERIZATION cases pin current policy so a change is noticed; they are labelled as such. The 3-strike rule is
worth knowing about: a captcha (HTTP 429) is neither retried on another account nor cooled down, but it DOES count as
a strike, so a short captcha episode can take every account out of rotation.

Run:  py -3.14 -m pytest evals/cases/test_account_policy.py -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from evals.cases.helpers import plain_request, send
from evals.cases.test_argon_fallback import FLASH_TEXT, MODEL
from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT
from evals.runner import is_silent_empty

FLASH_MODEL = "gemini-3-flash"
STREAKS = (False, True)


def _sid(stream: bool) -> str:
    return "stream" if stream else "nonstream"


async def _post(client: httpx.AsyncClient, model: str, stream: bool = False) -> dict:
    path, body = plain_request("chat", model, stream, "hi")
    return await send(client, "chat", path, body, stream, label="policy")


def _accounts(sut) -> list[tuple[str, str, int]]:
    return [(a.id, a.status.value, a.consecutive_failures) for a in sut.pool.accounts]


def _no_healthy_msg() -> str:
    from app.core.gemini_client import NO_HEALTHY_ACCOUNT_MSG  # lazy: app.* must import after the harness set the env

    return NO_HEALTHY_ACCOUNT_MSG


def _error_message(rec: dict) -> str:
    return str((rec.get("error_body") or {}).get("error", {}).get("message", ""))


# ---- captcha / 429 -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("stream", STREAKS, ids=[_sid(s) for s in STREAKS])
def test_argon_captcha_streak_keeps_serving_flash_without_amplification(stream: bool):
    """Every request of a captcha streak ends as a clean Flash answer, costs exactly ONE Argon call (429 is not
    retried or failed over), and never takes the whole pool down."""
    n = 8

    async def go():
        async with InProcessSUT(accounts=2) as sut:
            sut.upstream.default("argon", sc.captcha())
            sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
                recs = [await _post(client, MODEL, stream) for _ in range(n)]
            await asyncio.sleep(0.05)
            return recs, len(sut.upstream.calls_for("argon")), _accounts(sut), sut.active_slots()

    recs, argon_calls, accounts, slots = asyncio.run(go())
    for i, r in enumerate(recs, 1):
        assert r["status"] == 200 and r["error_class"] == "" and r["violations"] == [], (i, r)
        assert r["content"] == FLASH_TEXT, (i, r)
    assert argon_calls == n, f"{argon_calls} Argon calls for {n} requests: a captcha must cost one call each"
    assert any(status == "active" for _, status, _ in accounts), accounts
    assert slots == 0


def test_three_consecutive_429s_expire_the_account_characterization():
    """CHARACTERIZATION: strikes are consecutive failures of any non-5xx kind. After three 429s the only account is
    EXPIRED and the client gets 503 'No healthy account' instead of the upstream's 429 (until a re-check succeeds)."""
    async def go():
        async with InProcessSUT(accounts=1) as sut:
            sut.upstream.default("other", sc.captcha())
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
                recs, states = [], []
                for _ in range(4):
                    recs.append(await _post(client, FLASH_MODEL))
                    states.append(_accounts(sut)[0])
            await asyncio.sleep(0.05)
            return recs, states, sut.active_slots()

    recs, states, slots = asyncio.run(go())
    assert [r["status"] for r in recs] == [429, 429, 429, 503], recs
    assert [s[2] for s in states[:3]] == [1, 2, 3] and states[2][1] == "expired", states
    assert _no_healthy_msg() in _error_message(recs[3]), recs[3]
    assert slots == 0


def test_a_success_resets_the_strike_counter():
    """Strikes must be CONSECUTIVE: captcha / ok / captcha / ok ... never expires the account."""
    async def go():
        async with InProcessSUT(accounts=1) as sut:
            for _ in range(3):
                sut.upstream.program("other", sc.captcha(), sc.ok("fine", n_frames=3))
            sut.upstream.default("other", sc.ok("fine", n_frames=3))
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
                recs = [await _post(client, FLASH_MODEL) for _ in range(6)]
            return recs, _accounts(sut)

    recs, accounts = asyncio.run(go())
    assert [r["status"] for r in recs] == [429, 200, 429, 200, 429, 200], recs
    assert accounts == [("acct0", "active", 0)], accounts


def test_expired_account_is_revalidated_and_serves_again_without_restart():
    """After the strikes expire the only account, the next acquire() re-checks it (check_account); once the upstream
    is fine again the pool serves immediately, with no restart. ``check_account`` is stubbed: the fake session has
    no real login page to fetch."""
    async def go():
        async with InProcessSUT(accounts=1) as sut:
            sut.upstream.default("other", sc.captcha())
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
                for _ in range(3):
                    await _post(client, FLASH_MODEL)
                assert _accounts(sut)[0][1] == "expired"
                blocked = await _post(client, FLASH_MODEL)

                async def _valid():
                    return {"valid": True}

                sut.pool.accounts[0].client.check_account = _valid
                sut.upstream.default("other", sc.ok("back again", n_frames=3))
                back = await _post(client, FLASH_MODEL)
            return blocked, back, _accounts(sut)

    blocked, back, accounts = asyncio.run(go())
    assert blocked["status"] == 503, blocked
    assert back["status"] == 200 and back["content"] == "back again", back
    assert accounts == [("acct0", "active", 0)], accounts


@pytest.mark.parametrize("stream", STREAKS, ids=[_sid(s) for s in STREAKS])
def test_captcha_without_fallback_is_reported_not_swallowed(stream: bool):
    """A Flash request has no fallback: its 429 must reach the client as an error signal, never as an empty success.
    (Non-stream: HTTP 429. Stream: HTTP 200 + an in-stream error, since the headers are already out.)"""
    async def go():
        async with InProcessSUT(accounts=2) as sut:
            sut.upstream.default("other", sc.captcha())
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=60) as client:
                rec = await _post(client, FLASH_MODEL, stream)
            await asyncio.sleep(0.05)
            return rec, sut.active_slots()

    r, slots = asyncio.run(go())
    assert r["error_class"] != "" and not is_silent_empty(r), r
    assert r["content"] == "", r
    if not stream:
        assert r["status"] == 429, r
    assert slots == 0


# ---- cookie expiry -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("stream", STREAKS, ids=[_sid(s) for s in STREAKS])
def test_signin_page_reply_streak_costs_one_argon_call_each_characterization(stream: bool):
    """CHARACTERIZATION: when Google answers the generate call with the sign-in page (cookie expired mid-flight),
    the reply has no frames and is handled like an empty one. Each request still ends as a clean Flash answer, but
    the pool never learns: accounts stay ACTIVE with zero strikes, so every later request pays the same wasted
    Argon call(s) until a heal / refresh happens elsewhere. Argon calls per request: 1 non-streaming; 2 streaming
    (the stream plus the single plain Argon retry that argon_fallback makes before swapping to Flash)."""
    n = 6

    async def go():
        async with InProcessSUT(accounts=2) as sut:
            sut.upstream.default("argon", sc.cookie_expired_redirect())
            sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
                recs = [await _post(client, MODEL, stream) for _ in range(n)]
            await asyncio.sleep(0.05)
            return recs, len(sut.upstream.calls_for("argon")), _accounts(sut), sut.active_slots()

    recs, argon_calls, accounts, slots = asyncio.run(go())
    for i, r in enumerate(recs, 1):
        assert r["status"] == 200 and r["error_class"] == "" and r["content"] == FLASH_TEXT, (i, r)
    assert argon_calls == (2 * n if stream else n), argon_calls
    assert all(status == "active" and strikes == 0 for _, status, strikes in accounts), accounts
    assert slots == 0


@pytest.mark.parametrize("stream", STREAKS, ids=[_sid(s) for s in STREAKS])
@pytest.mark.parametrize("model", [MODEL, FLASH_MODEL])
def test_all_cookies_dead_is_a_clean_error_and_recovers_without_restart(model: str, stream: bool):
    """Both accounts report unhealthy (cookie expiry detected by the refresher/heartbeat): the client gets an honest
    'No healthy account' error, not a hang, a 500 or an empty answer, no slot leaks, and the very next request after
    the cookies are fixed succeeds with no restart. Latency is deliberately not asserted: the self-heal attempt runs
    against a fake session here."""
    async def go():
        async with InProcessSUT(accounts=2) as sut:
            sut.upstream.default("argon", sc.ok("argon reply", n_frames=3))
            sut.upstream.default("other", sc.ok("flash reply", n_frames=3))
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
                for a in sut.pool.accounts:
                    a.client._healthy = False
                dead = [await _post(client, model, stream) for _ in range(2)]
                slots_dead = sut.active_slots()
                for a in sut.pool.accounts:
                    a.client._healthy = True
                back = await _post(client, model, stream)
            await asyncio.sleep(0.05)
            return dead, slots_dead, back, sut.active_slots()

    dead, slots_dead, back, slots = asyncio.run(go())
    for r in dead:
        assert r["error_class"] != "" and not is_silent_empty(r), r
        assert r["content"] == "", r
        if not stream:
            assert r["status"] == 503 and _no_healthy_msg() in _error_message(r), r
    assert slots_dead == 0 and slots == 0
    assert back["status"] == 200 and back["error_class"] == "" and back["content"], back


def test_one_dead_cookie_does_not_hurt_clients():
    """With one of two accounts unhealthy, requests keep being served (by the healthy one) from Argon itself."""
    async def go():
        async with InProcessSUT(accounts=2) as sut:
            sut.upstream.default("argon", sc.ok("argon reply", n_frames=3))
            sut.upstream.default("other", sc.ok(FLASH_TEXT, n_frames=3))
            async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=120) as client:
                sut.pool.accounts[0].client._healthy = False
                recs = [await _post(client, MODEL) for _ in range(4)]
            await asyncio.sleep(0.05)
            return recs, len(sut.upstream.calls_for("argon")), len(sut.upstream.calls_for("other")), sut.active_slots()

    recs, argon_calls, other_calls, slots = asyncio.run(go())
    for r in recs:
        assert r["status"] == 200 and r["error_class"] == "" and r["content"] == "argon reply", r
    assert argon_calls == 4 and other_calls == 0, (argon_calls, other_calls)
    assert slots == 0
