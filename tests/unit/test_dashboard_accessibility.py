"""Accessible dashboard UX: refresh control, focus management, reduced motion.

The console is used on shared screens and with assistive technology, so the
dashboard must offer a pause control for the periodic refresh, move the keyboard
focus into the view that was opened, label its charts for screen readers and
respect the operating system's reduced-motion setting.
"""
from __future__ import annotations

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
_BASE_CSS = _ROOT / "static" / "app" / "base.css"
_DASH_HTML = _ROOT / "static" / "components" / "section-dashboard.html"
_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node is not available")
_LANGS = ("zh-CN", "en-US", "ja-JP", "ko-KR", "zh-TW")


# --------------------------------------------------------------------------------------
# Markup and CSS
# --------------------------------------------------------------------------------------

def test_refresh_toggle_button_is_present_and_announced():
    html = _DASH_HTML.read_text(encoding="utf-8")
    assert 'id="dashboardRefreshToggleBtn"' in html
    assert 'aria-pressed="false"' in html
    assert 'type="button"' in html


def test_reduced_motion_is_supported():
    css = _BASE_CSS.read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    block = css.split("@media (prefers-reduced-motion: reduce)")[1]
    assert "animation-duration" in block and "transition-duration" in block


def test_explorer_detail_is_focusable():
    html = _DASH_HTML.read_text(encoding="utf-8")
    detail = re.search(r'<div class="explorer-detail"[^>]*>', html)
    assert detail, "the explorer detail panel is missing"
    assert 'tabindex="-1"' in detail.group(0)


def test_refresh_toggle_keys_exist_in_every_language():
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
        key = re.match(r"^ +'(dashboard\.(?:pauseRefresh|resumeRefresh))':", line)
        if key:
            blocks[current].add(key.group(1))
    assert set(_LANGS) <= set(blocks), sorted(blocks)
    needed = {"dashboard.pauseRefresh", "dashboard.resumeRefresh"}
    missing = {lang: sorted(needed - blocks[lang]) for lang in _LANGS if needed - blocks[lang]}
    assert not missing, missing


# --------------------------------------------------------------------------------------
# Frontend behaviour (node)
# --------------------------------------------------------------------------------------

