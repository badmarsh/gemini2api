"""POST /admin/api-keys/models must not leak the upstream key or exception text (audit item 7b).

The Gemini branch used to put the key into the URL query (``?key=``), which ends up in
httpx exception text, proxy logs and anything that echoes ``str(e)`` back to the client.
"""

import httpx
import pytest

_AUTH = {"Authorization": "Bearer sk-test-key"}
SECRET = "AIzaSyFAKEKEYFORTESTS0123456789abcdef"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
URL = "/admin/api-keys/models"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, url="https://upstream.invalid/models"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"data": []}
        self._request = httpx.Request("GET", url)

    def raise_for_status(self):
        if self.status_code >= 400:
            resp = httpx.Response(self.status_code, request=self._request)
            raise httpx.HTTPStatusError(
                f"Server error '{self.status_code}' for url '{self._request.url}'",
                request=self._request,
                response=resp,
            )

    def json(self):
        return self._payload


@pytest.fixture
def upstream(monkeypatch):
    """Replace httpx.AsyncClient in the router with a recorder; no network, no DNS."""
    from app.routers import api_keys

    state = {"calls": [], "response": _FakeResponse(), "raises": None}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, **kwargs):
            state["calls"].append({"url": str(url), "headers": dict(headers or {})})
            if state["raises"] is not None:
                raise state["raises"]
            return state["response"]

    monkeypatch.setattr(api_keys.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(api_keys, "assert_safe_url", lambda url, **kw: None)
    return state


def _post(client, provider, base_url=GEMINI_BASE):
    return client.post(
        URL,
        headers=_AUTH,
        json={"provider": provider, "api_key": SECRET, "base_url": base_url},
    )


def test_gemini_key_goes_in_header_not_url(client, upstream):
    upstream["response"] = _FakeResponse(
        payload={"models": [{"name": "models/gemini-pro", "displayName": "Gemini Pro"}]}
    )
    resp = _post(client, "gemini")
    assert resp.status_code == 200
    assert resp.json() == {"models": [{"id": "gemini-pro", "display_name": "Gemini Pro"}]}
    call = upstream["calls"][0]
    assert SECRET not in call["url"]
    assert "key=" not in call["url"]
    assert call["url"] == f"{GEMINI_BASE}/models"
    assert call["headers"].get("x-goog-api-key") == SECRET


def test_openai_style_still_uses_bearer_header(client, upstream):
    resp = _post(client, "openai", base_url="https://api.openai.com/v1")
    assert resp.status_code == 200
    call = upstream["calls"][0]
    assert call["headers"] == {"Authorization": f"Bearer {SECRET}"}
    assert SECRET not in call["url"]


def test_anthropic_still_uses_x_api_key_header(client, upstream):
    resp = _post(client, "anthropic", base_url="https://api.anthropic.com/v1")
    assert resp.status_code == 200
    call = upstream["calls"][0]
    assert call["headers"]["x-api-key"] == SECRET
    assert SECRET not in call["url"]


def test_http_status_error_detail_has_no_url_or_key(client, upstream):
    upstream["response"] = _FakeResponse(
        status_code=500, url=f"{GEMINI_BASE}/models?key={SECRET}"
    )
    resp = _post(client, "gemini")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Upstream returned HTTP 500"
    assert SECRET not in resp.text
    assert "generativelanguage" not in resp.text


def test_unexpected_exception_detail_is_type_name_only(client, upstream):
    upstream["raises"] = RuntimeError(f"boom while calling {GEMINI_BASE}/models?key={SECRET}")
    resp = _post(client, "gemini")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "RuntimeError" in detail
    assert SECRET not in resp.text
    assert "boom" not in resp.text
    assert "generativelanguage" not in resp.text


def test_known_status_mappings_are_unchanged(client, upstream):
    upstream["response"] = _FakeResponse(status_code=401)
    resp = _post(client, "openai", base_url="https://api.openai.com/v1")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"

    upstream["response"] = _FakeResponse(status_code=502)
    resp = _post(client, "openai", base_url="https://api.openai.com/v1")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Bad gateway"


def test_timeout_mapping_is_unchanged(client, upstream):
    upstream["raises"] = httpx.ReadTimeout("slow")
    resp = _post(client, "openai", base_url="https://api.openai.com/v1")
    assert resp.status_code == 504
    assert resp.json()["detail"] == "Request timeout"
