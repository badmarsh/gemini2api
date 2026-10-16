"""ARGON_SYSTEM_PROMPT (settings.argon_system_prompt) injection on the OpenAI and Claude routers.

Contract:
- The prompt is injected into Argon requests only, on new conversations.
- Default mode "merge": the Argon rules go in front of the caller's own system/developer message. Coding
  harnesses (Codex, Cline, OpenCode) always send theirs, and the old "skip when the caller sent one" rule
  silently abandoned the Argon rules exactly there (ARGON_SYSTEM_PROMPT_MODE=only_when_none restores it).
- It goes in *before* the prompt is flattened and before the tool prompt is wrapped around it, so tool calling
  keeps working on Argon.
- A continuing conversation (valid stored gemini_conv_id) still sends only the last user message: the upstream
  chat already holds the system prompt from its first turn.
"""
import pytest

_AUTH = {"Authorization": "Bearer sk-test-key"}
_SYS = "ARGON-SYS-PROMPT-MARKER"
_TOOL_NAME = "UNIQUE_TOOL_NAME"

_OAI_TOOL = {"type": "function", "function": {
    "name": _TOOL_NAME, "description": "does a thing", "parameters": {"type": "object", "properties": {}}}}
_CLAUDE_TOOL = {"name": _TOOL_NAME, "description": "does a thing",
                "input_schema": {"type": "object", "properties": {}}}

_HISTORY = [
    {"role": "user", "content": "FIRST-USER-TURN"},
    {"role": "assistant", "content": "FIRST-ASSISTANT-TURN"},
    {"role": "user", "content": "LAST-USER-TURN"},
]


class _FakeConv:
    def __init__(self, gemini_conv_id=""):
        self.id = "conv"
        self.gemini_conv_id = gemini_conv_id


class _FakeStore:
    def __init__(self, convs=None):
        self._convs = convs or {}

    async def get(self, conv_id):
        return self._convs.get(conv_id)


@pytest.fixture
def argon_env(monkeypatch):
    """Argon enabled path: no forced Flash fallback, breaker closed, injection prompt configurable."""
    from app.config import settings
    import app.core.argon_breaker as breaker

    monkeypatch.setattr(settings, "argon_system_prompt", _SYS)
    monkeypatch.setattr(settings, "argon_always_fallback", False)
    monkeypatch.setattr(breaker, "is_open", lambda: False)
    return settings


@pytest.fixture
def oai(monkeypatch, gem_client):
    import app.routers.openai as oai_router

    prompts, conv_ids = [], []

    async def fake_generate(prompt, model, conversation_id="", attachments=None,
                            gem_id=None, account_id=None, extended_thinking=False):
        prompts.append(prompt)
        conv_ids.append(conversation_id)
        return {"text": "ok", "images": [], "conversation_id": "", "thoughts": ""}

    monkeypatch.setattr(oai_router, "conversation_store", _FakeStore())
    monkeypatch.setattr(oai_router.gemini_client, "generate", fake_generate)

    def post(model="gemini-4-argon", messages=None, **extra):
        body = {"model": model, "messages": messages or _HISTORY, **extra}
        r = gem_client.post("/v1/chat/completions", json=body, headers=_AUTH)
        assert r.status_code == 200, r.text
        return prompts[0], conv_ids[0]

    post.store = lambda s: monkeypatch.setattr(oai_router, "conversation_store", s)
    return post


@pytest.fixture
def claude(monkeypatch, gem_client):
    import app.routers.claude as claude_router

    prompts = []

    async def fake_generate(prompt, model, conversation_id="", attachments=None,
                            gem_id=None, account_id=None, extended_thinking=False):
        prompts.append(prompt)
        return {"text": "ok", "images": [], "conversation_id": "", "thoughts": ""}

    monkeypatch.setattr(claude_router.gemini_client, "generate", fake_generate)

    def post(model="gemini-4-argon", **extra):
        body = {"model": model, "max_tokens": 100,
                "messages": [{"role": "user", "content": "LAST-USER-TURN"}], **extra}
        r = gem_client.post("/v1/messages", json=body, headers=_AUTH)
        assert r.status_code == 200, r.text
        return prompts[0]

    return post


# ---- /v1/chat/completions -------------------------------------------------------------------------------

def test_openai_prepends_system_prompt_to_new_argon_conversation(argon_env, oai):
    prompt, conv_id = oai()
    assert conv_id == ""
    assert _SYS in prompt
    assert prompt.index(_SYS) < prompt.index("FIRST-USER-TURN")
    for marker in ("FIRST-USER-TURN", "FIRST-ASSISTANT-TURN", "LAST-USER-TURN"):
        assert marker in prompt


def test_openai_system_prompt_does_not_drop_the_tool_prompt(argon_env, oai):
    prompt, _ = oai(tools=[_OAI_TOOL])
    assert _SYS in prompt
    assert _TOOL_NAME in prompt, "tool prompt was lost when the Argon system prompt was injected"
    # build_tool_prompt wraps the flattened conversation: tool instructions first, conversation after.
    assert prompt.index(_TOOL_NAME) < prompt.index(_SYS) < prompt.index("FIRST-USER-TURN")


