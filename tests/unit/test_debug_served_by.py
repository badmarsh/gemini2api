"""The request diagnostics must report which model really served a request.

Live evidence (2026-10-17): every ``gemini-flash`` chat was recorded with ``served_by="argon"``
because ``_debug_fallback`` defaulted to "argon" whenever the Argon-only header was absent. The
requested-vs-served comparison is the signal that exposes a silent Flash substitution for Argon, so
a default that names Argon for non-Argon traffic made the signal meaningless.
"""

from types import SimpleNamespace

from app.main import _debug_fallback


def _response(**headers):
    return SimpleNamespace(headers=headers)


def test_a_non_argon_request_without_a_marker_was_served_by_the_requested_model():
    fallback_used, reason, served_by = _debug_fallback(_response(), b"", "gemini-flash")
    assert served_by == "gemini-flash"
    assert fallback_used is False and reason == ""


def test_an_argon_request_without_a_marker_is_still_reported_as_argon():
    assert _debug_fallback(_response(), b"", "gemini-4-argon")[2] == "argon"
    assert _debug_fallback(_response(), b"", "gemini-argon")[2] == "argon"


def test_an_unknown_requested_model_keeps_the_legacy_default():
    assert _debug_fallback(_response(), b"")[2] == "argon"
    assert _debug_fallback(_response(), b"", None)[2] == "argon"


def test_the_served_by_header_still_wins():
    headers = {"X-Gemini2API-Argon-Served-By": "gemini-flash-thinking"}
    fallback_used, _, served_by = _debug_fallback(_response(**headers), b"", "gemini-4-argon")
    assert served_by == "gemini-flash-thinking"
    assert fallback_used is True


def test_the_stream_marker_still_wins():
    raw = b"x gemini2api argon_fallback served_by=gemini-flash-thinking reason=empty_stream\n"
    fallback_used, reason, served_by = _debug_fallback(_response(), raw, "gemini-4-argon")
    assert (fallback_used, reason, served_by) == (True, "empty_stream", "gemini-flash-thinking")


def test_the_requested_model_label_is_bounded():
    assert len(_debug_fallback(_response(), b"", "m" * 500)[2]) <= 64
