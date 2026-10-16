"""Focused safety and resumability tests for the local tuning harness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.tune import (
    TuneConfig,
    TuneState,
    apply_proposal,
    load_state,
    redact,
    stable_digest,
    summarize,
    validate_proposal,
    worker_contract,
)


def test_redaction_removes_credentials_and_runtime_paths():
    value = {
        "authorization": "Bearer sk-live-never-print-this",
        "message": "read data/accounts.json and .env",
        "nested": {"psid": "cookie-value"},
    }
    redacted = redact(value)
    assert redacted["authorization"] == "[REDACTED]"
    assert "data/" not in redacted["message"]
    assert ".env" not in redacted["message"]
    assert redacted["nested"]["psid"] == "[REDACTED]"


def test_worker_contract_pins_model_and_exact_edit_rules():
    contract = worker_contract()
    assert contract["model"] == "gemini-3.8-flash"
    assert any("exact" in rule.lower() for rule in contract["rules"])
    assert any("truncation" in rule.lower() for rule in contract["rules"])


def test_exact_match_edit_preserves_crlf(tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_bytes(b"first\r\nsecond\r\n")
    proposal = {"edits": [{"path": "sample.txt", "old_text": "second\r\n",
                            "new_text": "changed\r\n", "reason": "focused"}]}
    assert validate_proposal(proposal, root=tmp_path)[0]["path"] == "sample.txt"
    applied = apply_proposal(proposal, root=tmp_path)
    assert applied[0]["applied"] is True
    assert target.read_bytes() == b"first\r\nchanged\r\n"


def test_exact_match_rejects_ambiguous_or_protected_edits(tmp_path: Path):
    (tmp_path / "sample.txt").write_text("same\nsame\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly once"):
        validate_proposal({"edits": [{"path": "sample.txt", "old_text": "same", "new_text": "new"}]}, root=tmp_path)
    with pytest.raises(ValueError, match="secrets"):
        validate_proposal({"edits": [{"path": "data/accounts.json", "old_text": "x", "new_text": "y"}]}, root=tmp_path)


def test_state_round_trip_is_json_and_resumable(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state = TuneState(run_id="run", tier="t0", seed=7, started_at="now", records=[{"status": 200}])
    state_path.write_text(json.dumps(state.to_dict()), encoding="utf-8")
    loaded = load_state(state_path)
    assert loaded is not None
    assert loaded.run_id == "run" and loaded.records == [{"status": 200}]
    assert TuneConfig(state_path=state_path).state_path == state_path


def test_stable_digest_ignores_volatile_runner_fields():
    first = [{"case": "ok", "status": 200, "content": "pong", "total_s": 1.0, "body_head": "id-a"}]
    second = [{"case": "ok", "status": 200, "content": "pong", "total_s": 8.0, "body_head": "id-b"}]
    assert stable_digest(first) == stable_digest(second)
    report = summarize([{**first[0], "total_ms": 10}, {**first[0], "total_ms": 20, "error_class": "upstream_error"}])
    assert report["records"] == 2 and report["error_classes"]["upstream_error"] == 1
