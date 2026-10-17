"""Unit tests for request tagging via X-G2A-Run and X-G2A-Role headers."""

import pytest
from app.core.log_store import LogStore
from app.core.debug_metrics import DebugMetricsStore

_AUTH = {"Authorization": "Bearer sk-test-key"}


@pytest.fixture
def test_setup(app_main):
    from fastapi.testclient import TestClient

    store = LogStore(capacity=50)
    app_main.app.state.log_store = store

    debug_store = DebugMetricsStore(capacity=50)
    app_main.debug_metrics = debug_store

    client = TestClient(app_main.app)
    return client, store, debug_store


def test_request_without_tags_has_empty_tags(test_setup):
    client, store, debug_store = test_setup
    store.clear()

    resp = client.get("/admin/status", headers=_AUTH)
    assert resp.status_code == 200

    res = store.query(limit=1)
    assert res["total"] >= 1
    assert res["records"][0]["tags"] == []

    d_records = debug_store.records(limit=1)
    assert len(d_records) == 1
    assert d_records[0].get("tags") == []


def test_request_with_valid_run_and_role_tags(test_setup):
    client, store, debug_store = test_setup
    store.clear()

    headers = {
        **_AUTH,
        "X-G2A-Run": "run_2026-10-17.test:01",
        "X-G2A-Role": "orchestrator",
    }
    resp = client.get("/admin/status", headers=headers)
    assert resp.status_code == 200

    res = store.query(limit=1)
    assert res["total"] >= 1
    assert res["records"][0]["tags"] == ["run_2026-10-17.test:01", "orchestrator"]

    d_records = debug_store.records(limit=1)
    assert len(d_records) == 1
    assert d_records[0].get("tags") == ["run_2026-10-17.test:01", "orchestrator"]


def test_request_with_single_role_tag_case_insensitive(test_setup):
    client, store, debug_store = test_setup
    store.clear()

    headers = {
        **_AUTH,
        "x-g2a-role": "monitor",
    }
    resp = client.get("/admin/status", headers=headers)
    assert resp.status_code == 200

    res = store.query(limit=1)
    assert res["total"] >= 1
    assert res["records"][0]["tags"] == ["monitor"]

    d_records = debug_store.records(limit=1)
    assert len(d_records) == 1
    assert d_records[0].get("tags") == ["monitor"]


def test_request_with_invalid_characters_or_too_long_ignored(test_setup):
    client, store, debug_store = test_setup
    store.clear()

    headers = {
        **_AUTH,
        "X-G2A-Run": "invalid tag with spaces!",
        "X-G2A-Role": "a" * 65,  # > 64 chars
    }
    resp = client.get("/admin/status", headers=headers)
    assert resp.status_code == 200

    res = store.query(limit=1)
    assert res["total"] >= 1
    assert res["records"][0]["tags"] == []

    d_records = debug_store.records(limit=1)
    assert len(d_records) == 1
    assert d_records[0].get("tags") == []
