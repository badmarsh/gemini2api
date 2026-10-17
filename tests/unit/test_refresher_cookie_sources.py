"""The refresher must try the pool's current cookies, not only its own stale seed.

Regression: refresher_accounts.json is a one-off seed. The gemini2api pool rotates PSIDTS (and
sometimes the PSID) on its own, so the seed went stale, Google served the signed-out page for it,
and the refresher reported "NOT SIGNED IN" for 4 accounts that were working fine.
"""

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_REFRESHER = Path(__file__).resolve().parents[2] / "refresher" / "refresher.py"


@pytest.fixture
def refresher(tmp_path, monkeypatch):
    """Import refresher.py against a temp DATA_DIR, with playwright stubbed out."""
    fake_pkg = types.ModuleType("playwright")
    fake_api = types.ModuleType("playwright.sync_api")
    fake_api.sync_playwright = lambda: None
    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_api)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("refresher_under_test", _REFRESHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_pool(tmp_path, entries, *, wrap=False):
    payload = {"accounts": entries} if wrap else entries
    (tmp_path / "accounts.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_jar(tmp_path, psid, psidts, jar_psid=None):
    name = hashlib.sha256(psid.encode()).hexdigest()[:16] + ".json"
    (tmp_path / "cookies").mkdir(exist_ok=True)
    (tmp_path / "cookies" / name).write_text(
        json.dumps([
            {"name": "__Secure-1PSID", "value": jar_psid or psid},
            {"name": "__Secure-1PSIDTS", "value": psidts},
        ]),
        encoding="utf-8",
    )


def test_pool_cookies_come_from_accounts_json_in_either_layout(refresher, tmp_path):
    entry = {"id": "account-0", "psid": "P1", "psidts": "T1"}
    _write_pool(tmp_path, [entry])
    assert refresher.load_pool_cookies("account-0") == ("P1", "T1")
    _write_pool(tmp_path, [entry], wrap=True)
    assert refresher.load_pool_cookies("account-0") == ("P1", "T1")


def test_pool_cookies_prefer_the_newer_psidts_from_the_pool_jar(refresher, tmp_path):
    _write_pool(tmp_path, [{"id": "account-0", "psid": "P1", "psidts": "OLD"}])
    _write_jar(tmp_path, "P1", "NEW")
    assert refresher.load_pool_cookies("account-0") == ("P1", "NEW")


def test_pool_jar_of_another_psid_is_ignored(refresher, tmp_path):
    _write_pool(tmp_path, [{"id": "account-0", "psid": "P1", "psidts": "T1"}])
    _write_jar(tmp_path, "P1", "OTHER", jar_psid="P2")
    assert refresher.load_pool_cookies("account-0") == ("P1", "T1")


def test_pool_cookies_missing_or_unknown_account_is_none(refresher, tmp_path):
    assert refresher.load_pool_cookies("account-0") is None  # no accounts.json at all
    _write_pool(tmp_path, [{"id": "account-9", "psid": "P", "psidts": "T"}])
    assert refresher.load_pool_cookies("account-0") is None


def _account(**kw):
    return {"id": "account-0", "label": "acc", "psid": "SEED_P", "psidts": "SEED_T", **kw}


def test_stale_seed_loses_to_the_pool_pair(refresher, tmp_path, monkeypatch):
    _write_pool(tmp_path, [{"id": "account-0", "psid": "POOL_P", "psidts": "POOL_T"}])
    tried = []

    def fake_attempt(browser, state_file, seed=None):
        tried.append(seed)
        if seed == ("POOL_P", "POOL_T"):
            return {"outcome": "ok", "psid": "POOL_P", "psidts": "ROTATED_T"}
        return {"outcome": "signed_out"}

    monkeypatch.setattr(refresher, "_attempt", fake_attempt)
    result = refresher.refresh_account(object(), _account())
    assert result["status"] == "active"
    assert result["source"] == "pool"
    assert result["psidts"] == "ROTATED_T"
    assert tried == [("POOL_P", "POOL_T")], "the stale seed must not be tried once the pool pair worked"


def test_browser_state_is_tried_first_and_a_working_one_is_kept(refresher, tmp_path, monkeypatch):
    state_dir = tmp_path / "browser_states" / "account-0"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({"cookies": [
        {"name": "__Secure-1PSID", "value": "STATE_P"},
        {"name": "__Secure-1PSIDTS", "value": "STATE_T"},
    ]}), encoding="utf-8")
    _write_pool(tmp_path, [{"id": "account-0", "psid": "POOL_P", "psidts": "POOL_T"}])
    tried = []

    def fake_attempt(browser, state_file, seed=None):
        tried.append(seed)
        return {"outcome": "ok", "psid": "STATE_P", "psidts": "STATE_T2"}

    monkeypatch.setattr(refresher, "_attempt", fake_attempt)
    result = refresher.refresh_account(object(), _account())
    assert result["status"] == "active" and result["source"] == "browser state"
    assert tried == [None]


def test_signed_out_is_reported_only_when_every_source_is_rejected(refresher, tmp_path, monkeypatch):
    _write_pool(tmp_path, [{"id": "account-0", "psid": "POOL_P", "psidts": "POOL_T"}])
    tried = []

    def fake_attempt(browser, state_file, seed=None):
        tried.append(seed)
        return {"outcome": "signed_out"}

    monkeypatch.setattr(refresher, "_attempt", fake_attempt)
    result = refresher.refresh_account(object(), _account())
    assert result["status"] == "signed_out"
    assert tried == [("POOL_P", "POOL_T"), ("SEED_P", "SEED_T")]


def test_identical_pairs_are_tried_once(refresher, tmp_path, monkeypatch):
    _write_pool(tmp_path, [{"id": "account-0", "psid": "SEED_P", "psidts": "SEED_T"}])
    tried = []

    def fake_attempt(browser, state_file, seed=None):
        tried.append(seed)
        return {"outcome": "signed_out"}

    monkeypatch.setattr(refresher, "_attempt", fake_attempt)
    refresher.refresh_account(object(), _account())
    assert tried == [("SEED_P", "SEED_T")]


def test_fingerprint_never_contains_the_cookie_value(refresher):
    value = "g.a000-secret-cookie-value"
    fp = refresher._fp(value)
    assert value not in fp and len(fp) == 8
    assert refresher._fp("") == "none"
