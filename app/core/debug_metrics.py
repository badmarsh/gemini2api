"""Bounded, secret-free request diagnostics for the admin console and local eval harness."""
from __future__ import annotations

from collections import Counter, deque
from threading import Lock


_TTFT_BUCKETS = (
    (0, 250, "0-250ms"),
    (250, 1000, "250ms-1s"),
    (1000, 5000, "1-5s"),
    (5000, 30000, "5-30s"),
    (30000, 120000, "30-120s"),
    (120000, float("inf"), "120s+")
)


class DebugMetricsStore:
    def __init__(self, capacity: int = 2000):
        self._lock = Lock()
        self._records: deque[dict] = deque(maxlen=capacity)

    def record(self, record: dict) -> None:
        """Store a normalized copy; callers may safely reuse their input dict."""
        safe = {
            "request_id": str(record.get("request_id") or ""),
            "ts": str(record.get("ts") or ""),
            "model": str(record.get("model") or ""),
            "served_by": str(record.get("served_by") or ""),
            "account_hash": str(record.get("account_hash") or ""),
            "status": int(record.get("status") or 0),
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
        }
        with self._lock:
            self._records.append(safe)

    def records(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return list(self._records)[-max(1, min(limit, 2000)):]

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
        return {
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

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


metrics = DebugMetricsStore()
