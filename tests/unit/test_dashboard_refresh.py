"""Dashboard v2: parallel loading with per-card errors, and a visibility-aware auto-refresh.

The real ``loadDashboard`` / ``applySystemInfo`` / ``setDashboardCardError`` /
``settleDashboardResults`` / ``shouldAutoRefresh`` are cut out of app.js and run in node with
small stubs (same technique as test_account_card_rendering.py).

app.js contains NUL bytes (markdown image placeholders): read it with read_text, never shell-grep it.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_APP_JS = _ROOT / "static" / "app" / "app.js"
_I18N_JS = _ROOT / "static" / "app" / "i18n.js"
_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node is not available")

_FUNCS = (
    "restartButtonVisible",
    "settleDashboardResults",
    "shouldAutoRefresh",
    "setDashboardCardError",
    "applySystemInfo",
    "loadDashboard",
)


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


_PRELUDE = textwrap.dedent("""
    const _ARGV = JSON.parse(process.argv[2]);
    function t(k) { return k; }
    function makeEl(tag) {
        return {
            tag, id: '', className: '', textContent: '', children: [], _attrs: {}, listeners: {},
            setAttribute(k, v) { this._attrs[k] = v; },
            addEventListener(ev, fn) { this.listeners[ev] = fn; },
            append(...c) { this.children.push(...c); },
            remove() { if (this.id) delete _els[this.id]; },
            prepend(c) { if (c.id) _els[c.id] = c; },
        };
    }
    const _els = { dashboard: makeEl('section') };
    const document = { getElementById(id) { return _els[id] || null; }, createElement: makeEl };

    const _texts = {};
    function setText(id, v) { _texts[id] = String(v); }
    function formatNumber(n) { return String(n); }
    const _calls = [];
    function renderAccountStatusGrid(a) { _calls.push('grid:' + a.length); }
    function renderModelsList(s) { _calls.push('models:' + s.size); }
    function updatePlaygroundModels(s) { _calls.push('pg:' + s.size); }
    function startUptimeTimer(s) { _calls.push('uptime:' + s); }
    async function loadQrCards() { _calls.push('qr'); }
    function handleCheckUpdate() {}
    function checkForUpdate() {}
    let _dashboardLoading = false;
    let _dashboardLastLoadAt = 0;
    let _dashboardQrLoaded = false;
    function loadSectionData() { _calls.push('reload'); }

    const telemetryService = {
        async getAdminStatus() {
            if (_ARGV.statusFail) throw new Error(_ARGV.statusFail);
            return { accounts: [{ status: 'active', request_count: 3, models: ['m1', 'm2'] },
                                { status: 'error', request_count: 2, models: ['m2'] }],
                     strategy: 'round-robin', max_concurrent_per_account: 4 };
        },
        async getSystemInfo() {
            if (_ARGV.sysFail) throw new Error(_ARGV.sysFail);
            return { version: '9.9', python_version: '3.14', server_time: 'now', os: 'X', memory_usage: 1,
                     memory_total: 2, cpu_percent: 3, run_mode: 'Standalone', pid: 7, uptime_seconds: 42,
                     restart_supported: true };
        },
    };
""")


def _run(body: str, argv: dict, tmp_path: Path):
    src = _APP_JS.read_text(encoding="utf-8")
    script = "\n".join([_PRELUDE, *(_extract_function(src, n) for n in _FUNCS), textwrap.dedent(body)])
    path = tmp_path / "dash.mjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([_NODE, str(path), json.dumps(argv)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


_RUN_BODY = """
    (async () => {
        let threw = '';
        try { await loadDashboard(_ARGV.opts || undefined); } catch (e) { threw = e.message; }
        process.stdout.write(JSON.stringify({
            threw, texts: _texts, calls: _calls,
            errors: Object.keys(_els).filter(k => k.startsWith('dashboard-card-error-')),
            errorText: Object.fromEntries(Object.entries(_els)
                .filter(([k]) => k.startsWith('dashboard-card-error-'))
                .map(([k, v]) => [k, v.children[0].textContent])),
        }));
    })();
