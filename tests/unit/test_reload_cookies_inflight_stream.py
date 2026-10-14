"""reload_cookies() must not abort streams that are already in flight.

generate_stream() captures the shared session (self._http) for the whole
response. The background cookie refresh calls reload_cookies(), which used to
close that very session, killing every active stream. The new session must be
swapped in immediately, and the old one closed only once its last in-flight
user has released it.
"""
import asyncio

import app.core.gemini_client as gc


class _FakeResponse:
    status_code = 200
    headers: dict = {}

    def __init__(self, gate: asyncio.Event, session: "_FakeSession"):
        self._gate = gate
        self._session = session

    async def aiter_content(self):
        yield b""
        await self._gate.wait()
        # A real aborted stream raises here once the session is closed.
        if self._session.closed:
            raise RuntimeError("session closed while stream in flight")
        yield b""

    async def atext(self):
        return ""


class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    instances: list = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        self.gate = asyncio.Event()
        _FakeSession.instances.append(self)

    def stream(self, *args, **kwargs):
        return _StreamCtx(_FakeResponse(self.gate, self))

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

    async def _finalize(*a, **k):
        return {"type": "final", "text": "", "conversation_id": "", "images": []}

    monkeypatch.setattr(client, "_obtain_session_token", _obtain)
    monkeypatch.setattr(client, "_send_heartbeat", _noop)
    monkeypatch.setattr(client, "_ensure_refresh_task", lambda: None)
    monkeypatch.setattr(client, "_finalize_stream", _finalize)
    monkeypatch.setattr(client, "_encode_payload_thinking", lambda *a, **k: ("{}", None))
    return client


def test_reload_cookies_does_not_close_session_used_by_inflight_stream(monkeypatch):
    client = _make_client(monkeypatch)
    old_session = client._http
    model = next(iter(gc.GEMINI_MODELS))

    async def scenario():
        events = []

        async def consume():
            async for ev in client.generate_stream("hi", model):
                events.append(ev)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # stream is now parked on its second chunk

        result = await client.reload_cookies()
        assert result == {"success": True}
        assert client._http is not old_session, "reload must swap in a new session"
        assert old_session.closed is False, "old session closed under an in-flight stream"

        old_session.gate.set()  # let the in-flight stream finish
        await asyncio.wait_for(task, timeout=2)
        # Once the last user released it, the old session must not leak.
        await asyncio.sleep(0)
        assert old_session.closed is True
        return events

    events = asyncio.run(scenario())
    assert events and events[-1]["type"] == "final"


def test_fingerprint_rotation_does_not_close_session_used_by_inflight_stream(monkeypatch):
    client = _make_client(monkeypatch)
    old_session = client._http
    model = next(iter(gc.GEMINI_MODELS))

    async def scenario():
        async def consume():
            async for _ in client.generate_stream("hi", model):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # stream is now parked on its second chunk

        # Simulate a TLS fingerprint rotation while the stream is in flight.
        monkeypatch.setattr(gc.header_builder, "get_impersonate_target", lambda: "rotated-target")
        await client._ensure_session_current()
        assert client._http is not old_session, "rotation must swap in a new session"
        assert old_session.closed is False, "old session closed under an in-flight stream"

        old_session.gate.set()
        await asyncio.wait_for(task, timeout=2)
        await asyncio.sleep(0)
        assert old_session.closed is True

    asyncio.run(scenario())