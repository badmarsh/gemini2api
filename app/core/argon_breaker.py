"""Circuit breaker for Argon stalls, plus the state the admin panel reports.

A hung Argon request burns its whole timeout (``ARGON_TIMEOUT``, 360 s by default) and the account is only
cooled down for a few seconds, so every request that follows a stall would wait out the same timeout again.
While the breaker is open, Argon requests are served by ``settings.argon_fallback_model`` -- and that
substitution is never silent: ``argon_fallback`` marks the response (``X-Gemini2API-*`` headers, a
``serve_marker`` frame in streams) and logs it. ``GET /admin/status`` reports this state, ``POST
/admin/argon/reset`` closes the breaker by hand (useful right after a Google-side incident).

Set ``ARGON_STALL_BREAKER_SECONDS=0`` to disable the breaker, or ``ARGON_FALLBACK_TO_FLASH=false`` to remove
the substitution entirely (Argon errors are then surfaced to the client as-is).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_open_until = 0.0            # time.monotonic() deadline, 0.0 = closed
_reason = ""                 # machine-readable tag of the stall that opened it
_note = ""                   # short human hint (exception type), never a full traceback
_opened_at: float | None = None
_trips = 0
_last_trip_wall: str = ""


def _ttl() -> float:
    try:
        return max(0.0, float(getattr(settings, "argon_stall_breaker_seconds", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def enabled() -> bool:
    """True when a stall can open the breaker at all (TTL set and the Flash fallback available)."""
    return _ttl() > 0 and bool(getattr(settings, "argon_fallback_to_flash", True))


def trip(reason: str = "timeout", note: str = "") -> None:
    """Record an Argon stall and open the breaker for ``ARGON_STALL_BREAKER_SECONDS``.

    ``reason`` is a short tag ("first-frame", "timeout"), ``note`` an optional hint such as the exception type.
    """
    global _open_until, _reason, _note, _opened_at, _trips, _last_trip_wall
    ttl = _ttl()
    with _lock:
        _trips += 1
        _last_trip_wall = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if ttl <= 0:
            return
        _open_until = time.monotonic() + ttl
        _reason = reason or "timeout"
        _note = note or ""
        _opened_at = time.time()
    logger.warning(
        f"Argon stalled ({reason or 'timeout'}{', ' + note if note else ''}): serving the Flash fallback "
        f"for the next {ttl:.0f}s (ARGON_STALL_BREAKER_SECONDS)"
    )


def is_open() -> bool:
    """True while Argon requests should be served by the Flash fallback instead."""
    return (enabled()
            and bool(getattr(settings, "argon_fallback_to_flash", True))
            and time.monotonic() < _open_until)


def reset() -> None:
    """Close the breaker (manual reset, and test isolation)."""
    global _open_until, _reason, _note, _opened_at
    with _lock:
        _open_until = 0.0
        _reason = ""
        _note = ""
        _opened_at = None


def snapshot() -> dict:
    """Breaker state for ``/admin/status`` -- never raises, safe to call from a request handler."""
    open_ = is_open()
    left = max(0.0, _open_until - time.monotonic())
    return {
        "enabled": enabled(),
        "open": open_,
        "ttl_seconds": _ttl(),
        "seconds_left": round(left, 1) if open_ else 0.0,
        "reason": _reason if open_ else "",
        "note": _note if open_ else "",
        "opened_at": (datetime.fromtimestamp(_opened_at, timezone.utc).isoformat(timespec="seconds")
                      if (open_ and _opened_at) else ""),
        "trips": _trips,
        "last_trip_at": _last_trip_wall,
    }
