"""Argon -> Flash fallback (app/core/argon_fallback.py): one implementation, and no silent substitutions.

Covered:
- plan(): Argon by default; the stall breaker and ARGON_ALWAYS_FALLBACK are named substitutions;
- generate(): an upstream error / empty reply / Google web error degrades to the fallback model and marks the
  result with ``argon_fallback``; with the fallback off the original error propagates;
- generate_stream(): a dead-but-silent Argon stream gets exactly one plain Argon retry, then the fallback
  starts *behind a serve_marker frame* before any content -- and once content has streamed, nothing switches;
- conversation_id is forwarded to the pool (regression: the first version dropped it).
"""
import asyncio

import pytest

from app.config import settings
from app.core import argon_breaker, argon_fallback


def _run(coro):
    return asyncio.run(coro)


class _Pool:
    """Minimal account_pool stand-in: records the calls, replays scripted answers per model."""

    def __init__(self, non_stream=None, stream=None):
        self.calls = []
        self.stream_calls = []
        self._non_stream = list(non_stream or [])
        self._stream = list(stream or [])

    async def generate(self, prompt, model, conversation_id="", attachments=None,
                       gem_id=None, account_id=None, extended_thinking=False):
        self.calls.append({"model": model, "conversation_id": conversation_id, "thinking": extended_thinking})
        item = self._non_stream.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def generate_stream(self, prompt, model, conversation_id="", attachments=None,
                              gem_id=None, account_id=None, extended_thinking=False):
        self.stream_calls.append({"model": model, "conversation_id": conversation_id})
        for evt in self._stream.pop(0):
            if isinstance(evt, Exception):
                raise evt
            yield evt


_OK_ARGON = {"text": "argon answer", "images": [], "thoughts": "", "conversation_id": "c1"}
_EMPTY = {"text": "", "images": [], "thoughts": "", "conversation_id": "c1"}
_WEB_ERROR = {"text": "Sorry, something went wrong. Please try your request again.", "images": [],
              "thoughts": "", "conversation_id": "c1"}
_FLASH_OK = {"text": "flash answer", "images": [], "thoughts": "", "conversation_id": "c2"}


@pytest.fixture(autouse=True)
def _reset_breaker():
    argon_breaker.reset()
    yield
    argon_breaker.reset()


# ---- plan -----------------------------------------------------------------------------------------------

def test_plan_argon_by_default():
    model, is_argon, thinking, served = argon_fallback.plan("gemini-4-argon", "gemini-4-argon")
    assert (model, is_argon, thinking) == ("gemini-4-argon", True, False)
    assert served == argon_fallback.SERVED_BY_ARGON


def test_plan_names_the_stall_breaker_substitution(monkeypatch):
    monkeypatch.setattr(settings, "argon_stall_breaker_seconds", 60)
    argon_breaker.trip("first-frame", "TimeoutError")
    model, is_argon, thinking, served = argon_fallback.plan("gemini-4-argon", "gemini-4-argon")
    assert served == "stall_breaker"
    assert model == settings.argon_fallback_model
    assert (is_argon, thinking) == (False, True)


def test_plan_names_always_fallback(monkeypatch):
    monkeypatch.setattr(settings, "argon_always_fallback", True)
    _, _, _, served = argon_fallback.plan("gemini-4-argon", "gemini-4-argon")
    assert served == "argon_always_fallback"


# ---- generate -------------------------------------------------------------------------------------------

def test_generate_passes_argon_conversation_id_through():
    pool = _Pool(non_stream=[_OK_ARGON])
    result = _run(argon_fallback.generate(pool, "p", "gemini-4-argon", None,
                                          is_argon=True, conversation_id="gconv-9"))
    assert result["text"] == "argon answer"
    assert pool.calls[0]["conversation_id"] == "gconv-9"
    assert "argon_fallback" not in result


def test_generate_falls_back_and_marks_on_error():
    pool = _Pool(non_stream=[RuntimeError("upstream 500"), _FLASH_OK])
    result = _run(argon_fallback.generate(pool, "p", "gemini-4-argon", None, is_argon=True))
    assert result["text"] == "flash answer"
    assert result["argon_fallback"]["served_by"] == argon_fallback.SERVED_BY_FLASH
    assert result["argon_fallback"]["reason"] == "argon_error"
    assert pool.calls[1]["model"] == settings.argon_fallback_model
    assert pool.calls[1]["thinking"] is True