_PRELUDE = textwrap.dedent("""
    const _ARGV = JSON.parse(process.argv[2]);
    function t(k) { return 'T:' + k; }
    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function escapeAttr(text) { return escapeHtml(text).replace(/"/g, '&quot;'); }
    function formatNumber(num) { return String(num == null ? 0 : num); }
    function setText(id, value) { _texts[id] = String(value); }
    const _texts = {};
    function makeEl() {
        return {
            id: '', className: '', textContent: '', innerHTML: '', value: '',
            _attrs: {}, listeners: {}, focused: false,
            setAttribute(k, v) { this._attrs[k] = v; },
            getAttribute(k) { return this._attrs[k]; },
            hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k); },
            addEventListener(ev, fn) { this.listeners[ev] = fn; },
            querySelector() { return null; },
            querySelectorAll() { return []; },
            focus() { this.focused = true; },
        };
    }
    const _els = {};
    const document = { getElementById(id) { return _els[id] || null; } };
    const _storage = {};
    const safeStorage = {
        getItem(key, def) { return Object.prototype.hasOwnProperty.call(_storage, key) ? _storage[key] : def; },
        setItem(key, value) { _storage[key] = String(value); },
        removeItem(key) { delete _storage[key]; },
    };
    const APP_CONSTANTS = {
        STORAGE_KEYS: { DASHBOARD_REFRESH_PAUSED: 'gemini2api_dashboard_refresh_paused',
                        EXPLORER_FILTER: 'gemini2api_explorer_filter' },
    };
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


def _run(functions, body: str, tmp_path: Path, prelude: str = ""):
    src = _APP_JS.read_text(encoding="utf-8")
    script = "\n".join([_PRELUDE, prelude, *[_extract_function(src, name) for name in functions],
                        textwrap.dedent(body)])
    path = tmp_path / "a11y.mjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([_NODE, str(path), json.dumps({})], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@_needs_node
def test_auto_refresh_is_skipped_while_paused(tmp_path):
    out = _run(
        ("shouldAutoRefresh",),
        """
        const base = { activeSection: 'dashboard', hidden: false, inFlight: false,
                       lastLoadAt: 1000, now: 31000, intervalMs: 30000 };
        process.stdout.write(JSON.stringify({
            running: shouldAutoRefresh(base),
            paused: shouldAutoRefresh({ ...base, paused: true }),
            notDashboard: shouldAutoRefresh({ ...base, activeSection: 'logs' }),
        }));
        """,
        tmp_path,
    )
    assert out["running"] is True
    assert out["paused"] is False
    assert out["notDashboard"] is False


@_needs_node
def test_pause_toggle_persists_the_choice(tmp_path):
    out = _run(
        ("isDashboardRefreshPaused", "setDashboardRefreshPaused", "updateRefreshToggleButton",
         "toggleDashboardRefresh"),
        """
        _els['dashboardRefreshToggleBtn'] = makeEl();
        const before = isDashboardRefreshPaused();
        toggleDashboardRefresh();
        const afterFirst = isDashboardRefreshPaused();
        const pressed = _els['dashboardRefreshToggleBtn'].getAttribute('aria-pressed');
        toggleDashboardRefresh();
        process.stdout.write(JSON.stringify({
            before, afterFirst, pressed,
            afterSecond: isDashboardRefreshPaused(),
            pressedSecond: _els['dashboardRefreshToggleBtn'].getAttribute('aria-pressed'),
        }));
        """,
        tmp_path,
    )
    assert out["before"] is False
    assert out["afterFirst"] is True
    assert out["pressed"] == "true"
    assert out["afterSecond"] is False
    assert out["pressedSecond"] == "false"


@_needs_node
def test_focus_moves_to_the_opened_section_heading(tmp_path):
    out = _run(
        ("focusSectionHeading",),
        """
        const heading = makeEl();
        const section = makeEl();
        section.querySelector = () => heading;
        _els['dashboard'] = section;
        focusSectionHeading('dashboard');
        process.stdout.write(JSON.stringify({
            tabindex: heading.getAttribute('tabindex'),
            focused: heading.focused,
        }));
        """,
        tmp_path,
    )
    assert out["tabindex"] == "-1"
    assert out["focused"] is True


@_needs_node
def test_histogram_bars_carry_an_accessible_label(tmp_path):
    out = _run(
        ("renderDebugMetrics",),
        """
        _els['debug-ttft-histogram'] = makeEl();
        _els['debug-served-by'] = makeEl();
        _els['debug-error-classes'] = makeEl();
        renderDebugMetrics({
            record_count: 3,
            fallback_count: 1,
            error_classes: { ok: 2, upstream_error: 1 },
            regenerations: 0,
            ttft_histogram_ms: { '0-250ms': 2, '250ms-1s': 1 },
            served_by: { argon: 3 },
        });
        process.stdout.write(JSON.stringify({
            histogram: _els['debug-ttft-histogram'].innerHTML,
        }));
        """,
        tmp_path,
    )
    assert 'aria-label="0-250ms: 2"' in out["histogram"]
    assert 'aria-hidden="true"' in out["histogram"]


@_needs_node
def test_explorer_filter_is_saved_and_restored(tmp_path):
    out = _run(
        ("explorerMatchesFilter", "explorerRows", "formatExplorerTime", "renderRequestExplorer"),
        """
        const filter = makeEl();
        filter.value = 'argon';
        _els['explorerFilter'] = filter;
        _els['explorer-rows'] = makeEl();
        _els['explorerCount'] = makeEl();
        renderRequestExplorer([]);
        process.stdout.write(JSON.stringify({ value: filter.value }));
        """,
        tmp_path,
        prelude="let _explorerRecords = [];\nlet _explorerSelectedId = '';\n",
    )
    assert out["value"] == "argon"