"""


# ---- pure helpers --------------------------------------------------------------------------

@_needs_node
def test_settle_results_and_refresh_decision(tmp_path):
    out = _run(
        """
        const ok = { status: 'fulfilled', value: { a: 1 } };
        const bad = { status: 'rejected', reason: new Error('boom') };
        const weird = { status: 'rejected', reason: 'x' };
        const base = { activeSection: 'dashboard', hidden: false, inFlight: false,
                       lastLoadAt: 1000, now: 31000, intervalMs: 30000 };
        process.stdout.write(JSON.stringify({
            both: settleDashboardResults(ok, bad),
            weird: settleDashboardResults(weird, ok),
            due: shouldAutoRefresh(base),
            early: shouldAutoRefresh({ ...base, now: 20000 }),
            hidden: shouldAutoRefresh({ ...base, hidden: true }),
            other: shouldAutoRefresh({ ...base, activeSection: 'logs' }),
            busy: shouldAutoRefresh({ ...base, inFlight: true }),
            nothing: shouldAutoRefresh(null),
        }));
        """,
        {}, tmp_path,
    )
    assert out["both"] == {"status": {"a": 1}, "statusError": "", "sys": None, "sysError": "boom"}
    assert out["weird"]["status"] is None and out["weird"]["statusError"] == "Unknown error"
    assert out["due"] is True
    assert out["early"] is False and out["hidden"] is False
    assert out["other"] is False and out["busy"] is False and out["nothing"] is False


# ---- real loadDashboard --------------------------------------------------------------------

@_needs_node
def test_all_good_renders_everything_and_no_error_notice(tmp_path):
    out = _run(_RUN_BODY, {}, tmp_path)
    assert out["threw"] == "" and out["errors"] == []
    assert out["texts"]["stat-total"] == "2" and out["texts"]["stat-active"] == "1"
    assert out["texts"]["stat-requests"] == "5" and out["texts"]["stat-models"] == "2"
    assert out["texts"]["sys-version"] == "v9.9"
    assert {"grid:2", "uptime:42", "qr"} <= set(out["calls"])


@_needs_node
def test_system_info_failure_keeps_the_status_cards_working(tmp_path):
    out = _run(_RUN_BODY, {"sysFail": "sysinfo down"}, tmp_path)
    assert out["threw"] == ""
    assert out["texts"]["stat-total"] == "2" and "grid:2" in out["calls"]
    assert "sys-version" not in out["texts"]
    assert out["errors"] == ["dashboard-card-error-system"]
    assert "sysinfo down" in out["errorText"]["dashboard-card-error-system"]


@_needs_node
def test_status_failure_keeps_system_info_working(tmp_path):
    out = _run(_RUN_BODY, {"statusFail": "status down"}, tmp_path)
    assert out["threw"] == ""
    assert out["texts"]["sys-version"] == "v9.9"
    assert "stat-total" not in out["texts"] and not any(c.startswith("grid:") for c in out["calls"])
    assert out["errors"] == ["dashboard-card-error-status"]
    assert "status down" in out["errorText"]["dashboard-card-error-status"]


@_needs_node
def test_both_failing_still_reaches_the_section_error_boundary(tmp_path):
    out = _run(_RUN_BODY, {"statusFail": "status down", "sysFail": "sys down"}, tmp_path)
    assert out["threw"] == "status down"


@_needs_node
def test_background_refresh_never_throws_and_skips_the_qr_reload(tmp_path):
    out = _run(_RUN_BODY, {"statusFail": "status down", "sysFail": "sys down", "opts": {"background": True}}, tmp_path)
    assert out["threw"] == ""
    assert sorted(out["errors"]) == ["dashboard-card-error-status", "dashboard-card-error-system"]
    assert "qr" not in out["calls"]


@_needs_node
def test_a_later_successful_load_clears_the_error_notice(tmp_path):
    out = _run(
        """
        (async () => {
            _ARGV.sysFail = 'sysinfo down';
            await loadDashboard();
            const during = Object.keys(_els).filter(k => k.startsWith('dashboard-card-error-'));
            _ARGV.sysFail = '';
            await loadDashboard();
            const after = Object.keys(_els).filter(k => k.startsWith('dashboard-card-error-'));
            process.stdout.write(JSON.stringify({ during, after }));
        })();
        """,
        {}, tmp_path,
    )
    assert out == {"during": ["dashboard-card-error-system"], "after": []}


@_needs_node
def test_error_notice_uses_textcontent_and_has_a_retry_button(tmp_path):
    out = _run(
        """
        setDashboardCardError('status', '<img src=x onerror=alert(1)>');
        const el = _els['dashboard-card-error-status'];
        el.children[1].listeners.click();
        process.stdout.write(JSON.stringify({
            text: el.children[0].textContent, role: el._attrs.role,
            retryLabel: el.children[1].textContent, calls: _calls,
        }));
        """,
        {}, tmp_path,
    )
    assert out["text"] == "dashboard.error.status: <img src=x onerror=alert(1)>"
    assert out["role"] == "alert" and out["retryLabel"] == "dashboard.retry"
    assert out["calls"] == ["reload"]


# ---- wiring and i18n -----------------------------------------------------------------------

def test_auto_refresh_is_started_from_init():
    src = _APP_JS.read_text(encoding="utf-8")
    init = _extract_function(src, "initApp")
    assert "startDashboardAutoRefresh()" in init
    starter = _extract_function(src, "startDashboardAutoRefresh")
    assert "document.hidden" in starter and "shouldAutoRefresh(" in starter
    assert "background: true" in starter


@pytest.mark.parametrize("key", ["dashboard.error.status", "dashboard.error.system", "dashboard.retry"])
def test_new_i18n_keys_exist_in_all_five_languages(key):
    text = _I18N_JS.read_text(encoding="utf-8")
    assert len(re.findall(rf"^\s*'{re.escape(key)}':", text, re.M)) == 5
