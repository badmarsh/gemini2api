"""Redacted admin diagnostics and parser fixture endpoint coverage."""
from __future__ import annotations


def test_debug_metrics_store_has_bounded_summary_and_no_account_id():
    from app.core.debug_metrics import DebugMetricsStore

    store = DebugMetricsStore(capacity=2)
    store.record({"account_hash": "hash-only", "served_by": "argon", "status": 200, "ttft_ms": 3})
    store.record({"account_hash": "other-hash", "served_by": "flash", "status": 503,
                  "error_class": "upstream_error", "fallback_used": True, "regenerations": 1})
    store.record({"account_hash": "third-hash", "served_by": "flash", "status": 200, "ttft_ms": 400})
    summary = store.summary()
    assert summary["record_count"] == 2
    assert summary["fallback_count"] == 1
    assert summary["error_classes"]["upstream_error"] == 1
    assert "account_id" not in summary
    assert len(store.records()) == 2


def test_admin_debug_metrics_endpoint_returns_redacted_records(client):
    from app.core.debug_metrics import metrics

    metrics.reset()
    metrics.record({"account_hash": "abc123", "served_by": "argon", "status": 200, "ttft_ms": 8})
    response = client.get("/admin/debug-metrics", headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["record_count"] == 1
    assert body["records"][0]["account_hash"] == "abc123"
    assert "psid" not in response.text.lower()


def test_tool_json_debug_endpoint_uses_same_safe_parser(client):
    valid = '{"status":"tool_use","tool_calls":[{"name":"write_file","arguments":"{\\"path\\":\\"a.txt\\"}"}]}'
    response = client.post("/admin/debug/tool-json", json={"text": valid},
                           headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["tool_call_count"] == 1

    truncated = '{"status":"tool_use","tool_calls":[{"name":"write_file","arguments":"{'
    response = client.post("/admin/debug/tool-json", json={"text": truncated},
                           headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["truncated_suspected"] is True
