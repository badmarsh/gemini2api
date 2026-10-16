"""Short-lived circuit breaker for Argon stalls.

A hung Argon request burns its full timeout (360 s non-stream, 240 s to the first chunk) and the account is only
cooled down for a few seconds, so every request that follows a stall waits out the same timeout again. With the
Flash fallback on, requests arriving within ``argon_stall_breaker_seconds`` of a stall go straight to Flash; the
first request after the window probes Argon again (a stall re-trips the breaker, a success needs no action).
Set ``ARGON_STALL_BREAKER_SECONDS=0`` to disable.
"""
from __future__ import annotations

import logging
import time

from app.config import settings

logger = logging.getLogger(__name__)

_open_until = 0.0


def _ttl() -> float:
    try:
        return max(0.0, float(getattr(settings, "argon_stall_breaker_seconds", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def trip() -> None:
    """Record an Argon stall (a request that hit its timeout)."""
    global _open_until
    ttl = _ttl()
    if ttl > 0:
        _open_until = time.monotonic() + ttl
        logger.warning(f"Argon stalled: serving the Flash fallback for the next {ttl:.0f}s")


def is_open() -> bool:
    """True while Argon requests should be served by the Flash fallback instead."""
    return (_ttl() > 0
            and bool(getattr(settings, "argon_fallback_to_flash", True))
            and time.monotonic() < _open_until)


def reset() -> None:
    global _open_until
    _open_until = 0.0
