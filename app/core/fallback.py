"""Gemini-primary → third-party fallback chain.

The gateway serves Gemini models by default. When a Gemini-backed request fails
(error / exhausted account failover) or returns an *empty* response, the same
request is retried natively against the third-party providers the operator has
added to the API-key pool. The client only ever sees one model name, so
reliability is handled transparently inside the gateway — no client-side
switching required, and it applies to ANY Gemini model (flash / pro / thinking).

Selection is automatic and provider-agnostic:
  * candidates come from whatever the operator added to the API-key pool;
  * obviously non-chat models (image / video / audio / embedding ...) are skipped
    so a text/tool request never falls back onto an image model;
  * the remaining candidates are tried in random order, and a candidate that
    errors or returns nothing is skipped for the next one ("one fails → try
    another") until one succeeds.

Config (see app.config.Settings):
    FALLBACK_ENABLED  master on/off switch (default off → zero behaviour change)
    FALLBACK_MODELS   OPTIONAL override. Leave empty to auto-use every suitable
                      third-party in the pool. Set a comma-separated, ordered
                      list to pin specific models (tried in the given order).
"""

import random
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Substrings that mark a pool model as NOT suitable for a chat/tool fallback.
# Matched case-insensitively against the model name. Keep conservative — only
# clearly non-text modalities — so real chat models are never excluded.
_NON_CHAT_HINTS = (
    "image", "video", "audio", "speech", "voice", "tts", "stt",
    "whisper", "embed", "rerank", "dall-e", "dalle", "diffusion",
    "imagen", "veo", "sora", "midjourney", "kling", "moderation", "ocr",
)


def fallback_enabled() -> bool:
    """Master switch. Off by default so existing deployments are unaffected."""
    return bool(getattr(settings, "fallback_enabled", False))


def parse_fallback_models(raw: Optional[str]) -> list[str]:
    """Split comma-separated FALLBACK_MODELS into an ordered, blank-free list."""
    if not raw:
        return []
    return [name.strip() for name in raw.split(",") if name.strip()]


def looks_chat_capable(model: str) -> bool:
    """Heuristic: True unless the model name clearly denotes a non-chat modality."""
    m = (model or "").lower()
    return not any(hint in m for hint in _NON_CHAT_HINTS)


def is_empty_result(result) -> bool:
    """True when a Gemini result carries no usable payload.

    On the Gemini path tool calls are embedded in the text, so empty text with no
    images means the model produced nothing actionable — exactly the "Empty
    response" symptom that should trigger fallback.
    """
    if not isinstance(result, dict):
        return True
    text = (result.get("text") or "").strip()
    images = result.get("images") or []
    return not text and not images


EMPTY_RESPONSE_MESSAGE = "Upstream returned an empty response (no text, no images)"


def is_blank_answer(result) -> bool:
    """is_empty_result() that also carries no reasoning text: nothing a client could show or act on.

    Routers use it once every fallback option is exhausted, to answer with a retryable error instead of an HTTP 200
    with empty content (a client cannot tell that from a real reply, and cannot retry it)."""
    if not is_empty_result(result):
        return False
    return not (isinstance(result, dict) and str(result.get("thoughts") or "").strip())


def openai_data_is_empty(data) -> bool:
    """True when an OpenAI-style response dict has neither content nor tool_calls
    (so a fallback candidate that "answered with nothing" is skipped too)."""
    if not isinstance(data, dict):
        return True
    choices = data.get("choices") or []
    if not choices:
        return True
    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    tool_calls = message.get("tool_calls")
    reasoning = (message.get("reasoning_content") or "").strip()
    return not content and not tool_calls and not reasoning


def select_fallback_candidates(entries: list, names: list[str]) -> list:
    """Pure candidate selection (no I/O — unit-tested).

    `entries` are API-key-pool entries (objects with .model / .status).
      * explicit `names` (pinned via FALLBACK_MODELS): pick those active entries
        in the given order — the operator chose them, so no modality filtering;
      * otherwise: every active, chat-capable entry, in random order.
    """
    active = [e for e in entries if getattr(e, "status", "active") == "active"]
    if names:
        by_model: dict = {}
        for e in active:
            by_model.setdefault(e.model, e)
        return [by_model[n] for n in names if n in by_model]
    candidates = [e for e in active if looks_chat_capable(e.model)]
    random.shuffle(candidates)
    return candidates


def get_fallback_entries(app_state, exclude_model: Optional[str] = None) -> list:
    """Resolve the ordered list of pool entries to try as fallback.

    Auto by default (all suitable third-parties); honours FALLBACK_MODELS when set.
    `exclude_model` drops any entry that matches the originally-requested model
    (defence against accidental self-routing).
    """
    pool = getattr(app_state, "api_key_pool", None)
    if pool is None:
        return []
    names = parse_fallback_models(getattr(settings, "fallback_models", ""))
    candidates = select_fallback_candidates(list(pool.entries.values()), names)
    if exclude_model:
        candidates = [e for e in candidates if e.model != exclude_model]
    return candidates
