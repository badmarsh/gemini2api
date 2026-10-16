"""Per-turn regeneration budget shared by the agent-loop guard and tool parser.

A tool request can pass through both safeguards.  They must share one token: otherwise a
promise correction followed by malformed-JSON retry silently stacks two expensive model
calls on top of the original generation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock


_lock = Lock()
_total_allowed = 0
_total_blocked = 0
_by_reason: dict[str, int] = {}
_last_reason = ""
_last_at = ""


class RegenerationBudget:
    """Allow at most ``limit`` regeneration calls for one logical request turn."""

    def __init__(self, limit: int = 1):
        self.limit = max(0, int(limit))
        self.used = 0
        self.blocked = 0
        self.last_reason = ""

    def try_consume(self, reason: str) -> bool:
        """Consume one token and record whether the call was allowed."""
        global _total_allowed, _total_blocked, _last_reason, _last_at
        reason = reason or "unknown"
        allowed = self.used < self.limit
        if allowed:
            self.used += 1
        else:
            self.blocked += 1
        self.last_reason = reason
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with _lock:
            if allowed:
                _total_allowed += 1
                _by_reason[reason] = _by_reason.get(reason, 0) + 1
            else:
                _total_blocked += 1
            _last_reason = reason
            _last_at = now
        return allowed

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def snapshot(self) -> dict:
        return {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "blocked": self.blocked,
            "last_reason": self.last_reason,
        }


def stats() -> dict:
    """Return process-wide counters suitable for the admin/debug panel."""
    with _lock:
        return {
            "limit_per_turn": 1,
            "allowed": _total_allowed,
            "blocked": _total_blocked,
            "by_reason": dict(_by_reason),
            "last_reason": _last_reason,
            "last_at": _last_at,
        }


def reset_stats() -> None:
    global _total_allowed, _total_blocked, _by_reason, _last_reason, _last_at
    with _lock:
        _total_allowed = 0
        _total_blocked = 0
        _by_reason = {}
        _last_reason = ""
        _last_at = ""
