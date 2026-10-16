"""StreamGenerate payload/header invariants the Argon rework got wrong.

1. **Companion UUID pairing.** ``_encode_payload_thinking`` mints one per-request UUID, stores it at
   ``inner[59]`` and returns it; the ``x-goog-ext-525005358-jspb`` header must echo that SAME UUID. The Argon
   rewrite threw the returned value away (``encoded, _ = ...``) and minted a second, unrelated UUID for the
   header — upstream paired them, and nothing documented the change. One invariant test per call site.
2. **No server-side continuation.** ``inner[2]`` is the metadata block, not a conversation slot, so a stored
   ``conversation_id`` cannot be honoured. That is reported once instead of being discovered by a user whose
   agent suddenly answers without context.
"""
import asyncio
import json

import pytest

import app.core.gemini_client as gc


class _Jar:
    def get_all(self):
        return {}

    def set(self, *a, **k):
        return None

    def update_from_response(self, *a, **k):
        return None


def _client():
    c = gc.GeminiWebClient(psid="psid", psidts="psidts")
    c._cookie_jar = _Jar()
    c._current_target = gc.header_builder.get_impersonate_target()
    c._session_token = "token"
    c._healthy = True
    return c


def _inner_uuid(request_body: dict) -> str:
    inner = json.loads(json.loads(request_body["f.req"])[1])
    return inner[59]


# ---- 1. payload <-> header UUID pairing -----------------------------------------------------------------

def test_send_request_echoes_the_payload_uuid_in_the_companion_header():
    client = _client()
    captured = {}

    class FakeResp:
        status_code = 500
        text = "nope"

    class FakeSession:
        async def post(self, url, **kwargs):
            captured.update(kwargs.get("data") or {})
            captured["headers"] = kwargs.get("headers") or {}
            return FakeResp()

    client._http = FakeSession()

    with pytest.raises(Exception):
        asyncio.run(client._send_request("prompt", model="gemini-4-argon"))

    header_uuid = json.loads(captured["headers"]["x-goog-ext-525005358-jspb"])[0]
    assert header_uuid == _inner_uuid(captured), (
        "the companion header UUID and the payload's inner[59] must be the same per-request id"
    )


def test_generate_stream_echoes_the_payload_uuid_in_the_companion_header():
    client = _client()
    captured = {}

    class FakeStreamCtx:
        def __init__(self, **kwargs):
            captured.update(kwargs.get("data") or {})
            captured["headers"] = kwargs.get("headers") or {}

        async def __aenter__(self):
            raise RuntimeError("stop stream test")

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        def stream(self, method, url, **kwargs):
            return FakeStreamCtx(**kwargs)

    client._http = FakeSession()

    async def drain():
        async for _ in client.generate_stream("hi", model="gemini-4-argon"):
            pass

    with pytest.raises(RuntimeError, match="stop stream test"):
        asyncio.run(drain())

    header_uuid = json.loads(captured["headers"]["x-goog-ext-525005358-jspb"])[0]
    assert header_uuid == _inner_uuid(captured)
    assert header_uuid == header_uuid.upper(), "the protocol form is upper-case"


# ---- 2. the conversation slot does not exist ------------------------------------------------------------

def test_thinking_payload_has_no_conversation_slot():
    inner = json.loads(json.loads(
        _client()._encode_payload_thinking("hi", "gemini-4-argon", "gconv-123", None, None)[0])[1])
    assert inner[2] != "gconv-123", "inner[2] is the metadata block, not a conversation id"
    assert isinstance(inner[2], list)
    assert "gconv-123" not in json.dumps(inner), "the id must not leak into an unrelated slot"


def test_dropped_conversation_is_reported_once(monkeypatch, caplog):
    monkeypatch.setattr(gc, "_continuation_drop_warned", False)
    client = _client()
    with caplog.at_level("WARNING", logger="app.core.gemini_client"):
        client._encode_payload_thinking("hi", "gemini-4-argon", "gconv-123", None, None)
        client._encode_payload_thinking("hi", "gemini-4-argon", "gconv-456", None, None)

    hits = [r for r in caplog.records if "cannot continue a stored conversation" in r.getMessage()]
    assert len(hits) == 1, "the warning must be logged once per process, not per request"
    assert "gconv" not in caplog.text, "the opaque conversation id must not be printed"


def test_legacy_encoder_is_not_used_at_runtime():
    """`_encode_payload` is kept only as a byte-identity reference; runtime must use the thinking payload."""
    src = (gc.__file__ and open(gc.__file__, encoding="utf-8").read()) or ""
    body = src.split("def _encode_payload_thinking", 1)[1]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "def " in stripped:
            continue
        assert "self._encode_payload(" not in stripped, "legacy 4-element payload is back on the hot path"
