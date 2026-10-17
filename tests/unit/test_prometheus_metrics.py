"""Prometheus-compatible metrics surface: format, redaction and cardinality.

The exporter is a rendering of the existing redacted diagnostics store. These tests
pin the properties an operator (and a scraper) depends on:

* valid Prometheus text exposition format;
* no request IDs, account hashes, prompts, cookies or API keys;
* low cardinality: the requested (client-controlled) model is never a label;
* monotonic counters that do not decrease when the rolling buffer evicts records.
"""
from __future__ import annotations

import re

import pytest


def _parse(text: str) -> list[tuple[str, str]]:
    """Return the (name, sample) pairs of an exposition body, ignoring comments."""
    samples = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_:][A-Za-z0-9_:]*)\{?([^}]*)\}?\s+(\S+)$", line)
        assert match, f"unparsable exposition line: {line!r}"
        samples.append((match.group(1), match.group(2)))
    return samples


def _families(text: str) -> dict[str, list[str]]:
    families: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            parts = line.split()
            families.setdefault(parts[2], []).append(parts[3])
    return families


def test_exposition_has_no_duplicate_help_or_type_blocks():
    from app.core.prometheus_metrics import render_metrics

    text = render_metrics({
        "uptime_seconds": 12.5,
        "counters": {"requests": 3, "status_class:2xx": 3},
        "records": [{"ttft_ms": 10, "total_latency_ms": 100, "max_inter_chunk_gap_ms": 5}],
        "windows": {"1h": {"requests": 3, "errors": 1, "fallbacks": 1, "error_rate": 0.33}},
        "breaker": {"open": True, "trips": 2, "seconds_left": 12.0},
        "pool": {"accounts_active": 2, "capacity": 16, "active_requests": 3, "saturation": 0.2},
        "budget": {"blocked": 1},
    })
    # A metric family may only be declared once (HELP/TYPE are per family), and every
    # family must carry a supported type.
    declares = [line for line in text.splitlines() if line.startswith("# TYPE ")]
    assert len(declares) == len(set(declares))
    for name, types in _families(text).items():
        assert len(types) == 1, name
        assert types[0] in ("counter", "gauge", "histogram"), name


def test_exposition_contains_the_operational_families():
    from app.core.prometheus_metrics import render_metrics

    text = render_metrics({
        "uptime_seconds": 1.0,
        "counters": {"requests": 1, "fallbacks": 1, "fallback_reason:stall_breaker": 1},
        "records": [{"ttft_ms": 5, "total_latency_ms": 50, "max_inter_chunk_gap_ms": 2}],
        "windows": {"1h": {"requests": 1, "errors": 0, "fallbacks": 1, "error_rate": 0.0}},
        "breaker": {"open": False, "trips": 1, "seconds_left": 0.0},
        "pool": {"accounts_active": 1, "capacity": 8, "active_requests": 0, "saturation": 0.0},
        "budget": {"blocked": 0},
    })
    for family in (
        "gemini2api_uptime_seconds",
        "gemini2api_requests_total",
        "gemini2api_requests_observed_total",
        "gemini2api_error_rate_ratio",
        "gemini2api_request_duration_ms",
        "gemini2api_request_ttft_ms",
        "gemini2api_inter_chunk_gap_ms",
        "gemini2api_fallback_total",
        "gemini2api_argon_breaker_open",
        "gemini2api_account_pool_accounts",
        "gemini2api_account_pool_saturation_ratio",
        "gemini2api_account_pool_slot_leak_accounts",
        "gemini2api_regenerations_total",
        "gemini2api_parser_failures_total",
    ):
        assert f"# TYPE {family} " in text, family
    assert "gemini2api_request_duration_ms_count 1" in text
    assert 'gemini2api_fallback_total{reason="stall_breaker"} 1' in text


