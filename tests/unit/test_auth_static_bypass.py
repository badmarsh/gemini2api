"""Regression tests: the static-asset auth bypass must not leak into API/admin routes.

Previously ``_is_public_path`` exempted ANY path whose suffix looked like a static
file (.png/.js/.css/...), regardless of HTTP method or prefix, so e.g.
``DELETE /admin/accounts/test.png`` skipped both verify_admin_key and verify_api_key.
"""

import pytest

from app.core.auth import _is_public_path


# ---- pure-function level -------------------------------------------------

@pytest.mark.parametrize(
    "path",
    ["/health", "/", "/login.html", "/index.html"],
)
def test_exact_public_pages_stay_public_for_any_method(path):
    for method in ("GET", "HEAD", "POST"):
        assert _is_public_path(path, method) is True


@pytest.mark.parametrize(
    "path",
    [
        "/app/settings.js",
        "/components/section-settings.html",
        "/api-assets/wechat-qr.png",
        "/favicon.ico",
        "/static-thing.css",
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD", "get"])
def test_static_assets_public_for_read_methods(path, method):
    assert _is_public_path(path, method) is True


@pytest.mark.parametrize(
    "path",
    [
        "/app/settings.js",
        "/components/section-settings.html",
        "/api-assets/wechat-qr.png",
        "/anything.png",
    ],
)
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_static_assets_not_public_for_mutating_methods(path, method):
    assert _is_public_path(path, method) is False


@pytest.mark.parametrize(
    "path",
    [
        "/admin/accounts/test.png",
        "/admin/logs/x.js",
        "/admin",
        "/v1/models.css",
        "/v1/chat/completions.png",
        "/v1beta/models/foo.js",
        "/openai/v1/models.html",
        "/claude/v1/messages.ico",
        "/gemini/v1beta/x.svg",
    ],
)
def test_api_and_admin_prefixes_never_get_static_bypass(path):
    assert _is_public_path(path, "GET") is False


def test_prefix_match_is_segment_based():
    # "/administrator.png" is NOT under /admin/, so it keeps normal static behaviour.
    assert _is_public_path("/administrator.png", "GET") is True
    assert _is_public_path("/v10.css", "GET") is True


# ---- app level -----------------------------------------------------------

def test_delete_admin_route_with_png_suffix_requires_auth(client):
    r = client.delete("/admin/accounts/test.png")
    assert r.status_code == 401


@pytest.mark.parametrize("suffix", [".png", ".js", ".css", ".html", ".ico", ".svg"])
def test_delete_admin_route_with_static_suffix_rejected_without_key(client, suffix):
    # DELETE /admin/accounts/{account_id} is a real mutating admin route; the
    # account id deliberately looks like a static file.
    r = client.delete(f"/admin/accounts/test{suffix}")
    assert r.status_code == 401


def test_delete_admin_route_with_static_suffix_rejected_with_wrong_key(client):
    r = client.delete(
        "/admin/accounts/test.png", headers={"Authorization": "Bearer sk-wrong"}
    )
    assert r.status_code == 401


def test_api_route_with_static_suffix_requires_key(client):
    r = client.post("/v1/chat/completions.png", json={})
    assert r.status_code in (401, 404, 405)
    assert r.status_code != 200


def test_static_panel_assets_still_served_without_key(client):
    r = client.get("/app/settings.js")
    assert r.status_code == 200
    r = client.get("/login.html")
    assert r.status_code == 200


def test_health_still_public(client):
    assert client.get("/health").status_code == 200
