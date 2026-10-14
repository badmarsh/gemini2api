import pytest
from app.core.gemini_client import (
    GoogleWebError, GOOGLE_WEB_ERROR_PHRASES, classify_error, GeminiWebClient
)
from app.core.account_pool import _is_retryable
from app.utils.prompt import build_prompt_from_messages


def test_classify_error_google_web_error():
    err = GoogleWebError("Google web error: Sorry, something went wrong. Please try your request again.")
    status, err_type, retry_after = classify_error(err)
    assert status == 502
    assert err_type == "api_error"
    assert retry_after == 2


def test_is_retryable_google_web_error():
    err = GoogleWebError("Google web error: Sorry, something went wrong. Please try your request again.")
    assert _is_retryable(err) is True


def test_google_web_error_phrases_non_empty():
    assert len(GOOGLE_WEB_ERROR_PHRASES) >= 4
    for phrase in GOOGLE_WEB_ERROR_PHRASES:
        assert isinstance(phrase, str)
        assert len(phrase) > 5


def test_prompt_preserves_developer_and_multi_turn():
    messages = [
        {"role": "developer", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "Can you inspect my repo?"},
    ]
    prompt = build_prompt_from_messages(messages)
    assert "System: You are a helpful coding assistant." in prompt
    assert "Human: Hello" in prompt
    assert "Assistant: Hi there!" in prompt
    assert "Human: Can you inspect my repo?" in prompt