def test_openai_merges_argon_rules_in_front_of_callers_system_message(argon_env, oai):
    """Default mode: BOTH prompts reach the model, Argon's rules first."""
    messages = [{"role": "system", "content": "CALLER-SYSTEM"}, *_HISTORY]
    prompt, _ = oai(messages=messages)
    assert "CALLER-SYSTEM" in prompt, "the caller's own system prompt was dropped"
    assert _SYS in prompt
    assert prompt.index(_SYS) < prompt.index("CALLER-SYSTEM")


def test_openai_only_when_none_mode_keeps_the_legacy_behaviour(argon_env, oai, monkeypatch):
    monkeypatch.setattr(argon_env, "argon_system_prompt_mode", "only_when_none")
    prompt, _ = oai(messages=[{"role": "system", "content": "CALLER-SYSTEM"}, *_HISTORY])
    assert "CALLER-SYSTEM" in prompt
    assert _SYS not in prompt


def test_openai_replace_mode_uses_the_argon_rules_instead(argon_env, oai, monkeypatch):
    monkeypatch.setattr(argon_env, "argon_system_prompt_mode", "replace")
    prompt, _ = oai(messages=[{"role": "system", "content": "CALLER-SYSTEM"}, *_HISTORY])
    assert _SYS in prompt
    assert "CALLER-SYSTEM" not in prompt, "replace mode must drop the caller's system prompt"


def test_openai_keeps_the_full_history_when_the_server_cannot_continue(argon_env, oai):
    """A stored conversation_id is forwarded, but the thinking payload has no slot for it upstream (spec
    §4.3), so the prompt must keep the client's whole history — stripping it to the last message used to lose
    the context silently (see SERVER_SIDE_CONTINUATION)."""
    oai.store(_FakeStore({"conv-1": _FakeConv(gemini_conv_id="gconv-123")}))
    prompt, conv_id = oai(conversation_id="conv-1", tools=[_OAI_TOOL])
    # The id is dropped rather than forwarded: nothing upstream can use it, and forwarding it kept the
    # "conversation expired -> resend the full prompt" retry path alive, which would just repeat this call.
    assert conv_id == ""
    assert "FIRST-USER-TURN" in prompt and "LAST-USER-TURN" in prompt, "the history was stripped again"
    assert _SYS not in prompt, "the system prompt is re-sent with the history, no injection needed"
    assert _TOOL_NAME in prompt


def test_openai_strips_the_history_only_when_continuation_is_enabled(argon_env, oai, monkeypatch):
    monkeypatch.setattr(argon_env, "server_side_continuation", True)
    oai.store(_FakeStore({"conv-1": _FakeConv(gemini_conv_id="gconv-123")}))
    prompt, conv_id = oai(conversation_id="conv-1", tools=[_OAI_TOOL])
    assert conv_id == "gconv-123"
    assert "FIRST-USER-TURN" not in prompt, "whole history was re-sent although the server continues it"
    assert "LAST-USER-TURN" in prompt
    assert _TOOL_NAME in prompt


def test_openai_non_argon_model_is_untouched(argon_env, oai):
    prompt, _ = oai(model="gemini-flash")
    assert _SYS not in prompt


def test_openai_empty_setting_disables_injection(argon_env, oai, monkeypatch):
    monkeypatch.setattr(argon_env, "argon_system_prompt", "   ")
    prompt, _ = oai()
    assert "System:" not in prompt


# ---- /v1/messages ---------------------------------------------------------------------------------------

def test_claude_prepends_system_prompt_and_keeps_tools(argon_env, claude):
    prompt = claude(tools=[_CLAUDE_TOOL])
    assert prompt.startswith("System: " + _SYS) or prompt.index(_SYS) < prompt.index("LAST-USER-TURN")
    assert _TOOL_NAME in prompt


def test_claude_merges_argon_rules_in_front_of_callers_system(argon_env, claude):
    prompt = claude(system="CALLER-SYSTEM")
    assert "CALLER-SYSTEM" in prompt, "the caller's own system prompt was dropped"
    assert _SYS in prompt
    assert prompt.index(_SYS) < prompt.index("CALLER-SYSTEM")


def test_claude_non_argon_model_is_untouched(argon_env, claude):
    prompt = claude(model="gemini-flash")
    assert _SYS not in prompt


def test_claude_system_prompt_still_applies_when_stall_breaker_serves_flash(argon_env, claude, monkeypatch):
    import app.core.argon_breaker as breaker

    monkeypatch.setattr(breaker, "is_open", lambda: True)
    assert _SYS in claude()


def test_openai_system_prompt_still_applies_when_stall_breaker_serves_flash(argon_env, oai, monkeypatch):
    import app.core.argon_breaker as breaker

    monkeypatch.setattr(breaker, "is_open", lambda: True)
    prompt, _ = oai()
    assert _SYS in prompt


def test_openai_developer_message_no_longer_disables_the_argon_rules(argon_env, oai):
    """The regression Codex hit: it always sends a `developer` prompt, which used to switch the Argon
    rules off entirely (the injected prompt was never applied to the one client it was written for)."""
    prompt, _ = oai(messages=[{"role": "developer", "content": "CALLER-DEV"}, *_HISTORY])
    assert "CALLER-DEV" in prompt
    assert _SYS in prompt, "a developer message disabled ARGON_SYSTEM_PROMPT again"
    assert prompt.index(_SYS) < prompt.index("CALLER-DEV")
