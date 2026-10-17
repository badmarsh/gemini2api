"""Correlated timeline for a single request, derived from the bounded diagnostics.

What the request explorer shows:

* ingress and egress timestamps plus the offsets between them;
* time to first token, total duration and the largest gap between two chunks;
* the requested model next to the model that actually served the request;
* the fallback reason, the tool-call parser failure class and the regeneration count;
* the stream markers (fallback, regeneration, parser repair/truncation) and the
  finish reason.

What it never shows: request or response bodies, prompts, cookies, Authorization
headers and account identifiers. The account hash that the diagnostics store keeps
is deliberately dropped here -- the explorer must not identify an account.
"""
from __future__ import annotations

from typing import Any, Mapping

# Fields that must never leave the diagnostics store through this view.
_FORBIDDEN = ("account_hash", "request", "response", "authorization", "cookie")


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_timeline(record: Mapping[str, Any] | None) -> dict | None:
    """Build the redacted timeline for one diagnostics record."""
    if record is None:
        return None
    total_ms = _as_float(record.get("total_latency_ms"))
    ttft_ms = _as_float(record.get("ttft_ms"))
    gap_ms = _as_float(record.get("max_inter_chunk_gap_ms"))

    requested_model = str(record.get("model") or "")
    serving_model = str(record.get("served_by") or "")

    events: list[dict] = []
    if record.get("ingress_ts"):
        events.append({"at_ms": 0.0, "kind": "ingress"})
    if ttft_ms:
        events.append({"at_ms": round(ttft_ms, 1), "kind": "first_token"})
    if gap_ms:
        events.append({"at_ms": round(total_ms, 1), "kind": "max_gap"})
    events.append({"at_ms": round(total_ms, 1), "kind": "egress"})

    markers: list[dict] = []
    if record.get("fallback_used"):
        markers.append({
            "kind": "fallback",
            "detail": str(record.get("fallback_reason") or ""),
        })
    regenerations = int(record.get("regenerations") or 0)
    if regenerations:
        markers.append({"kind": "regeneration", "detail": str(regenerations)})
    if record.get("parser_failure"):
        markers.append({
            "kind": "parser_failure",
            "detail": str(record.get("parser_failure")),
        })
    if record.get("parser_repaired"):
        markers.append({"kind": "parser_repair", "detail": ""})
    if record.get("truncated_suspected"):
        markers.append({"kind": "truncated_suspected", "detail": ""})

    timeline = {
        "request_id": str(record.get("request_id") or ""),
        "ingress_ts": str(record.get("ingress_ts") or ""),
        "egress_ts": str(record.get("ts") or ""),
        "duration_ms": round(total_ms, 1),
        "ttft_ms": round(ttft_ms, 1),
        "max_inter_chunk_gap_ms": round(gap_ms, 1),
        "requested_model": requested_model,
        "serving_model": serving_model,
        "model_substituted": bool(serving_model) and serving_model != requested_model,
        "status": int(record.get("status") or 0),
        "error_class": str(record.get("error_class") or ""),
        "stream": bool(record.get("stream")),
        "path": str(record.get("path") or ""),
        "fallback": {
            "used": bool(record.get("fallback_used")),
            "reason": str(record.get("fallback_reason") or ""),
        },
        "parser": {
            "failure_class": str(record.get("parser_failure") or ""),
            "repaired": bool(record.get("parser_repaired")),
            "truncated_suspected": bool(record.get("truncated_suspected")),
            "response_length": int(record.get("parser_response_length") or 0),
        },
        "regenerations": regenerations,
        "finish_reason": str(record.get("finish_reason") or ""),
        "events": events,
        "markers": markers,
    }
    for key in _FORBIDDEN:
        timeline.pop(key, None)
    return timeline
