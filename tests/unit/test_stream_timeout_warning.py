"""generate_stream() logs a WARNING when its per-chunk timeout ends a stream.

If Google keeps the HTTP/2 stream open after the final frame (rt=c keep-alive),
generate_stream() deliberately finishes cleanly once content has been emitted.
That is indistinguishable from a real mid-reply stall, so the break must leave a
trace in the log. With nothing emitted yet the timeout must still propagate and
must not be logged as a clean end.
"""
import asyncio
import logging

import pytest

import app.core.gemini_client as gc


class _HangingResponse:
    status_code = 200
    headers: dict = {}

    async def aiter_content(self):
        yield b"x"
        await asyncio.Event().wait()  # never delivers a second chunk

    async def atext(self):
        return ""


class _StreamCtx:
    async def __aenter__(self):
        return _HangingResponse()

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, *args, **kwargs):
        pass

    def stream(self, *args, **kwargs):
        return _StreamCtx()

    async def close(self):
        pass


class _Jar:
    def set(self, *a, **k):
        pass

    def get_all(self):
        return {}

    def update_from_response(self, resp):
        pass


def _make_client(monkeypatch, frame_text):
    monkeypatch.setattr(gc, "AsyncSession", _FakeSession)

    async def _no_jitter(*a, **k):
        return None

    monkeypatch.setattr(gc, "apply_jitter", _no_jitter)

    # The first chunk yields one frame; its text is what generate_stream emits.
    monkeypatch.setattr(gc, "_scan_complete_wrb_frames", lambda buf: (["frame"], len(buf)))
    monkeypatch.setattr(gc, "_extract_text_from_wrb", lambda elem: (frame_text, "", ""))

    # Make the second wait_for (the one that would hang) time out immediately.
    real_wait_for = asyncio.wait_for
    calls = {"n": 0}

    async def fake_wait_for(aw, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_wait_for(aw, timeout)
        close = getattr(aw, "close", None)
        if close:
            close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(gc.asyncio, "wait_for", fake_wait_for)

    client = gc.GeminiWebClient(psid="psid", psidts="psidts")
    client._cookie_jar = _Jar()
    client._http = _FakeSession()
    client._session_token = "TOKEN"
    client._healthy = True
    client._current_target = gc.header_builder.get_impersonate_target()

    async def _noop(*a, **k):
        return None

    async def _finalize(*a, **k):
        return {"type": "final", "text": "", "conversation_id": "", "images": []}

    monkeypatch.setattr(client, "_send_heartbeat", _noop)
    monkeypatch.setattr(client, "_ensure_refresh_task", lambda: None)
    monkeypatch.setattr(client, "_finalize_stream", _finalize)
    monkeypatch.setattr(client, "_encode_payload_thinking", lambda *a, **k: ("{}", None))
    return client


def _drain(client):
    model = next(iter(gc.GEMINI_MODELS))

    async def run():
        events = []
        async for ev in client.generate_stream("hi", model):
            events.append(ev)
        return events

    return asyncio.run(run())


def test_timeout_after_content_ends_cleanly_and_logs_warning(monkeypatch, caplog):
    client = _make_client(monkeypatch, frame_text="hello")
    with caplog.at_level(logging.WARNING, logger=gc.logger.name):
        events = _drain(client)

    assert events and events[-1]["type"] == "final"  # still a clean finish
    warnings = [r for r in caplog.records if "no data for" in r.getMessage()]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "emitted_chars=5" in msg  # len("hello")


def test_timeout_before_any_content_still_raises_without_warning(monkeypatch, caplog):
    client = _make_client(monkeypatch, frame_text="")
    with caplog.at_level(logging.WARNING, logger=gc.logger.name):
        with pytest.raises(Exception) as excinfo:
            _drain(client)

    assert gc._is_timeout_error(excinfo.value) or isinstance(excinfo.value, asyncio.TimeoutError)
    assert not [r for r in caplog.records if "no data for" in r.getMessage()]
