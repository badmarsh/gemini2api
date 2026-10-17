"""Response hardening headers and the (non-)existence of a CSRF surface.

The console authenticates with an ``Authorization: Bearer <key>`` header, so the
browser never attaches an ambient credential to a state-changing admin call: a
cross-site form or ``<img>`` cannot carry the admin key. These tests pin that
property (it is the actual CSRF defence) and the hardening headers.
"""
from __future__ import annotations

import pytest


def test_console_pages_carry_the_hardening_headers(client):
    for path in ("/health", "/index.html", "/login.html"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "geolocation=()" in response.headers["permissions-policy"]
        assert response.headers["cross-origin-opener-policy"] == "same-origin"


def test_content_security_policy_blocks_plugins_and_framing(client):
    response = client.get("/index.html")
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "connect-src 'self'" in csp
    # Only same-origin scripts plus the two CDNs the console really uses.
    assert "script-src 'self'" in csp
    assert "cdnjs.cloudflare.com" in csp


def test_api_responses_carry_the_headers_without_changing_the_contract(client):
    response = client.get("/admin/status", headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    # The body is untouched: the headers are additive only.
    assert "accounts" in response.json()


def test_admin_endpoints_do_not_accept_a_cookie_as_a_credential(client):
    """A cookie alone must never authenticate an admin call (no ambient credentials)."""
    response = client.get("/admin/status", cookies={"gemini2api_token": "sk-test-key"})
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"


def test_state_changing_admin_call_without_a_key_is_rejected(client):
    response = client.post("/admin/argon/reset")
    assert response.status_code == 401


def test_static_assets_keep_their_own_cache_headers(client):
    response = client.get("/app/app.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_apply_security_headers_does_not_overwrite_existing_values():
    from starlette.responses import Response

    from app.core.security_headers import apply_security_headers

    response = Response("x")
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    apply_security_headers(response)
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/admin/metrics", "/admin/debug-metrics"])
def test_metrics_endpoints_are_hardened_too(client, path):
    response = client.get(path, headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
