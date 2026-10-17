"""Unit tests for saw_frame protocol drift detection (item L)."""

import pytest
from unittest.mock import AsyncMock
from app.core.gemini_client import GoogleWebError
try:
    from app.core.gemini_client import ProtocolDriftError
except ImportError:
    ProtocolDriftError = None

_AUTH = {"Authorization": "Bearer sk-test-key"}


def test_protocol_drift_error_class_exists():
    assert ProtocolDriftError is not None
    assert issubclass(ProtocolDriftError, GoogleWebError)


@pytest.mark.asyncio
async def test_generate_stream_raises_protocol_drift_when_bytes_but_no_frames(monkeypatch):
    from app.core.gemini_client import GeminiWebClient

    client = GeminiWebClient(psid="test", psidts="test")
    client._healthy = True
    client._session_token = "fake-token"

    class FakeStreamResponse:
        status_code = 200

        async def atext(self):
            return "fake"

        async def aiter_content(self):
            # Yields raw bytes that never form a valid wrb.fr frame
            yield b")]}'\n\n1234\n[[[\"unparseable_google_bytes\"]]]\n"

    class FakeAsyncContextManager:
        async def __aenter__(self):
            return FakeStreamResponse()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class FakeSession:
        def stream(self, *args, **kwargs):
            return FakeAsyncContextManager()

    monkeypatch.setattr(client, "_lease_http", lambda: FakeSession())
    monkeypatch.setattr(client, "_release_http", AsyncMock())

    with pytest.raises(ProtocolDriftError) as exc_info:
        async for _ in client.generate_stream("hello", "gemini-flash"):
            pass

    assert "protocol drift" in str(exc_info.value).lower()


def test_stream_protocol_drift_classifies_as_protocol_drift_in_debug_metrics(gem_client, monkeypatch):
    from app.routers import openai as openai_router
    from app.core.gemini_client import ProtocolDriftError
    from app.core.debug_metrics import DebugMetricsStore
    from app import main as app_main

    debug_store = DebugMetricsStore(capacity=20)
    app_main.debug_metrics = debug_store

    async def fake_failing_stream(*args, **kwargs):
        raise ProtocolDriftError("Google web error: received 123 bytes but zero parseable WRB frames (protocol drift)")
        yield {"type": "delta", "text": "never reached"}

    monkeypatch.setattr(openai_router.gemini_client, "generate_stream", fake_failing_stream)

    resp = gem_client.post(
        "/v1/chat/completions",
        headers={**_AUTH, "Content-Type": "application/json"},
        json={"model": "gemini-flash", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert resp.status_code == 200  # Streaming response connection starts at 200

    # Ensure the debug metric record captured error_class as protocol_drift
    records = debug_store.records(limit=1)
    assert len(records) == 1
    assert records[0]["error_class"] == "protocol_drift"
