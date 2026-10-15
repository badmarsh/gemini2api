"""Scrub regex gaps (hardening follow-up A1).

``_scrub_sensitive`` backs both the uvicorn access-log filter and the request-logging
middleware. These tests pin the cases the first version missed: Anthropic/OpenAI project keys
(letters, digits, ``-`` and ``_``), URL-encoded ``sk%2D`` prefixes and ``key=`` / ``api_key=``
query values. The original behaviour (see test_access_log_scrub.py) is untouched.
"""

import logging

import pytest

ACCESS_FMT = '%s - "%s %s HTTP/%s" %d'


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Anthropic key: every dash-separated segment must go.
        ("/x?a=sk-ant-api03-AbC_dEf-123XYZ&b=1", "/x?a=sk-****&b=1"),
        # OpenAI project key: the tail after the first '-' / '_' used to leak.
        ("Authorization: sk-proj-abc123_DEF456-ZZ-tail", "Authorization: sk-****"),
        # URL-encoded dash, both hex cases.
        ("/x?k=sk%2Dabcdef1234567890&b=1", "/x?k=sk-****&b=1"),
        ("/x?k=sk%2dabcdef1234567890&b=1", "/x?k=sk-****&b=1"),
        ("/x?k=sk%2Dant%2Dapi03%2DAbCdEf", "/x?k=sk-****"),
        # Plain legacy key keeps working.
        ("/admin/sk-abcdef1234567890/status", "/admin/sk-****/status"),
    ],
)
def test_sk_style_keys_are_fully_masked(app_main, raw, expected):
    assert app_main._scrub_sensitive(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("/v1beta/models?key=AIzaSyFAKE123_-xyz&alt=sse", "/v1beta/models?key=****&alt=sse"),
        ("/v1/models?api_key=topsecretvalue&x=1", "/v1/models?api_key=****&x=1"),
        ("/v1/models?API_KEY=TopSecret", "/v1/models?API_KEY=****"),
        ("/v1/models?apikey=topsecretvalue", "/v1/models?apikey=****"),
        ("/v1/models?api-key=topsecretvalue&x=1", "/v1/models?api-key=****&x=1"),
        ("/v1/models?token=abc123&key=def456", "/v1/models?token=****&key=****"),
    ],
)
def test_key_and_api_key_query_values_are_masked(app_main, raw, expected):
    assert app_main._scrub_sensitive(raw) == expected


@pytest.mark.parametrize(
    "clean",
    [
        "/v1/models",
        "/v1/models?limit=10&keyword=cats",
        "/v1/models?monkey=banana",
        "/v1/models?key_id=7",
        "/admin/sk-ab/status",  # too short to be a key, same as before
        "",
    ],
)
def test_clean_strings_are_untouched(app_main, clean):
    assert app_main._scrub_sensitive(clean) == clean


def test_access_log_filter_masks_new_shapes_and_keeps_int_status(app_main):
    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0, ACCESS_FMT,
        ("127.0.0.1:1", "GET", "/x?key=SECRETVALUE&a=sk-ant-api03-AbC_dEf-123", "1.1", 200),
        None,
    )
    assert logging.getLogger("uvicorn.access").filter(rec)
    msg = rec.getMessage()
    assert "SECRETVALUE" not in msg
    assert "AbC_dEf" not in msg
    assert "123" not in msg.split("HTTP")[0].split("sk-****")[-1]
    assert rec.args[-1] == 200 and isinstance(rec.args[-1], int)
