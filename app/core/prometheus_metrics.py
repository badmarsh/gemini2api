"""Prometheus-compatible exposition of the redacted request diagnostics.

The admin JSON endpoint (``GET /admin/debug-metrics``) stays the source of truth for
the web console; this module renders the same bounded numbers in the Prometheus text
exposition format so an operator can scrape the proxy with a stock agent.

Redaction rules (hard requirements, not preferences):

* no request IDs, no account IDs/hashes, no prompts, no API keys, no cookie material;
* the requested model is client-controlled and therefore never used as a label --
  only the *serving* model (chosen by the server) is, and only after sanitisation;
* every label value is sanitised to a conservative character set, and the number of
  distinct values per label is capped so a client cannot inflate the time series;
* counters are monotonic and derived from the diagnostics store, not from the rolling
  record buffer (the buffer evicts old records, which would make counters decrease).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

# Prometheus text exposition format 0.0.4.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Latency buckets (milliseconds). The upper end covers a full Argon timeout.
LATENCY_BUCKETS = (100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 120000, 300000)
TTFT_BUCKETS = (250, 1000, 2500, 5000, 15000, 30000, 60000, 120000, 300000)
GAP_BUCKETS = (50, 250, 1000, 5000, 15000, 30000, 60000)

# Maximum number of distinct label values per label. Anything beyond that is folded
# into "other" so a hostile client cannot blow up the time-series count.
MAX_LABEL_VALUES = 12

_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9_.:\-]")


def sanitize_label_value(value: Any, *, limit: int = 48) -> str:
    """Return a label value that is safe to embed in the exposition format.

    Quotes, backslashes and newlines are replaced instead of escaped: the exposition
    format would accept them, but keeping them out entirely removes any chance of
    breaking out of a label value.
    """
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    text = _LABEL_UNSAFE.sub("_", text)
    text = text.strip() or "unknown"
    return text[:limit]


def _as_float(value: Any, default: float = 0.0) -> float:
    """Coerce a snapshot value to a float without raising on odd input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_value(value: float) -> str:
    """Render a float without scientific notation surprises."""
    if value == int(value):
        return str(int(value))
    return f"{value:.3f}"


def _labels(pairs: Sequence[tuple[str, str]]) -> str:
    if not pairs:
        return ""
    inner = ",".join(f'{name}="{value}"' for name, value in pairs)
    return "{" + inner + "}"


def _fold(values: Mapping[str, int]) -> dict[str, int]:
    """Keep the ``MAX_LABEL_VALUES`` most common values; fold the rest into "other"."""
    if len(values) <= MAX_LABEL_VALUES:
        return dict(values)
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    kept = dict(ordered[:MAX_LABEL_VALUES])
    kept["other"] = sum(count for _, count in ordered[MAX_LABEL_VALUES:])
    return kept


def _counter_family(
    lines: list[str],
    name: str,
    help_text: str,
    samples: Iterable[tuple[Sequence[tuple[str, str]], float]],
) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} counter")
    for labels, value in samples:
        lines.append(f"{name}{_labels(labels)} {_format_value(value)}")


def _gauge(lines: list[str], name: str, help_text: str, value: float) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name} {_format_value(value)}")


def _histogram(
    lines: list[str],
    name: str,
    help_text: str,
    buckets: Sequence[float],
    values: Sequence[float],
) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} histogram")
    ordered = sorted(float(v) for v in values)
    for bound in buckets:
        count = sum(1 for value in ordered if value <= bound)
        lines.append(f'{name}_bucket{{le="{_format_value(bound)}"}} {_format_value(count)}')
    lines.append(f'{name}_bucket{{le="+Inf"}} {_format_value(len(ordered))}')
    lines.append(f"{name}_sum {_format_value(sum(ordered))}")
    lines.append(f"{name}_count {_format_value(len(ordered))}")


def _counter_values(counters: Mapping[str, int], prefix: str) -> dict[str, int]:
    """Extract the ``prefix:<value>`` counters into a ``{value: count}`` mapping."""
    result: dict[str, int] = {}
    for key, value in counters.items():
        if not key.startswith(prefix + ":"):
            continue
        result[key[len(prefix) + 1:]] = int(value)
    return result


