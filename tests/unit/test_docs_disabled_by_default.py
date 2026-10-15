"""/docs, /redoc and /openapi.json are unauthenticated in FastAPI, so they are off by default."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings


def test_docs_enabled_defaults_to_false():
    assert Settings.model_fields["docs_enabled"].default is False


def test_docs_enabled_can_be_switched_on_from_the_environment(monkeypatch):
    monkeypatch.setenv("DOCS_ENABLED", "true")
    assert Settings().docs_enabled is True


def test_fastapi_kwargs_disable_all_three_doc_routes_by_default():
    from app.main import _fastapi_kwargs

    kwargs = _fastapi_kwargs(False)

    assert kwargs["docs_url"] is None
    assert kwargs["redoc_url"] is None
    assert kwargs["openapi_url"] is None
    assert kwargs["title"] == "Gemini2API"


def test_fastapi_kwargs_keep_fastapi_defaults_when_enabled():
    from app.main import _fastapi_kwargs

    app = FastAPI(**_fastapi_kwargs(True))

    assert (app.docs_url, app.redoc_url, app.openapi_url) == ("/docs", "/redoc", "/openapi.json")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_doc_paths_return_404_without_a_key_by_default(path):
    from app.main import app

    response = TestClient(app).get(path)

    assert response.status_code == 404
