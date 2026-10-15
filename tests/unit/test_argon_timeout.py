import asyncio
import pytest
import app.core.gemini_client as gc


class _Jar:
    def get_all(self):
        return {}


def test_argon_timeout_constants():
    assert gc._GENERATE_TIMEOUT_ARGON == 360
    assert gc._GENERATE_TIMEOUT_DEFAULT == 180


def test_argon_send_request_uses_argon_timeout(monkeypatch):
    client = gc.GeminiWebClient(psid="psid", psidts="psidts")
    client._cookie_jar = _Jar()
    client._current_target = gc.header_builder.get_impersonate_target()
    client._session_token = "token"
    captured_timeout = None

    class FakeSession:
        async def post(self, url, **kwargs):
            nonlocal captured_timeout
            captured_timeout = kwargs.get("timeout")
            raise RuntimeError("stop test")

    client._http = FakeSession()

    with pytest.raises(RuntimeError, match="stop test"):
        asyncio.run(client._send_request("prompt", model="gemini-4-argon"))

    assert captured_timeout == gc._GENERATE_TIMEOUT_ARGON


def test_non_argon_send_request_uses_default_timeout(monkeypatch):
    client = gc.GeminiWebClient(psid="psid", psidts="psidts")
    client._cookie_jar = _Jar()
    client._current_target = gc.header_builder.get_impersonate_target()
    client._session_token = "token"
    captured_timeout = None

    class FakeSession:
        async def post(self, url, **kwargs):
            nonlocal captured_timeout
            captured_timeout = kwargs.get("timeout")
            raise RuntimeError("stop test")

    client._http = FakeSession()

    with pytest.raises(RuntimeError, match="stop test"):
        asyncio.run(client._send_request("prompt", model="gemini-2.5-pro"))

    assert captured_timeout == gc._GENERATE_TIMEOUT_DEFAULT


def test_argon_generate_stream_uses_argon_timeout(monkeypatch):
    client = gc.GeminiWebClient(psid="psid", psidts="psidts")
    client._cookie_jar = _Jar()
    client._current_target = gc.header_builder.get_impersonate_target()
    client._session_token = "token"
    client._healthy = True

    captured_timeout = None

    class FakeStreamCtx:
        def __init__(self, **kwargs):
            nonlocal captured_timeout
            captured_timeout = kwargs.get("timeout")

        async def __aenter__(self):
            raise RuntimeError("stop stream test")

        async def __aexit__(self, *args):
            pass

    class FakeSession:
        def stream(self, method, url, **kwargs):
            return FakeStreamCtx(**kwargs)

    client._http = FakeSession()

    async def _drain():
        async for _ in client.generate_stream("hi", model="gemini-4-argon"):
            pass

    with pytest.raises(RuntimeError, match="stop stream test"):
        asyncio.run(_drain())

    assert captured_timeout == gc._GENERATE_TIMEOUT_ARGON