def render_metrics(snapshot: Mapping[str, Any]) -> str:
    """Render the Prometheus exposition text for a diagnostics snapshot.

    ``snapshot`` is assembled by the caller (the admin router) so this module stays
    free of imports that could create cycles with the account pool.
    """
    counters = snapshot.get("counters") or {}
    records = list(snapshot.get("records") or [])
    windows = snapshot.get("windows") or {}
    breaker = snapshot.get("breaker") or {}
    pool = snapshot.get("pool") or {}
    budget = snapshot.get("budget") or {}

    lines: list[str] = []

    _gauge(
        lines,
        "gemini2api_uptime_seconds",
        "Seconds since the proxy process started.",
        _as_float(snapshot.get("uptime_seconds")),
    )

    # Request volume per window and coarse status class. The window label has three
    # possible values, the status class five: both bounded.
    window_samples: list[tuple[Sequence[tuple[str, str]], float]] = []
    error_rate_samples: list[tuple[Sequence[tuple[str, str]], float]] = []
    for window in ("1h", "24h", "7d"):
        data = windows.get(window) or {}
        total = int(data.get("requests") or 0)
        errors = int(data.get("errors") or 0)
        fallbacks = int(data.get("fallbacks") or 0)
        window_samples.append(([("window", window), ("status_class", "error")], errors))
        window_samples.append(([("window", window), ("status_class", "ok")], max(0, total - errors)))
        window_samples.append(([("window", window), ("status_class", "fallback")], fallbacks))
        error_rate_samples.append(([("window", window)], _as_float(data.get("error_rate"))))
    _counter_family(
        lines,
        "gemini2api_requests_observed_total",
        "Requests observed in the retained diagnostics buffer, by window and status class.",
        window_samples,
    )
    lines.append("# HELP gemini2api_error_rate_ratio Share of observed requests that failed, by window.")
    lines.append("# TYPE gemini2api_error_rate_ratio gauge")
    for labels, value in error_rate_samples:
        lines.append(f"gemini2api_error_rate_ratio{_labels(labels)} {_format_value(value)}")

    _counter_family(
        lines,
        "gemini2api_requests_total",
        "Requests observed since process start (monotonic).",
        [([], int(counters.get("requests") or 0))],
    )
    _counter_family(
        lines,
        "gemini2api_request_errors_total",
        "Failed requests observed since process start, by error class.",
        [
            ([("error_class", sanitize_label_value(name))], count)
            for name, count in _fold(_counter_values(counters, "error_class")).items()
        ],
    )
    _counter_family(
        lines,
        "gemini2api_served_by_total",
        "Requests served, by the serving model chosen by the server.",
        [
            ([("served_by", sanitize_label_value(name))], count)
            for name, count in _fold(_counter_values(counters, "served_by")).items()
        ],
    )
    _counter_family(
        lines,
        "gemini2api_fallback_total",
        "Argon to Flash fallbacks, by reason.",
        [([], int(counters.get("fallbacks") or 0))]
        + [
            ([("reason", sanitize_label_value(name))], count)
            for name, count in _fold(_counter_values(counters, "fallback_reason")).items()
        ],
    )
    _counter_family(
        lines,
        "gemini2api_parser_failures_total",
        "Tool-call parser failures, by failure class.",
        [
            ([("failure_class", sanitize_label_value(name))], count)
            for name, count in _fold(_counter_values(counters, "parser_failure")).items()
        ],
    )
    _counter_family(
        lines,
        "gemini2api_regenerations_total",
        "Regenerations granted by the shared per-turn budget.",
        [([], int(counters.get("regenerations") or 0))],
    )
    _counter_family(
        lines,
        "gemini2api_parser_repaired_total",
        "Tool-call payloads repaired by the conservative complete-JSON repair.",
        [([], int(counters.get("parser_repaired") or 0))],
    )
    _counter_family(
        lines,
        "gemini2api_truncated_suspected_total",
        "Responses that looked truncated to the tool-call parser.",
        [([], int(counters.get("truncated_suspected") or 0))],
    )
    _counter_family(
        lines,
        "gemini2api_regeneration_budget_blocked_total",
        "Regeneration attempts refused by the shared per-turn budget.",
        [([], int(budget.get("blocked") or 0))],
    )

    _histogram(
        lines,
        "gemini2api_request_duration_ms",
        "End-to-end request latency in milliseconds (retained buffer).",
        LATENCY_BUCKETS,
        [_as_float(r.get("total_latency_ms")) for r in records],
    )
    _histogram(
        lines,
        "gemini2api_request_ttft_ms",
        "Time to first token in milliseconds (retained buffer).",
        TTFT_BUCKETS,
        [_as_float(r.get("ttft_ms")) for r in records],
    )
    _histogram(
        lines,
        "gemini2api_inter_chunk_gap_ms",
        "Largest gap between two streamed chunks in milliseconds (retained buffer).",
        GAP_BUCKETS,
        [_as_float(r.get("max_inter_chunk_gap_ms")) for r in records],
    )

    _gauge(
        lines,
        "gemini2api_argon_breaker_open",
        "1 while the Argon stall breaker routes requests to the Flash fallback.",
        1.0 if breaker.get("open") else 0.0,
    )
    _counter_family(
        lines,
        "gemini2api_argon_breaker_trips_total",
        "Argon stall breaker trips since process start.",
        [([], int(breaker.get("trips") or 0))],
    )
    _gauge(
        lines,
        "gemini2api_argon_breaker_seconds_left",
        "Seconds until the Argon stall breaker closes again.",
        _as_float(breaker.get("seconds_left")),
    )

    lines.append("# HELP gemini2api_account_pool_accounts Accounts in the pool, by status.")
    lines.append("# TYPE gemini2api_account_pool_accounts gauge")
    for state in ("active", "expired", "disabled", "refreshing"):
        value = _as_float(pool.get(f"accounts_{state}"))
        lines.append(f'gemini2api_account_pool_accounts{{state="{state}"}} {_format_value(value)}')
    _gauge(
        lines,
        "gemini2api_account_pool_active_requests",
        "Requests currently holding an account slot.",
        _as_float(pool.get("active_requests")),
    )
    _gauge(
        lines,
        "gemini2api_account_pool_capacity",
        "Total number of account slots in the pool.",
        _as_float(pool.get("capacity")),
    )
    _gauge(
        lines,
        "gemini2api_account_pool_saturation_ratio",
        "Share of account slots currently in use (0..1).",
        _as_float(pool.get("saturation")),
    )
    _gauge(
        lines,
        "gemini2api_account_pool_slot_leak_accounts",
        "Accounts holding more slots than the configured per-account limit (leak indicator).",
        _as_float(pool.get("slot_leak_accounts")),
    )
    _gauge(
        lines,
        "gemini2api_account_pool_cooling_accounts",
        "Accounts in failover cooldown.",
        _as_float(pool.get("cooling_accounts")),
    )

    return "\n".join(lines) + "\n"