def test_generate_falls_back_on_empty_and_web_error_replies():
    for bad, reason in ((_EMPTY, "empty_reply"), (_WEB_ERROR, "google_web_error")):
        pool = _Pool(non_stream=[bad, _FLASH_OK])
        result = _run(argon_fallback.generate(pool, "p", "gemini-4-argon", None, is_argon=True))
        assert result["argon_fallback"]["reason"] == reason
        assert result["text"] == "flash answer"


def test_generate_raises_when_the_fallback_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "argon_fallback_to_flash", False)
    pool = _Pool(non_stream=[RuntimeError("boom")])
    with pytest.raises(RuntimeError, match="boom"):
        _run(argon_fallback.generate(pool, "p", "gemini-4-argon", None, is_argon=True))


def test_generate_raises_the_fallback_error_when_both_fail():
    pool = _Pool(non_stream=[RuntimeError("argon dead"), RuntimeError("flash dead too")])
    with pytest.raises(RuntimeError, match="flash dead too"):
        _run(argon_fallback.generate(pool, "p", "gemini-4-argon", None, is_argon=True))


def test_generate_never_returns_an_empty_completion_when_the_fallback_is_on():
    """An empty Argon reply must not reach the client as a successful completion."""
    pool = _Pool(non_stream=[_EMPTY, _EMPTY])
    result = _run(argon_fallback.generate(pool, "p", "gemini-4-argon", None, is_argon=True))
    assert result["text"] == ""          # both empty: nothing better exists, but it is marked...
    assert result["argon_fallback"]["reason"] == "empty_reply"   # ...so the routers/headers can say so


# ---- generate_stream ------------------------------------------------------------------------------------

def _collect(pool, **kw):
    async def run():
        return [evt async for evt in argon_fallback.generate_stream(pool, "p", "gemini-4-argon", None, **kw)]
    return _run(run())


def test_stream_argon_success_has_no_marker():
    pool = _Pool(stream=[[{"type": "delta", "text": "hi"},
                          {"type": "final", "text": "hi", "images": [], "thoughts": "",
                           "conversation_id": "c1"}]])
    events = _collect(pool, is_argon=True, conversation_id="g")
    assert [e["type"] for e in events] == ["delta", "final"]
    assert pool.stream_calls[0]["conversation_id"] == "g"


def test_stream_empty_answer_retries_argon_once_then_falls_back():
    pool = _Pool(
        stream=[[{"type": "final", "text": "", "images": [], "thoughts": "", "conversation_id": "c1"}]],
        non_stream=[_OK_ARGON],   # the single plain-Argon retry succeeds
    )
    events = _collect(pool, is_argon=True, conversation_id="g")
    assert [e["type"] for e in events] == ["delta", "thoughts", "final"]
    assert events[0]["text"] == "argon answer"
    assert not any(e["type"] == "serve_marker" for e in events), "a successful retry needs no marker"
    assert len(pool.calls) == 1, "exactly one non-streaming retry"


def test_stream_dead_answer_retries_then_emits_the_marker_before_flash_content():
    pool = _Pool(
        stream=[[{"type": "final", "text": "", "images": [], "thoughts": "", "conversation_id": "c1"}],
                [{"type": "delta", "text": "flash "}, {"type": "final", "text": "flash answer",
                                                       "images": [], "thoughts": "", "conversation_id": "c2"}]],
        non_stream=[_EMPTY],
    )
    events = _collect(pool, is_argon=True, conversation_id="g")
    assert [e["type"] for e in events] == ["serve_marker", "delta", "final"]
    assert events[0]["served_by"] == argon_fallback.SERVED_BY_FLASH
    assert events[0]["reason"] == "empty_stream"
    assert pool.stream_calls[-1]["model"] == settings.argon_fallback_model


def test_stream_error_before_any_content_falls_back_behind_the_marker():
    pool = _Pool(
        stream=[[RuntimeError("connect reset")],
                [{"type": "final", "text": "flash answer", "images": [], "thoughts": "",
                  "conversation_id": "c2"}]],
    )
    events = _collect(pool, is_argon=True)
    assert events[0]["type"] == "serve_marker"
    assert events[0]["reason"] == "argon_error"


def test_stream_never_switches_after_content_was_streamed():
    pool = _Pool(stream=[[{"type": "delta", "text": "half an ans"}, RuntimeError("connection lost")]])
    with pytest.raises(RuntimeError, match="connection lost"):
        _collect(pool, is_argon=True)
    assert len(pool.stream_calls) == 1, "no silent second attempt after content went out"
