"""A missing SNlM0e token is not proof of an expired cookie.

Regression: the old check matched the substring "accounts.google.com" anywhere in the body, but a
valid Gemini page links to accounts.google.com too, so healthy sessions were reported as
"Cookie expired - redirected to Google login page". Classification now uses the final URL.
"""

import pytest

from app.core.gemini_client import classify_missing_token

_VALID_LOOKING_PAGE = (
    "<html><head><title>Google Gemini</title></head><body>"
    + '<a href="https://accounts.google.com/SignOutOptions?hl=en">account</a>' * 5
    + "x" * 5000
    + "</body></html>"
)


def test_valid_gemini_page_linking_to_accounts_google_com_is_not_called_expired():
    kind, msg = classify_missing_token("https://gemini.google.com/app", 200, _VALID_LOOKING_PAGE)
    assert kind == "no_token"
    assert "Cookie expired" not in msg
    assert "gemini.google.com/app" in msg and "HTTP 200" in msg and "Google Gemini" in msg


@pytest.mark.parametrize(
    "url",
    [
        "https://accounts.google.com/v3/signin/identifier?continue=x",
        "https://accounts.google.com/ServiceLogin?continue=x",
    ],
)
def test_real_redirect_to_the_sign_in_page_is_reported_as_expired(url):
    kind, msg = classify_missing_token(url, 200, "<html>" + "x" * 3000)
    assert kind == "login"
    assert msg == "Cookie expired - redirected to Google login page"


def test_consent_and_challenge_pages_are_not_expiry():
    kind, msg = classify_missing_token("https://consent.google.com/m?continue=x", 200, "x" * 3000)
    assert kind == "consent" and "not an expired cookie" in msg
    kind, msg = classify_missing_token("https://www.google.com/sorry/index?continue=x", 429, "x" * 3000)
    assert kind == "challenge" and "not an expired cookie" in msg


def test_short_body_and_unknown_url_do_not_crash():
    kind, msg = classify_missing_token("", 200, "tiny")
    assert kind == "short" and "4 bytes" in msg
    kind, _ = classify_missing_token("http://[bad", 200, "x" * 2000)
    assert kind == "no_token"
    kind, _ = classify_missing_token("https://gemini.google.com/app", 200, None)
    assert kind == "short"
