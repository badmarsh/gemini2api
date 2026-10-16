"""Argon -> Flash fallback, shared by the Claude (/v1/messages) and native Gemini routers.

One contract, one implementation: whenever an Argon request is served by something other than Argon -- because
the caller asked for it (``ARGON_ALWAYS_FALLBACK``), the stall breaker is open (``argon_breaker``), or Argon
failed / timed out / returned an empty or Google-web-error reply -- the substitution is *observable*:

* non-streaming: the result dict carries ``argon_fallback = {"served_by", "reason"}`` and the router adds the
  ``X-Gemini2API-Arg...`` response headers;
* streaming: the first frame yielded is ``{"type": "serve_marker", ...}`` before any content (a substitution
  after content started is impossible by construction, streams never switch mid-flight);
* both: a WARNING log line.

Nothing degrades quietly: a failed fallback re-raises the original error (never an empty completion), and a
stream that ends with no answer first gets one full non-streaming Argon retry (the hang that opened the breaker
is already reported, so retrying costs 1x the generation timeout, not 2x) before the fallback is used.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from app.config import settings
from app.core import argon_breaker
from app.core.fallback import is_empty_result
from app.core.gemini_client import GOOGLE_WEB_ERROR_PHRASES, UPSTREAM_ERRORS

logger = logging.getLogger(__name__)

SERVED_BY_ARGON = "argon"
SERVED_BY_FLASH = "gemini-flash-thinking"   # the family, not the alias, so clients can branch on it
STREAM_MAY_SUBSTITUTE = "argon-or-gemini-flash-thinking"

# A substitution must be visible to the client, not only in the logs.
SERVED_HEADER = "X-Gemini2API-Argon-Served-By"
REASON_HEADER = "X-Gemini2API-Argon-Fallback-Reason"


def plan_headers(served_by: str) -> dict:
    """Headers for a substitution decided before the request ran (always-fallback / stall breaker)."""
    if served_by == SERVED_BY_ARGON:
        return {}
    return {SERVED_HEADER: served_by, REASON_HEADER: served_by}


def result_headers(result) -> dict:
    """Headers describing an Argon fallback that already happened (empty when Argon answered itself)."""
    marker = result.get("argon_fallback") if isinstance(result, dict) else None
    if not marker:
        return {}
    return {SERVED_HEADER: str(marker.get("served_by") or ""), REASON_HEADER: str(marker.get("reason") or "")}


def stream_headers(is_argon: bool) -> dict:
    """A stream cannot know in advance whether Argon will need the fallback, so the header states the
    possibility; the exact substitution arrives as a comment frame inside the stream (see
    ``generate_stream``'s ``serve_marker`` event)."""
    if not is_argon:
        return {}
    if enabled(is_argon):
        return {SERVED_HEADER: STREAM_MAY_SUBSTITUTE}
    return {SERVED_HEADER: SERVED_BY_ARGON}


def marker_frame(evt: dict) -> str:
    """SSE comment frame announcing that this stream is served by the fallback model."""
    return (f": gemini2api argon_fallback served_by={evt.get('served_by')} "
            f"reason={evt.get('reason')}\n\n")


def is_argon_model(model: str) -> bool:
    return "argon" in (model or "").lower()


def plan(requested_model: str, resolved_model: str) -> tuple[str, bool, bool, str]:
    """Return ``(model_to_use, is_argon, force_thinking, served_by)`` for a request.

    ``served_by`` is ``"argon"`` for a real Argon attempt, otherwise the value that goes into the
    ``X-Gemini2API-Arg-Served-By`` header (``"stall_breaker"`` or ``"argon_always_fallback"``).
    """
    is_argon = is_argon_model(requested_model) or is_argon_model(resolved_model)
    if is_argon and getattr(settings, "argon_always_fallback", False):
        logger.warning(f"Argon requested ('{requested_model}') but ARGON_ALWAYS_FALLBACK=true: "
                       f"serving {settings.argon_fallback_model}")
        return settings.argon_fallback_model, False, True, "argon_always_fallback"
    if is_argon and argon_breaker.is_open():
        snap = argon_breaker.snapshot()
        logger.warning(f"Argon requested ('{requested_model}') but the stall breaker is open "
                       f"({snap.get('reason') or 'stall'}, {snap.get('seconds_left')}s left): "
                       f"serving {settings.argon_fallback_model}")
        return settings.argon_fallback_model, False, True, "stall_breaker"
    return resolved_model, is_argon, False, SERVED_BY_ARGON


def enabled(is_argon: bool) -> bool:
    """True when an Argon failure should be retried on the fallback model."""
    return bool(is_argon and getattr(settings, "argon_fallback_to_flash", True))


def unusable(result: Any) -> bool:
    """Empty reply (no text, no images), or a reply that is just a Google web error message."""
    if is_empty_result(result):
        return True
    text = (result.get("text") or "").strip()
    return bool(text) and any(text.startswith(p) for p in GOOGLE_WEB_ERROR_PHRASES)


def mark(result: dict, served_by: str, reason: str) -> dict:
    """Attach the fallback marker to a result dict (safe on dicts that came out of the pool)."""
    if isinstance(result, dict):
        result["argon_fallback"] = {"served_by": served_by, "reason": reason}
    return result


def is_web_error(result: Any) -> bool:
    text = ((result or {}).get("text") or "").strip() if isinstance(result, dict) else ""
    return bool(text) and any(text.startswith(p) for p in GOOGLE_WEB_ERROR_PHRASES)


async def _generate(pool, prompt, model, attachments, *, thinking=False, conversation_id="",
                    gem_id=None, account_id=None):
    kw = {"extended_thinking": True} if thinking else {}
    return await pool.generate(prompt, model, conversation_id, attachments,
                               gem_id=gem_id, account_id=account_id, **kw)


async def _generate_stream(pool, prompt, model, attachments, *, thinking=False, conversation_id="",
                           gem_id=None, account_id=None):
    kw = {"extended_thinking": True} if thinking else {}
    async for evt in pool.generate_stream(prompt, model, conversation_id, attachments,
                                          gem_id=gem_id, account_id=account_id, **kw):
        yield evt


async def generate(pool, prompt: str, model: str, attachments, *, is_argon: bool, thinking: bool = False,
                   conversation_id: str = "", gem_id=None, account_id=None,
                   served_by: str = SERVED_BY_ARGON) -> dict:
    """``pool.generate`` with the Argon -> Flash fallback.

    Raises the fallback's error when both fail -- never returns an empty result when the fallback was on.
    """
    if not enabled(is_argon):
        return await _generate(pool, prompt, model, attachments, thinking=thinking,
                               conversation_id=conversation_id, gem_id=gem_id, account_id=account_id)

    fb = settings.argon_fallback_model
    try:
        result = await _generate(pool, prompt, model, attachments, thinking=thinking,
                                 conversation_id=conversation_id, gem_id=gem_id, account_id=account_id)
    except UPSTREAM_ERRORS as e:
        logger.warning(f"Argon request failed ({type(e).__name__}: {e}); falling back to {fb}")
        return mark(await _generate(pool, prompt, fb, attachments, thinking=True,
                                    conversation_id=conversation_id, gem_id=gem_id, account_id=account_id),
                    SERVED_BY_FLASH, "argon_error")

    if not unusable(result):
        return result

    why = "empty_reply" if is_empty_result(result) else "google_web_error"
    logger.warning(f"Argon returned {why} ({(result.get('text') or '')[:60]!r}); falling back to {fb}")
    return mark(await _generate(pool, prompt, fb, attachments, thinking=True,
                                conversation_id=conversation_id, gem_id=gem_id, account_id=account_id),
                SERVED_BY_FLASH, why)


async def generate_stream(pool, prompt: str, model: str, attachments, *, is_argon: bool, thinking: bool = False,
                          conversation_id: str = "", gem_id=None, account_id=None,
                          served_by: str = SERVED_BY_ARGON) -> AsyncGenerator[dict, None]:
    """``pool.generate_stream`` with the Argon -> Flash fallback and an explicit serve marker.

    The fallback only starts while nothing has been streamed to the client: an upstream error before the first
    content, or a stream that ends with no answer at all. Once content has gone out, errors propagate unchanged.
    A dead Argon stream first gets one full non-streaming retry on Argon (that request also refreshes the
    conversation state); only if that fails too does Flash take over.
    """
    if not enabled(is_argon):
        async for evt in _generate_stream(pool, prompt, model, attachments, thinking=thinking,
                                          conversation_id=conversation_id, gem_id=gem_id, account_id=account_id):
            yield evt
        return

    fb = settings.argon_fallback_model
    streamed = False
    failure: tuple[str, Exception | None] = ("empty_stream", None)
    try:
        async for evt in _generate_stream(pool, prompt, model, attachments, thinking=thinking,
                                          conversation_id=conversation_id, gem_id=gem_id, account_id=account_id):
            kind = evt.get("type")
            if kind == "delta":
                if evt.get("text"):
                    streamed = True
            elif kind == "thoughts":
                continue  # thoughts alone are not an answer; never let them decide "streamed"
            elif kind == "final":
                if (evt.get("text") or "").strip() or evt.get("images"):
                    streamed = True
                elif not streamed:
                    continue  # empty answer: hold it back, the fallback below replaces it
            yield evt
    except Exception as e:
        if streamed:
            raise
        failure = ("argon_error", e)

    if streamed:
        return

    if failure[1] is not None:
        logger.warning(f"Argon stream failed before the first chunk ({type(failure[1]).__name__}: "
                       f"{failure[1]}); falling back to {fb}")
    else:
        # No exception, just no answer: one plain Argon retry before the model swap. The hang that opened
        # the stall breaker was already reported, so this retry is not the "wait out the same timeout twice"
        # case the breaker exists for -- and it may well be the empty-frame case the retry actually fixes.
        logger.warning(f"Argon stream ended without an answer; retrying Argon once (non-streaming) before {fb}")
        try:
            retry = await _generate(pool, prompt, model, attachments, thinking=thinking,
                                    conversation_id=conversation_id, gem_id=gem_id, account_id=account_id)
            if not unusable(retry):
                text = retry.get("text") or ""
                if text:
                    yield {"type": "delta", "text": text}
                yield {"type": "thoughts", "text": retry.get("thoughts") or ""}
                yield {"type": "final", "text": text, "images": retry.get("images") or [],
                       "thoughts": retry.get("thoughts") or "",
                       "conversation_id": retry.get("conversation_id", "")}
                return
        except UPSTREAM_ERRORS as e2:
            failure = ("argon_error", e2)
        logger.warning(f"Argon retry also produced no answer; falling back to {fb}")

    yield {"type": "serve_marker", "served_by": SERVED_BY_FLASH, "reason": failure[0]}
    async for evt in _generate_stream(pool, prompt, fb, attachments, thinking=True,
                                      conversation_id=conversation_id, gem_id=gem_id, account_id=account_id):
        yield evt
