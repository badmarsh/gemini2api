"""Operational SLO view for the admin console.

The dashboard used to show a green "everything is fine" as long as the endpoints
answered. That is ambiguous: an idle service, a service whose requests are all
served by the Flash fallback and a service whose requests all fail look the same.
This module turns the bounded diagnostics into an explicit state:

``ok``       - requests were observed and none of the degradation rules fired;
``degraded`` - at least one rule fired (the reasons are listed);
``unknown`` - nothing was observed at all, so nothing can be claimed either way.

The thresholds are deliberately lenient where the product is lenient: an Argon
stream may legitimately need 30-75 s before its first token, so the TTFT rule only
fires far above that.
"""
from __future__ import annotations

# Share of failed requests in the 1h window that marks the service degraded.
ERROR_RATE_DEGRADED = 0.10
# Share of the 1h window that had to be served by the Flash fallback.
FALLBACK_RATE_DEGRADED = 0.50
# p95 time to first token (ms). Argon regularly needs 30-75 s, so this is far above
# the accepted Argon behaviour and only fires on a real stall.
TTFT_P95_DEGRADED_MS = 120_000.0
# Longest tolerated silence between two streamed chunks (ms).
INTER_CHUNK_GAP_DEGRADED_MS = 30_000.0

WINDOW = "1h"


def build_slo(
    *,
    windows: dict | None = None,
    breaker: dict | None = None,
    pool: dict | None = None,
    budget: dict | None = None,
    lifetime_requests: int = 0,
) -> dict:
    """Assemble the operational view. Pure: it never raises on partial input."""
    windows = windows or {}
    breaker = breaker or {}
    pool = pool or {}
    budget = budget or {}

    current = windows.get(WINDOW) or {}
    requests_1h = int(current.get("requests") or 0)
    error_rate = float(current.get("error_rate") or 0.0)
    fallback_rate = float(current.get("fallback_rate") or 0.0)
    ttft_p95 = float((current.get("ttft_ms") or {}).get("p95") or 0.0)
    max_gap = float(current.get("max_inter_chunk_gap_ms") or 0.0)
    blocked = int(budget.get("blocked") or 0)

    reasons: list[str] = []
    if breaker.get("open"):
        reasons.append("breaker_open")
    if requests_1h and error_rate >= ERROR_RATE_DEGRADED:
        reasons.append("error_rate")
    if requests_1h and fallback_rate >= FALLBACK_RATE_DEGRADED:
        reasons.append("fallback_rate")
    if requests_1h and ttft_p95 >= TTFT_P95_DEGRADED_MS:
        reasons.append("ttft_p95")
    if requests_1h and max_gap >= INTER_CHUNK_GAP_DEGRADED_MS:
        reasons.append("inter_chunk_gap")
    if pool.get("saturated"):
        reasons.append("pool_saturated")
    if int(pool.get("slot_leak_accounts") or 0) > 0:
        reasons.append("pool_slot_leak")
    if blocked > 0:
        reasons.append("regeneration_budget_exhausted")

    if not lifetime_requests:
        state = "unknown"
    elif reasons:
        state = "degraded"
    else:
        state = "ok"

    return {
        "state": state,
        "degraded": state == "degraded",
        "reasons": reasons,
        "windows": windows,
        "targets": {
            "error_rate": ERROR_RATE_DEGRADED,
            "fallback_rate": FALLBACK_RATE_DEGRADED,
            "ttft_p95_ms": TTFT_P95_DEGRADED_MS,
            "inter_chunk_gap_ms": INTER_CHUNK_GAP_DEGRADED_MS,
            "window": WINDOW,
        },
        "breaker": {
            "open": bool(breaker.get("open")),
            "seconds_left": float(breaker.get("seconds_left") or 0.0),
            "trips": int(breaker.get("trips") or 0),
            "reason": str(breaker.get("reason") or ""),
            "enabled": bool(breaker.get("enabled")),
        },
        "pool": {
            "accounts": int(pool.get("accounts") or 0),
            "accounts_active": int(pool.get("accounts_active") or 0),
            "capacity": int(pool.get("capacity") or 0),
            "active_requests": int(pool.get("active_requests") or 0),
            "saturation": float(pool.get("saturation") or 0.0),
            "saturated": bool(pool.get("saturated")),
            "cooling_accounts": int(pool.get("cooling_accounts") or 0),
            "cooldown_seconds_max": float(pool.get("cooldown_seconds_max") or 0.0),
            "slot_leak_accounts": int(pool.get("slot_leak_accounts") or 0),
            "max_concurrent_per_account": int(pool.get("max_concurrent_per_account") or 0),
        },
        "budget": {
            "limit_per_turn": int(budget.get("limit_per_turn") or 0),
            "allowed": int(budget.get("allowed") or 0),
            "blocked": blocked,
            "exhausted": blocked > 0,
            "last_reason": str(budget.get("last_reason") or ""),
        },
    }
