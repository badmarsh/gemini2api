"""reload_cookies() must not abort NON-stream requests that are already in flight.

The stream path leases the shared HTTP session (see test_reload_cookies_inflight_stream.py), but the
non-stream ``_send_request`` posted through ``self._http`` directly. ``reload_cookies`` swaps the
session and retires the old one immediately when nobody holds a lease, so an in-flight
``/v1/chat/completions`` (non-stream) lost its connection the moment a cookie push reloaded that
account. Live evidence (2026-10-17 18:13 UTC): every chat in flight on the account being reloaded
answered 502, within milliseconds of that account's PUT finishing.
"""
import asyncio

import app.core.gemini_client as gc


class _FakeResponse:
    status_code = 200
    text = ""


class _FakeSession:
    instances: list = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        self.gate = asyncio.Event()
        _FakeSession.instances.append(self)

    async def post(self, *args, **kwargs):
        await self.gate.wait()
        # A real request on a closed session fails here.
        if self.closed:
            raise RuntimeError("session closed while request in flight")
        return _FakeResponse()

    async def close(self):
        self.closed = True


class _Jar:
    def set(self, *a, **k):
        pass

    def get_all(self):
        return {}

    def update_from_response(self, resp):
        pass


def _make_client(monkeypatch):
    _FakeSession.instances = []
    monkeypatch.setattr(gc, "AsyncSession", _FakeSession)

    async def _no_jitter(*a, **k):
        return None

    monkeypatch.setattr(gc, "apply_jitter", _no_jitter)

    client = gc.GeminiWebClient(psid="psid", psidts="psidts")
    client._cookie_jar = _Jar()
    client._http = _FakeSession()
    client._session_token = "TOKEN"
    client._healthy = True
    client._current_target = gc.header_builder.get_impersonate_target()

    async def _obtain():
        client._session_token = "NEW-TOKEN"

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(client, "_obtain_session_token", _obtain)
    monkeypatch.setattr(client, "_send_heartbeat", _noop)
    monkeypatch.setattr(client, "_ensure_refresh_task", lambda: None)
    monkeypatch.setattr(client, "_encode_payload_thinking", lambda *a, **k: ("{}", "uuid"))
    monkeypatch.setattr(client, "_parse_output", lambda text: {"text": "pong"})
    return client


def test_reload_cookies_does_not_close_session_used_by_inflight_request(monkeypatch):
    client = _make_client(monkeypatch)
    old_session = client._http
    model = next(iter(gc.GEMINI_MODELS))

    async def scenario():
        task = asyncio.create_task(client._send_request("hi", model))
        await asyncio.sleep(0.05)  # the POST is now parked on the old session

        result = await client.reload_cookies()
        assert result == {"success": True}
        assert client._http is not old_session, "reload must swap in a new session"
        assert old_session.closed is False, "old session closed under an in-flight request"

        old_session.gate.set()  # let the in-flight request finish
        answer = await asyncio.wait_for(task, timeout=2)
        # Once the last user released it, the old session must not leak.
        await asyncio.sleep(0)
        assert old_session.closed is True
        return answer

    assert asyncio.run(scenario()) == {"text": "pong"}


def test_fingerprint_rotation_does_not_close_session_used_by_inflight_request(monkeypatch):
    client = _make_client(monkeypatch)
    old_session = client._http
    model = next(iter(gc.GEMINI_MODELS))

    async def scenario():
        task = asyncio.create_task(client._send_request("hi", model))
        await asyncio.sleep(0.05)

        monkeypatch.setattr(gc.header_builder, "get_impersonate_target", lambda: "rotated-target")
        await client._ensure_session_current()
        assert client._http is not old_session, "rotation must swap in a new session"
        assert old_session.closed is False, "old session closed under an in-flight request"

        old_session.gate.set()
        answer = await asyncio.wait_for(task, timeout=2)
        await asyncio.sleep(0)
        assert old_session.closed is True
        return answer

    assert asyncio.run(scenario()) == {"text": "pong"}
