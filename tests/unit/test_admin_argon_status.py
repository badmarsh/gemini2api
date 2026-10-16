"""/admin/status must explain an Argon slowdown, and /admin/argon/reset must be able to end it.

Before this, "Argon is slow / answers nothing" had no answer in the panel: the stall breaker lived in a module
nobody could see, the agent-loop counter was only in the logs, and the only way out of an open breaker was to
wait out ARGON_STALL_BREAKER_SECONDS.
"""
from app.config import settings
from app.core import agent_loop, argon_breaker
from app.utils.tools import TOOL_LOOP_REMINDER

_AUTH = {"Authorization": "Bearer sk-test-key", "X-Admin-Key": "sk-test-key"}


def _get(client, path):
    return client.get(path, headers=_AUTH)


def _post(client, path):
    return client.post(path, headers=_AUTH)


def test_status_reports_the_argon_and_agent_loop_state(client, monkeypatch):
    argon_breaker.reset()
    agent_loop.reset_stats()
    monkeypatch.setattr(settings, "argon_stall_breaker_seconds", 60)
    argon_breaker.trip("first-frame", "TimeoutError")

    body = _get(client, "/admin/status").json()
    argon = body["argon"]
    assert argon["open"] is True
    assert argon["reason"] == "first-frame"
    assert argon["note"] == "TimeoutError"
    assert 0 < argon["seconds_left"] <= 60
    assert argon["trips"] >= 1

    loop = body["agent_loop"]
    assert loop["detected"] == 0 and loop["corrected"] == 0
    assert loop["tool_loop_reminder"] is True
    assert loop["stall_guard"] is True
    assert loop["fallback_model"] == settings.argon_fallback_model
    assert "salvaged_empty_streams" in loop
    argon_breaker.reset()


def test_reset_closes_the_breaker(client, monkeypatch):
    monkeypatch.setattr(settings, "argon_stall_breaker_seconds", 60)
    argon_breaker.trip("timeout", "")
    assert argon_breaker.is_open()

    body = _post(client, "/admin/argon/reset").json()
    assert body["success"] is True
    assert body["was_open"] is True
    assert body["argon"]["open"] is False
    assert argon_breaker.is_open() is False


def test_breaker_with_a_zero_ttl_stays_closed_but_still_counts_trips(monkeypatch):
    """ARGON_STALL_BREAKER_SECONDS=0 disables the substitution, not the observability."""
    argon_breaker.reset()
    monkeypatch.setattr(settings, "argon_stall_breaker_seconds", 0)
    assert argon_breaker.enabled() is False
    argon_breaker.trip("timeout", "")
    assert argon_breaker.is_open() is False
    snap = argon_breaker.snapshot()
    assert snap["trips"] >= 1 and snap["open"] is False
    argon_breaker.reset()


def test_reminder_text_is_the_one_the_model_actually_sees():
    """Guard against editing the reminder into something that no longer forbids promises."""
    low = TOOL_LOOP_REMINDER.lower()
    assert "tool_call" in low
    assert "promise" in low
