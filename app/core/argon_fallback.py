"""Argon -> Flash fallback for the Claude (/v1/messages) and native Gemini routers.

openai.py and responses.py carry this logic inline. These two routers share this helper so that an Argon
failure (error, hang, empty reply, web-error text) degrades to ``settings.argon_fallback_model`` instead of
reaching the client, controlled by the same settings: ``argon_fallback_to_flash`` and ``argon_always_fallback``.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from app.config import settings
from app.core import argon_breaker
from app.core.fallback import is_empty_result
from app.core.gemini_client import GOOGLE_WEB_ERROR_PHRASES, UPSTREAM_ERRORS

logger = logging.getLogger(__name__)


def plan(requested_model: str, resolved_model: str) -> tuple[str, bool, bool]:
    """Return ``(model_to_use, is_argon, force_thinking)`` for a request.

    With ``argon_always_fallback`` (or while the stall breaker is open) the fallback model is served directly
    (thinking on) and the request is no longer treated as Argon, mirroring openai.py.
    """
    is_argon = "argon" in (requested_model or "").lower() or "argon" in (resolved_model or "").lower()
    if is_argon and (getattr(settings, "argon_always_fallback", False) or argon_breaker.is_open()):
        logger.info(f"Argon requested ('{requested_model}'), but argon_always_fallback or the stall breaker is "
                    f"active. Serving {settings.argon_fallback_model}.")
        return settings.argon_fallback_model, False, True
    return resolved_model, is_argon, False


def _enabled(is_argon: bool) -> bool:
    return bool(is_argon and getattr(settings, "argon_fallback_to_flash", True))


def _unusable(result: Any) -> bool:
    """Empty reply, or a reply that is just a Google web error message."""
    if is_empty_result(result):
        return True
    text = (result.get("text") or "").strip()
    return bool(text) and any(text.startswith(p) for p in GOOGLE_WEB_ERROR_PHRASES)


async def generate(pool, prompt: str, model: str, attachments, *, is_argon: bool, thinking: bool = False,
                   gem_id=None, account_id=None) -> dict:
    """``pool.generate`` with the Argon -> Flash fallback. Raises the fallback's error if both fail."""
    kw = {"extended_thinking": True} if thinking else {}
    use_fallback = _enabled(is_argon)
    try:
        result = await pool.generate(prompt, model, "", attachments, gem_id=gem_id, account_id=account_id, **kw)
    except UPSTREAM_ERRORS as e:
        if not use_fallback:
            raise
        logger.warning(f"Argon request failed ({e}), falling back to {settings.argon_fallback_model}")
        return await pool.generate(prompt, settings.argon_fallback_model, "", attachments,
                                   gem_id=gem_id, account_id=account_id, extended_thinking=True)
    if use_fallback and _unusable(result):
        logger.warning(f"Argon returned an empty or web-error reply, falling back to {settings.argon_fallback_model}")
        return await pool.generate(prompt, settings.argon_fallback_model, "", attachments,
                                   gem_id=gem_id, account_id=account_id, extended_thinking=True)
    return result


async def generate_stream(pool, prompt: str, model: str, attachments, *, is_argon: bool, thinking: bool = False,
                          gem_id=None, account_id=None) -> AsyncGenerator[dict, None]:
    """``pool.generate_stream`` with the Argon -> Flash fallback.

    The fallback only starts while nothing has been streamed to the client: an upstream error before the first
    content, or a stream that ends with no answer. Once content has gone out, errors propagate unchanged.
    """
    kw = {"extended_thinking": True} if thinking else {}
    if not _enabled(is_argon):
        async for evt in pool.generate_stream(prompt, model, "", attachments,
                                              gem_id=gem_id, account_id=account_id, **kw):
            yield evt
        return

    streamed = False
    try:
        async for evt in pool.generate_stream(prompt, model, "", attachments,
                                              gem_id=gem_id, account_id=account_id, **kw):
            kind = evt.get("type")
            if kind == "delta":
                if evt.get("text"):
                    streamed = True
            elif kind == "final":
                if (evt.get("text") or "").strip() or evt.get("images"):
                    streamed = True
                elif not streamed:
                    continue  # empty answer: hold it back, the Flash stream below replaces it
            yield evt
    except Exception as e:
        if streamed:
            raise
        logger.warning(f"Argon stream failed before first chunk ({e}), falling back to {settings.argon_fallback_model}")
    else:
        if streamed:
            return
        logger.warning(f"Argon stream ended without any answer, falling back to {settings.argon_fallback_model}")

    async for evt in pool.generate_stream(prompt, settings.argon_fallback_model, "", attachments,
                                          gem_id=gem_id, account_id=account_id, extended_thinking=True):
        yield evt
