"""Regression tests for multi-turn context in POST /v1/chat/completions.

An audit claimed that a stale or unknown ``conversation_id`` drops earlier turns
and only the last user message reaches Gemini. The router actually behaves the
other way round: only a *valid* stored ``gemini_conv_id`` sends the last user
message alone (server-side session continuation). In every other case
(no conversation_id, unknown/stale id, stored conversation without a
gemini_conv_id) the whole message history is flattened into the prompt and the
upstream call gets an empty conversation id.

These tests pin that behavior so a future refactor cannot silently regress it.
"""

_AUTH = {"Authorization": "Bearer sk-test-key"}

_HISTORY = [
    {"role": "user", "content": "FIRST-USER-TURN"},
    {"role": "assistant", "content": "FIRST-ASSISTANT-TURN"},
    {"role": "user", "content": "SECOND-USER-TURN"},
]


class _FakeConv:
    def __init__(self, gemini_conv_id="", conv_id="conv"):
        self.id = conv_id
        self.gemini_conv_id = gemini_conv_id


class _FakeStore:
    """Stand-in for conversation_store so no test touches the on-disk store."""

    def __init__(self, convs=None):
        self._convs = convs or {}

    async def get(self, conv_id):
        return self._convs.get(conv_id)


def _post(gem_client, monkeypatch, store, **extra):
    import app.routers.openai as oai

    captured = {}

    async def fake_generate(prompt, model, conversation_id="", attachments=None,
                            gem_id=None, account_id=None, extended_thinking=False):
        captured["prompt"] = prompt
        captured["conversation_id"] = conversation_id
        # Empty conversation_id in the result -> router does not persist anything.
        return {"text": "ok", "images": [], "conversation_id": "", "thoughts": ""}

    monkeypatch.setattr(oai, "conversation_store", store)
    monkeypatch.setattr(oai.gemini_client, "generate", fake_generate)
    body = {"model": "gemini-flash", "messages": _HISTORY, **extra}
    r = gem_client.post("/v1/chat/completions", json=body, headers=_AUTH)
    assert r.status_code == 200, r.text
    return captured


def _assert_full_history(captured):
    assert captured["conversation_id"] == ""
    for marker in ("FIRST-USER-TURN", "FIRST-ASSISTANT-TURN", "SECOND-USER-TURN"):
        assert marker in captured["prompt"]


def test_no_conversation_id_keeps_full_history(gem_client, monkeypatch):
    _assert_full_history(_post(gem_client, monkeypatch, _FakeStore()))


def test_unknown_conversation_id_keeps_full_history(gem_client, monkeypatch):
    captured = _post(gem_client, monkeypatch, _FakeStore(), conversation_id="stale-id")
    _assert_full_history(captured)


def test_stored_conversation_without_gemini_id_keeps_full_history(gem_client, monkeypatch):
    store = _FakeStore({"conv-1": _FakeConv(gemini_conv_id="")})
    captured = _post(gem_client, monkeypatch, store, conversation_id="conv-1")
    _assert_full_history(captured)


def test_valid_gemini_conv_id_sends_only_last_user_message(gem_client, monkeypatch):
    """Control: the one case that really does send just the latest user message."""
    store = _FakeStore({"conv-2": _FakeConv(gemini_conv_id="gconv-123")})
    captured = _post(gem_client, monkeypatch, store, conversation_id="conv-2")
    assert captured["conversation_id"] == "gconv-123"
    assert captured["prompt"] == "SECOND-USER-TURN"
