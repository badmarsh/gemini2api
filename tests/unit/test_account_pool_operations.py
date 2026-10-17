"""Account-pool operations: enable/disable, operational card data, safe actions.

The dashboard's account cards must show what an operator needs to act on
(slots in use, cooldown, session health) and offer only actions the backend
already supports safely -- check, enable/disable and the Argon breaker reset.
Cookies and raw account identifiers must never reach the card text.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from app.core.account_pool import Account, AccountPool, AccountStatus

_ROOT = Path(__file__).resolve().parents[2]
_APP_JS = _ROOT / "static" / "app" / "app.js"
_I18N_JS = _ROOT / "static" / "app" / "i18n.js"
_DASH_HTML = _ROOT / "static" / "components" / "section-dashboard.html"
_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node is not available")
_LANGS = ("zh-CN", "en-US", "ja-JP", "ko-KR", "zh-TW")
_AUTH = {"Authorization": "Bearer sk-test-key"}


class _HealthyClient:
    def __init__(self):
        self._healthy = True

    @property
    def is_healthy(self) -> bool:
        return self._healthy


def _pool() -> AccountPool:
    pool = AccountPool.__new__(AccountPool)
    pool._accounts = [
        Account(id="acc-1", psid="p1", psidts="s1", label="One", client=_HealthyClient()),
        Account(id="acc-2", psid="p2", psidts="s2", label="Two", client=_HealthyClient()),
    ]
    pool._max_concurrent = 8
    pool._robin_index = 0
    from app.core.account_pool import RotationStrategy
    pool._strategy = RotationStrategy.ROUND_ROBIN
    return pool


# --------------------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------------------

def test_disabled_account_is_skipped_by_the_rotation():
    async def _run():
        pool = _pool()
        assert pool._find_available() is not None
        result = pool.set_account_enabled("acc-1", False)
        assert result["status"] == "disabled"
        assert result["enabled"] is False
        chosen = pool._find_available()
        assert chosen is not None and chosen.id == "acc-2"

    asyncio.run(_run())


def test_re_enabling_restores_the_account():
    async def _run():
        pool = _pool()
        pool.set_account_enabled("acc-1", False)
        result = pool.set_account_enabled("acc-1", True)
        assert result["status"] == "active"
        assert result["enabled"] is True
        assert pool._find_available() is not None

    asyncio.run(_run())


def test_re_enabling_does_not_revive_an_expired_account():
    pool = _pool()
    pool._accounts[0].status = AccountStatus.EXPIRED
    result = pool.set_account_enabled("acc-1", True)
    # An account that failed its checks stays expired until a check succeeds.
    assert result["status"] == "expired"


def test_unknown_account_raises():
    pool = _pool()
    with pytest.raises(ValueError):
        pool.set_account_enabled("nope", False)


def test_status_endpoint_requires_the_admin_key(client):
    assert client.post("/admin/accounts/acc-1/status", json={"enabled": False}).status_code == 401


def test_status_endpoint_reports_unknown_accounts(client, monkeypatch):
    from app.core.account_pool import account_pool as pool_singleton

    monkeypatch.setattr(pool_singleton, "set_account_enabled",
                        lambda account_id, enabled: (_ for _ in ()).throw(ValueError("Account nope not found")))
    response = client.post("/admin/accounts/nope/status", json={"enabled": False}, headers=_AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"


def test_status_endpoint_disables_and_enables(client, monkeypatch):
    from app.core.account_pool import account_pool as pool_singleton

    calls = []

    def _fake(account_id, enabled):
        calls.append((account_id, enabled))
        return {"account_id": account_id, "status": "disabled" if not enabled else "active",
                "enabled": enabled}

    monkeypatch.setattr(pool_singleton, "set_account_enabled", _fake)
    response = client.post("/admin/accounts/acc-1/status", json={"enabled": False}, headers=_AUTH)
    assert response.status_code == 200
    assert response.json()["account"]["status"] == "disabled"
    response = client.post("/admin/accounts/acc-1/status", json={"enabled": True}, headers=_AUTH)
    assert response.status_code == 200
    assert calls == [("acc-1", False), ("acc-1", True)]


def test_account_status_reports_cooldown_remaining(client, monkeypatch):
    from app.core.account_pool import account_pool as pool_singleton

    monkeypatch.setattr(pool_singleton, "get_status", lambda: {
        "total": 1, "active": 1, "strategy": "round-robin", "max_concurrent_per_account": 8,
        "accounts": [{
            "id": "acc-1", "label": "One", "psid": "secret", "status": "active",
            "request_count": 1, "error_count": 0, "active_requests": 2,
            "last_used": None, "cooling_down": True, "cooldown_remaining_s": 12.5,
            "is_healthy": True, "consecutive_failures": 0, "consecutive_empty": 0,
            "empty_count": 0, "last_success_at": None, "last_error": None,
            "last_error_at": None, "models": [], "models_count": 0,
        }],
    })
    response = client.get("/admin/status", headers=_AUTH)
    assert response.status_code == 200
    account = response.json()["accounts"][0]
    assert account["cooldown_remaining_s"] == 12.5
    # The pool status is masked at the boundary.
    assert "secret" not in response.text


# --------------------------------------------------------------------------------------
# i18n and markup
# --------------------------------------------------------------------------------------

def _locale_keys(prefix: str) -> dict[str, set[str]]:
    blocks: dict[str, set[str]] = {}
    current = None
    for line in _I18N_JS.read_text(encoding="utf-8").splitlines():
        lang = re.match(r"^ {4}'([A-Za-z-]+)': \{$", line)
        if lang:
            current = lang.group(1)
            blocks[current] = set()
            continue
        if current is None:
            continue
        key = re.match(rf"^ +'({re.escape(prefix)}[^']*)':", line)
        if key:
            blocks[current].add(key.group(1))
    return blocks


def test_account_operation_keys_exist_in_every_language():
    blocks = _locale_keys("accounts.")
    assert set(_LANGS) <= set(blocks), sorted(blocks)
    needed = {
        "accounts.slotsInUse", "accounts.cooldownRemaining", "accounts.sessionHealth",
        "accounts.actionCheck", "accounts.actionEnable", "accounts.actionDisable",
        "accounts.actionToggleTitle", "accounts.actionToggleMessage", "accounts.actionToggleConfirm",
    }
    missing = {lang: sorted(needed - blocks[lang]) for lang in _LANGS if needed - blocks[lang]}
    assert not missing, missing


def test_all_language_blocks_share_the_same_account_keys():
    blocks = _locale_keys("accounts.")
    baseline = blocks["zh-CN"]
    assert baseline, "no accounts.* keys parsed - i18n.js format changed?"
    drift = {
        lang: {"missing": sorted(baseline - blocks[lang]), "extra": sorted(blocks[lang] - baseline)}
        for lang in _LANGS
        if blocks[lang] != baseline
    }
    assert not drift, drift


def test_dashboard_markup_has_the_breaker_reset_button():
    html = _DASH_HTML.read_text(encoding="utf-8")
    assert 'id="argonBreakerResetBtn"' in html
    assert "hidden" in html


# --------------------------------------------------------------------------------------
# Frontend rendering (node)
# --------------------------------------------------------------------------------------

_PRELUDE = textwrap.dedent("""
    const _ARGV = JSON.parse(process.argv[2]);
    function t(k) { return 'T:' + k; }
    function formatNumber(num) { return String(num == null ? 0 : num); }
    function getStatusBadge(status) { return '<BADGE:' + status + '>'; }
    function formatDate(value) { return String(value == null ? '-' : value); }
    function makeEl() {
        return { id: '', className: '', textContent: '', innerHTML: '',
                 _attrs: {}, listeners: {},
                 setAttribute(k, v) { this._attrs[k] = v; },
                 addEventListener(ev, fn) { this.listeners[ev] = fn; },
                 querySelectorAll() { return []; } };
    }
    const _els = {};
    const document = { getElementById(id) { return _els[id] || null; } };
