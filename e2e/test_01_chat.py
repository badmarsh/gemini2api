"""Plain chat behaviour on /v1/chat/completions: stream/non-stream, system prompt, history, conversation_id, unicode."""
import pytest

import e2e_helpers as h

pytestmark = pytest.mark.live


def u(text):
    return [{"role": "user", "content": text}]


@pytest.mark.core
def test_completion_shape(client):
    resp = h.chat(client, u("Reply with exactly one word: OKAPI"))
    assert resp["object"] == "chat.completion"
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert "okapi" in h.text_of(resp).lower()


@pytest.mark.core
def test_stream_matches_contract(client):
    res = h.chat_stream(client, u("Reply with exactly one word: NARWHAL"))
    assert "narwhal" in res.text.lower()
    assert res.finish_reason == "stop"
    assert res.saw_done and res.chunks >= 2


def test_callers_system_prompt_is_obeyed(client):
    msgs = [{"role": "system", "content": "Whatever the user says, answer with the single word TAPIR."},
            {"role": "user", "content": "What is the capital of France?"}]
    assert "tapir" in h.text_of(h.chat(client, msgs)).lower()


def test_developer_role_is_accepted(client):
    msgs = [{"role": "developer", "content": "Whatever the user says, answer with the single word LEMUR."},
            {"role": "user", "content": "What is the capital of France?"}]
    assert "lemur" in h.text_of(h.chat(client, msgs)).lower()


def test_multi_turn_history(client):
    msgs = [{"role": "user", "content": "My lucky number is 4817. Just say 'noted'."},
            {"role": "assistant", "content": "noted"},
            {"role": "user", "content": "What is my lucky number? Answer with the number only."}]
    assert "4817" in h.text_of(h.chat(client, msgs))


def test_conversation_id_continuation_sends_only_last_message(client):
    first = h.chat(client, u("Remember this codeword: QUOKKA-5521. Just say 'stored'."))
    conv = first.get("conversation_id")
    assert conv, f"response carries no conversation_id: {list(first)}"
    second = h.chat(client, u("What was the codeword? Answer with the codeword only."), conversation_id=conv)
    assert "quokka-5521" in h.text_of(second).lower()


def test_unicode_roundtrip(client):
    phrase = "Ďakujem, žltý kôň úpäl ďaleko"
    text = h.text_of(h.chat(client, u(f"Repeat this sentence exactly and nothing else: {phrase}")))
    assert phrase.lower() in text.lower()


def test_long_prompt(client):
    filler = "The quick brown fox jumps over the lazy dog. " * 400
    msgs = u(f"{filler}\nIgnore the text above. What is 12 + 30? Answer with the number only.")
    assert "42" in h.text_of(h.chat(client, msgs))
