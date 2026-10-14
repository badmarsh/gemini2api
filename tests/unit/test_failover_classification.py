"""Failover classification must key on error type/code, not on free text.

_is_stall() used to substring-match "timeout"/"exhausted"/"failed to generate"
against str(exc). HTTPStatusError embeds the upstream response body in its
message, so any 4xx whose body merely mentioned one of those words was treated
as an account stall (cooldown plus failover). Likewise the Google error-template
check used startswith() on the model reply, so a legitimate answer that opened
with one of those sentences became a GoogleWebError.
"""
import asyncio

from curl_cffi.requests.errors import RequestsError

import app.core.gemini_client as gc
from app.core.account_pool import _is_stall
from app.core.gemini_client import HTTPStatusError

CURLE_OPERATION_TIMEDOUT = 28


def test_stall_is_recognized_by_type_and_code():
    assert _is_stall(asyncio.TimeoutError()) is True
    assert _is_stall(TimeoutError()) is True
    # Message deliberately lacks any timeout wording: only the curl code identifies it.
    assert _is_stall(RequestsError("transfer aborted", CURLE_OPERATION_TIMEDOUT)) is True
    assert _is_stall(gc.RetriesExhaustedError("Exhausted 3 retries: connection reset")) is True


def test_error_text_mentioning_timeout_or_exhausted_is_not_a_stall():
    assert _is_stall(HTTPStatusError(400, '{"error": "timeout_ms must be an integer"}')) is False
    assert _is_stall(HTTPStatusError(400, "quota exhausted for this project")) is False
    assert _is_stall(ValueError("failed to generate a valid schema")) is False
    assert _is_stall(RuntimeError("buffer exhausted")) is False


def test_retries_exhausted_keeps_its_message():
    err = gc.RetriesExhaustedError("Exhausted 3 retries: boom")
    assert isinstance(err, RuntimeError)
    assert str(err) == "Exhausted 3 retries: boom"


PHRASE = "Sorry, something went wrong. Please try your request again."


def test_error_template_reply_is_detected():
    assert gc._is_google_error_reply(PHRASE) is True
    assert gc._is_google_error_reply("  " + PHRASE + "\n") is True
    assert gc._is_google_error_reply("I seem to be encountering an error. Can I try something else for you?") is True
    assert gc._is_google_error_reply(PHRASE + " (code 7)") is True


def test_legitimate_answer_starting_with_a_phrase_is_not_an_error():
    long_tail = (
        " That is the generic message most apps show when a request fails; a better one "
        "tells the user what happened and what to do next, for example by offering a retry button."
    )
    assert gc._is_google_error_reply(PHRASE + long_tail) is False
    assert gc._is_google_error_reply(PHRASE + "\n\nHere are five alternatives:\n1. Oops!") is False
    assert gc._is_google_error_reply("Here is a friendly error message: " + PHRASE) is False
    assert gc._is_google_error_reply("") is False