""")


def _extract_function(source: str, name: str) -> str:
    head = source.index(f"function {name}(")
    if source[max(0, head - 6):head] == "async ":
        head -= 6
    depth = 0
    i = source.index("{", head)
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[head:i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces for {name}")


@_needs_node
def test_account_card_shows_operational_rows(tmp_path):
    src = _APP_JS.read_text(encoding="utf-8")
    functions = [
        _extract_function(src, name)
        for name in ("escapeHtml", "escapeAttr", "_accountOperationsRows", "_accountActionButtons")
    ]
    script = "\n".join([_PRELUDE, *functions, textwrap.dedent("""
        const account = { id: 'acc-1', label: 'One', status: 'active', active_requests: 2,
                          cooldown_remaining_s: 12.4, is_healthy: false };
        process.stdout.write(JSON.stringify({
            rows: _accountOperationsRows(account, 8),
            buttons: _accountActionButtons(account),
            noLimit: _accountOperationsRows(account, 0),
        }));
    """)])
    path = tmp_path / "ops.mjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([_NODE, str(path), json.dumps({})], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "T:accounts.slotsInUse" in out["rows"]
    assert "2 / 8" in out["rows"]
    assert "T:accounts.cooldownRemaining" in out["rows"]
    assert "12 s" in out["rows"]
    assert "T:accounts.sessionUnhealthy" in out["rows"]
    # Without a known limit the slots row is not rendered with a wrong number.
    assert "T:accounts.slotsInUse" not in out["noLimit"]
    # The action buttons exist; the id only travels in the data attribute.
    assert "data-account-action=\"check\"" in out["buttons"]
    assert "data-account-action=\"toggle\"" in out["buttons"]
    assert "data-account-enabled=\"true\"" in out["buttons"]


@_needs_node
def test_account_card_does_not_render_cookies(tmp_path):
    src = _APP_JS.read_text(encoding="utf-8")
    functions = [
        _extract_function(src, name)
        for name in ("escapeHtml", "escapeAttr", "_accountOperationsRows", "_accountActionButtons")
    ]
    script = "\n".join([_PRELUDE, *functions, textwrap.dedent("""
        const account = { id: 'acc-1', label: '<img src=x onerror=alert(1)>', status: 'disabled',
                          psid: 'super-secret-psid', active_requests: 0 };
        process.stdout.write(JSON.stringify({
            buttons: _accountActionButtons(account),
        }));
    """)])
    path = tmp_path / "ops2.mjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([_NODE, str(path), json.dumps({})], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "super-secret-psid" not in out["buttons"]
    assert "data-account-enabled=\"false\"" in out["buttons"]
    assert "T:accounts.actionEnable" in out["buttons"]
