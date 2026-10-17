"""The refresher pushes only cookies that changed, and says why it skipped a cookie source.

Live evidence (2026-10-17): every ~7 minute cycle logged the same PSIDTS fingerprint per account
and still PUT all four accounts, each PUT reloading a healthy client (see
test_admin_cookies_push_stability.py). Separately, a 61 minute outage (0/4 accounts) could not be
diagnosed because the refresher silently de-duplicated cookie sources: the "pool" source never
appeared in the log, so nobody could tell whether it was empty, stale or identical to the browser
state.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_REFRESHER = Path(__file__).resolve().parents[2] / "refresher" / "refresher.py"

SECRET_P = "g.a000-very-secret-psid-value"
SECRET_T = "sidts-very-secret-ts-value"


@pytest.fixture
def refresher(tmp_path, monkeypatch):
    fake_pkg = types.ModuleType("playwright")
    fake_api = types.ModuleType("playwright.sync_api")
    fake_api.sync_playwright = lambda: None
    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_api)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("refresher_push_policy", _REFRESHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _account(**kw):
    return {"id": "account-0", "label": "acc", "psid": "SEED_P", "psidts": "SEED_T", **kw}


def _write_pool(tmp_path, psid, psidts):
    (tmp_path / "accounts.json").write_text(
        json.dumps([{"id": "account-0", "psid": psid, "psidts": psidts}]), encoding="utf-8"
    )


def _write_state(tmp_path, psid, psidts):
    state_dir = tmp_path / "browser_states" / "account-0"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps({"cookies": [
            {"name": "__Secure-1PSID", "value": psid},
            {"name": "__Secure-1PSIDTS", "value": psidts},
        ]}),
        encoding="utf-8",
    )


def test_a_skipped_duplicate_source_is_logged_with_its_reason(refresher, tmp_path, monkeypatch, capsys):
    _write_pool(tmp_path, SECRET_P, SECRET_T)
    _write_state(tmp_path, SECRET_P, SECRET_T)  # browser state == pool pair -> "pool" is skipped
    monkeypatch.setattr(
        refresher, "_attempt",
        lambda browser, state_file, seed=None: {"outcome": "ok", "psid": SECRET_P, "psidts": SECRET_T},
    )

    # The one-off seed carries the same pair as well, so both later sources are duplicates.
    refresher.refresh_account(object(), _account(psid=SECRET_P, psidts=SECRET_T))
    out = capsys.readouterr().out

    assert "skipped source pool: same cookie pair as browser state" in out
    assert "skipped source refresher_accounts.json: same cookie pair as browser state" in out
    assert SECRET_P not in out and SECRET_T not in out, "cookie values must never be logged"


def test_an_empty_source_is_logged_as_unusable(refresher, tmp_path, monkeypatch, capsys):
    # No accounts.json in the data dir: the pool source has nothing to offer.
    monkeypatch.setattr(
        refresher, "_attempt",
        lambda browser, state_file, seed=None: {"outcome": "ok", "psid": "SEED_P", "psidts": "NEW_T"},
    )

    refresher.refresh_account(object(), _account())
    out = capsys.readouterr().out

    assert "skipped source pool: no usable cookie pair" in out


def test_result_is_marked_unchanged_when_the_pair_equals_the_pool_pair(refresher, tmp_path, monkeypatch):
    _write_pool(tmp_path, "POOL_P", "POOL_T")
    monkeypatch.setattr(
        refresher, "_attempt",
        lambda browser, state_file, seed=None: {"outcome": "ok", "psid": "POOL_P", "psidts": "POOL_T"},
    )
    assert refresher.refresh_account(object(), _account())["unchanged"] is True


def test_result_is_not_unchanged_when_psidts_rotated(refresher, tmp_path, monkeypatch):
    _write_pool(tmp_path, "POOL_P", "POOL_T")
    monkeypatch.setattr(
        refresher, "_attempt",
        lambda browser, state_file, seed=None: {"outcome": "ok", "psid": "POOL_P", "psidts": "ROTATED"},
    )
    assert refresher.refresh_account(object(), _account())["unchanged"] is False


def _active(unchanged):
    return {"id": "account-0", "psid": "P", "psidts": "T", "status": "active", "unchanged": unchanged}


def test_unchanged_cookies_are_not_pushed_while_the_pool_session_is_healthy(refresher, monkeypatch, capsys):
    pushed = []
    monkeypatch.setattr(refresher, "pool_session_ready", lambda account_id: True)
    monkeypatch.setattr(refresher, "notify_gemini2api", lambda *a: pushed.append(a) or True)

    refresher.notify_active([_active(True)])

    assert pushed == []
    assert "account-0 skipped" in capsys.readouterr().out


def test_unchanged_cookies_are_pushed_when_the_pool_session_is_not_healthy(refresher, monkeypatch):
    pushed = []
    monkeypatch.setattr(refresher, "pool_session_ready", lambda account_id: False)
    monkeypatch.setattr(refresher, "notify_gemini2api", lambda *a: pushed.append(a) or True)

    refresher.notify_active([_active(True)])

    assert pushed == [("account-0", "P", "T")]


def test_rotated_cookies_are_always_pushed(refresher, monkeypatch):
    pushed = []
    monkeypatch.setattr(refresher, "pool_session_ready", lambda account_id: True)
    monkeypatch.setattr(refresher, "notify_gemini2api", lambda *a: pushed.append(a) or True)

    refresher.notify_active([_active(False)])

    assert pushed == [("account-0", "P", "T")]


def test_pool_session_ready_reads_the_admin_accounts_listing(refresher, monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return {"accounts": [
                {"id": "account-0", "status": "active", "is_healthy": True},
                {"id": "account-1", "status": "active", "is_healthy": False},
            ]}

    monkeypatch.setattr(refresher.http_requests, "get", lambda *a, **k: _Resp())
    assert refresher.pool_session_ready("account-0") is True
    assert refresher.pool_session_ready("account-1") is False
    assert refresher.pool_session_ready("account-9") is False


def test_pool_session_ready_is_false_when_the_pool_cannot_be_asked(refresher, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(refresher.http_requests, "get", boom)
    assert refresher.pool_session_ready("account-0") is False
