"""Unit tests for model resolution in image-generation endpoint (item M)."""

import pytest
from unittest.mock import AsyncMock

_AUTH = {"Authorization": "Bearer sk-test-key"}


def test_image_endpoint_uses_resolved_model(client, monkeypatch):
    from app.routers import openai as openai_router

    mock_generate = AsyncMock(return_value={"images": [{"b64": "fakeb64", "mime": "image/png"}]})
    monkeypatch.setattr(openai_router.gemini_client, "generate", mock_generate)

    # dall-e-3 is mapped or resolved via _resolve_model
    resp = client.post(
        "/v1/images/generations",
        headers={**_AUTH, "Content-Type": "application/json"},
        json={"prompt": "A cute cat", "model": "gemini-1.5-pro"},
    )
    assert resp.status_code == 200

    # Ensure gemini_client.generate was called with resolved model, not raw "gemini-1.5-pro"
    assert mock_generate.called
    called_model = mock_generate.call_args[0][1]
    expected_model = openai_router._resolve_model("gemini-1.5-pro")
    assert called_model != "gemini-1.5-pro"
    assert called_model == expected_model