def test_exposition_never_leaks_identifiers_or_secrets():
    from app.core.prometheus_metrics import render_metrics

    text = render_metrics({
        "uptime_seconds": 1.0,
        "counters": {
            "requests": 1,
            "served_by:argon": 1,
            "error_class:upstream_error": 1,
        },
        "records": [{
            "request_id": "deadbeefcafe",
            "account_hash": "9f8e7d6c5b4a",
            "model": "client-chosen-model-name",
            "served_by": "argon",
            "ttft_ms": 5,
            "total_latency_ms": 50,
            "max_inter_chunk_gap_ms": 2,
        }],
        "windows": {},
        "breaker": {},
        "pool": {},
        "budget": {},
    })
    assert "deadbeefcafe" not in text
    assert "9f8e7d6c5b4a" not in text
    assert "client-chosen-model-name" not in text
    assert "psid" not in text.lower()
    assert "authorization" not in text.lower()
    # The serving model is server-controlled and may be a label.
    assert 'served_by="argon"' in text


def test_label_cardinality_is_capped():
    from app.core.prometheus_metrics import MAX_LABEL_VALUES, render_metrics

    counters = {"requests": 30}
    for index in range(30):
        counters[f"served_by:model-{index}"] = 1
    text = render_metrics({
        "uptime_seconds": 1.0,
        "counters": counters,
        "records": [],
        "windows": {},
        "breaker": {},
        "pool": {},
        "budget": {},
    })
    labels = re.findall(r'gemini2api_served_by_total\{served_by="([^"]+)"\}', text)
    assert len(labels) <= MAX_LABEL_VALUES + 1  # +1 for the folded "other" bucket
    assert "other" in labels


def test_label_values_are_sanitised():
    from app.core.prometheus_metrics import sanitize_label_value

    assert sanitize_label_value('a"b\\c') == 'a_b_c'
    assert sanitize_label_value("line\nbreak") == "line_break"
    assert sanitize_label_value("") == "unknown"
    assert sanitize_label_value("gemini-flash-thinking") == "gemini-flash-thinking"
    assert sanitize_label_value("x" * 200).endswith("x") and len(sanitize_label_value("x" * 200)) == 48


def test_counters_are_monotonic_when_the_buffer_evicts():
    from app.core.debug_metrics import DebugMetricsStore

    store = DebugMetricsStore(capacity=2)
    for index in range(5):
        store.record({"ts": "2026-10-16T00:00:00+00:00", "status": 200, "served_by": "argon"})
    counters = store.counters()
    assert counters["requests"] == 5
    assert counters["status_class:2xx"] == 5
    # The rolling buffer only keeps the last two records, the counter keeps all five.
    assert len(store.records()) == 2


def test_window_summary_reports_volume_and_rates():
    from datetime import datetime, timezone

    from app.core.debug_metrics import DebugMetricsStore

    store = DebugMetricsStore()
    now = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    recent = datetime.fromtimestamp(now - 60, tz=timezone.utc).isoformat()
    old = datetime.fromtimestamp(now - 7200, tz=timezone.utc).isoformat()
    store.record({"ts": recent, "status": 200, "ttft_ms": 100, "total_latency_ms": 900})
    store.record({"ts": recent, "status": 500, "error_class": "upstream_error",
                  "fallback_used": True, "fallback_reason": "stall_breaker"})
    store.record({"ts": old, "status": 200})
    windows = store.window_summary(now=now)
    assert windows["1h"]["requests"] == 2
    assert windows["1h"]["errors"] == 1
    assert windows["1h"]["error_rate"] == 0.5
    assert windows["1h"]["fallbacks"] == 1
    assert windows["1h"]["fallback_rate"] == 0.5
    assert windows["24h"]["requests"] == 3
    assert windows["7d"]["requests"] == 3


def test_admin_metrics_endpoint_is_admin_protected_and_scrubbed(client):
    from app.core.debug_metrics import metrics

    metrics.reset()
    metrics.record({"account_hash": "hash-1", "served_by": "argon", "status": 200, "ttft_ms": 4})
    unauthorized = client.get("/admin/metrics")
    assert unauthorized.status_code == 401

    response = client.get("/admin/metrics", headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in response.headers["content-type"]
    body = response.text
    assert "gemini2api_requests_total" in body
    assert "hash-1" not in body
    metrics.reset()


@pytest.mark.parametrize("value", ["", "unknown", "0"])
def test_render_metrics_never_raises_on_empty_snapshot(value):
    from app.core.prometheus_metrics import render_metrics

    text = render_metrics({"uptime_seconds": value, "counters": {}, "records": [],
                           "windows": {}, "breaker": {}, "pool": {}, "budget": {}})
    assert "gemini2api_uptime_seconds" in text
