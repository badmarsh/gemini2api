"""Bounded, secret-free request diagnostics for the admin console and local eval harness."""
from __future__ import annotations

import time
from collections import Counter, deque
from datetime import datetime
from threading import Lock


_TTFT_BUCKETS = (
    (0, 250, "0-250ms"),
    (250, 1000, "250ms-1s"),
    (1000, 5000, "1-5s"),
    (5000, 30000, "5-30s"),
    (30000, 120000, "30-120s"),
    (120000, float("inf"), "120s+")
)

# Rolling windows the operational dashboard and the Prometheus exporter report.
# The order is fixed so the UI and the exporter agree on the window names.
WINDOW_SECONDS: tuple[tuple[str, int], ...] = (
    ("1h", 3600),
    ("24h", 86400),
    ("7d", 604800),
)


def _parse_ts(value: str) -> float | None:
    """Parse an ISO-8601 timestamp into a POSIX timestamp (``None`` when unusable)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def _percentile(sorted_values: list[float], quantile: float) -> float:
    """Nearest-rank percentile of an already sorted list (0.0 for an empty list)."""
    if not sorted_values:
        return 0.0
    index = int(len(sorted_values) * quantile)
    if index >= len(sorted_values):
        index = len(sorted_values) - 1
    return sorted_values[index]


def _status_class(status: int) -> str:
    """Coarse HTTP status class so the exporter never needs a per-code label."""
    if not status:
        return "other"
    if 200 <= status < 300:
        return "2xx"
    if 300 <= status < 400:
        return "3xx"
    if 400 <= status < 500:
        return "4xx"
    if 500 <= status < 600:
        return "5xx"
    return "other"


class DebugMetricsStore:
    def __init__(self, capacity: int = 2000):
        self._lock = Lock()
        self._records: deque[dict] = deque(maxlen=capacity)
        # Monotonic counters. The record buffer is a rolling window (old records are
        # evicted), so lifetime totals are kept separately: a Prometheus counter must
        # never decrease, and the buffer would make it decrease on every eviction.
        self._counters: Counter = Counter()

    def record(self, record: dict) -> None:
        """Store a normalized copy; callers may safely reuse their input dict."""
        safe = {
            "request_id": str(record.get("request_id") or ""),
            "ts": str(record.get("ts") or ""),
            "ingress_ts": str(record.get("ingress_ts") or ""),
            "model": str(record.get("model") or ""),
            "served_by": str(record.get("served_by") or ""),
            "account_hash": str(record.get("account_hash") or ""),
            "status": int(record.get("status") or 0),
            "stream": bool(record.get("stream")),
            "path": str(record.get("path") or ""),
            "ttft_ms": round(float(record.get("ttft_ms") or 0), 1),
            "total_latency_ms": round(float(record.get("total_latency_ms") or 0), 1),
            "max_inter_chunk_gap_ms": round(float(record.get("max_inter_chunk_gap_ms") or 0), 1),
            "error_class": str(record.get("error_class") or ""),
            "fallback_used": bool(record.get("fallback_used")),
            "fallback_reason": str(record.get("fallback_reason") or ""),
            "regenerations": int(record.get("regenerations") or 0),
            "finish_reason": str(record.get("finish_reason") or ""),
            "parser_failure": str(record.get("parser_failure") or ""),
            "parser_response_length": int(record.get("parser_response_length") or 0),
            "parser_repaired": bool(record.get("parser_repaired")),
            "truncated_suspected": bool(record.get("truncated_suspected")),
            "tags": [str(t) for t in (record.get("tags") or []) if isinstance(t, str)][:2],
        }
        with self._lock:
            self._records.append(safe)
            self._counters["requests"] += 1
            self._counters[f"status_class:{_status_class(safe['status'])}"] += 1
            if safe["error_class"]:
                self._counters[f"error_class:{safe['error_class']}"] += 1
            if safe["served_by"]:
                self._counters[f"served_by:{safe['served_by']}"] += 1
            if safe["fallback_used"]:
                self._counters["fallbacks"] += 1
                if safe["fallback_reason"]:
                    self._counters[f"fallback_reason:{safe['fallback_reason']}"] += 1
            self._counters["regenerations"] += safe["regenerations"]
            if safe["parser_failure"]:
                self._counters[f"parser_failure:{safe['parser_failure']}"] += 1
            if safe["parser_repaired"]:
                self._counters["parser_repaired"] += 1
            if safe["truncated_suspected"]:
                self._counters["truncated_suspected"] += 1

    def counters(self) -> dict:
        """Monotonic counters as ``{name: value}`` (no labels, no identifiers).

        Names are prefixed by dimension (``status_class:2xx``) so the Prometheus
        exporter can turn them into labels without re-deriving anything.
        """
        with self._lock:
            return dict(self._counters)

    def records(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return list(self._records)[-max(1, min(limit, 2000)):]

    def record_by_id(self, request_id: str) -> dict | None:
        """The newest record with this request ID (``None`` when it is not retained).

        The buffer is bounded, so an old request is simply not found -- the caller
        reports that instead of inventing data.
        """
        wanted = str(request_id or "").strip()
        if not wanted:
            return None
        with self._lock:
            for record in reversed(self._records):
                if record.get("request_id") == wanted:
                    return dict(record)
        return None

    def window_summary(self, now: float | None = None) -> dict:
        """Per-window request volume, error rate, fallback rate and latency percentiles.

        The windows are computed from the retained (bounded) record buffer, so they
        describe "what the diagnostics buffer still holds", not an eternal history.
        Records without a parsable timestamp are excluded from the windows but stay
        in the lifetime counters.
        """
        now = time.time() if now is None else now
        with self._lock:
            records = list(self._records)
        windows: dict[str, dict] = {}
        for label, seconds in WINDOW_SECONDS:
            cutoff = now - seconds
            selected = []
            for record in records:
                ts = _parse_ts(record.get("ts", ""))
                if ts is not None and ts >= cutoff:
                    selected.append(record)
            windows[label] = _summarize_records(selected)
        return windows

    def summary(self) -> dict:
        with self._lock:
            records = list(self._records)
        errors = Counter(r["error_class"] or "ok" for r in records)
        served = Counter(r["served_by"] or "unknown" for r in records)
        parser_failures = Counter(r["parser_failure"] for r in records if r["parser_failure"])
        histogram = {label: 0 for _, _, label in _TTFT_BUCKETS}
        for record in records:
            value = record["ttft_ms"]
            for low, high, label in _TTFT_BUCKETS:
                if low <= value < high:
                    histogram[label] += 1
                    break
        latency = sorted(r["total_latency_ms"] for r in records)
        p95 = latency[min(len(latency) - 1, int(len(latency) * 0.95))] if latency else 0.0
        summary = {
            "record_count": len(records),
            "ttft_histogram_ms": histogram,
            "served_by": dict(served),
            "fallback_count": sum(1 for r in records if r["fallback_used"]),
            "error_classes": dict(errors),
            "parser_failures": dict(parser_failures),
            "parser_repaired_count": sum(1 for r in records if r["parser_repaired"]),
            "truncated_suspected_count": sum(1 for r in records if r["truncated_suspected"]),
            "regenerations": sum(r["regenerations"] for r in records),
            "latency_ms": {
                "p50": latency[(len(latency) - 1) // 2] if latency else 0.0,
                "p95": p95,
                "max": latency[-1] if latency else 0.0,
            },
        }
        summary.update(_summarize_records(records))
        return summary

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._counters.clear()


def _summarize_records(records: list[dict]) -> dict:
    """Aggregate a list of normalized records into rates and percentiles."""
    total = len(records)
    errors = sum(1 for r in records if r.get("error_class") or (r.get("status") or 0) >= 400)
    fallbacks = sum(1 for r in records if r.get("fallback_used"))
    ttft = sorted(float(r.get("ttft_ms") or 0) for r in records)
    latency = sorted(float(r.get("total_latency_ms") or 0) for r in records)
    gaps = [float(r.get("max_inter_chunk_gap_ms") or 0) for r in records]
    return {
        "requests": total,
        "errors": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "fallbacks": fallbacks,
        "fallback_rate": round(fallbacks / total, 4) if total else 0.0,
        "regenerations": sum(int(r.get("regenerations") or 0) for r in records),
        "parser_failures": sum(1 for r in records if r.get("parser_failure")),
        "ttft_ms": {
            "p50": _percentile(ttft, 0.50),
            "p95": _percentile(ttft, 0.95),
            "max": ttft[-1] if ttft else 0.0,
        },
        "latency_ms": {
            "p50": _percentile(latency, 0.50),
            "p95": _percentile(latency, 0.95),
            "max": latency[-1] if latency else 0.0,
        },
        "max_inter_chunk_gap_ms": round(max(gaps), 1) if gaps else 0.0,
    }


metrics = DebugMetricsStore()
