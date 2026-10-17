"""Operational overview (SLO): backend state machine, dashboard rendering and i18n.

The dashboard used to show "green" whenever the endpoints answered. These tests pin
the explicit ``ok`` / ``degraded`` / ``unknown`` states, the degradation reasons and
the rendering of the panel (including that a hostile value cannot inject markup).
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
_DASH_HTML = _ROOT / "static" / "components" / "section-dashboard.html"
_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node is not available")

_LANGS = ("zh-CN", "en-US", "ja-JP", "ko-KR", "zh-TW")


# --------------------------------------------------------------------------------------
# Backend: the SLO state machine
# --------------------------------------------------------------------------------------

def _slo(**kwargs):
    from app.core.slo import build_slo

    base = {"windows": {}, "breaker": {}, "pool": {}, "budget": {}, "lifetime_requests": 0}
    base.update(kwargs)
    return build_slo(**base)


def test_no_data_is_unknown_not_ok():
    slo = _slo()
    assert slo["state"] == "unknown"
    assert slo["degraded"] is False
    assert slo["reasons"] == []


def test_healthy_service_is_ok():
    slo = _slo(
        windows={"1h": {"requests": 10, "errors": 0, "error_rate": 0.0, "fallback_rate": 0.0,
                        "ttft_ms": {"p95": 40000}, "max_inter_chunk_gap_ms": 1000}},
        lifetime_requests=10,
    )
    assert slo["state"] == "ok"
    assert slo["degraded"] is False


def test_each_degradation_rule_is_reported():
    cases = {
        "breaker_open": {"breaker": {"open": True}},
        "error_rate": {"windows": {"1h": {"requests": 10, "error_rate": 0.5}}},
        "fallback_rate": {"windows": {"1h": {"requests": 10, "fallback_rate": 0.9}}},
        "ttft_p95": {"windows": {"1h": {"requests": 10, "ttft_ms": {"p95": 200000}}}},
        "inter_chunk_gap": {"windows": {"1h": {"requests": 10, "max_inter_chunk_gap_ms": 45000}}},
        "pool_saturated": {"pool": {"saturated": True}},
        "pool_slot_leak": {"pool": {"slot_leak_accounts": 1}},
        "regeneration_budget_exhausted": {"budget": {"blocked": 1}},
    }
    for reason, override in cases.items():
        kwargs = {"lifetime_requests": 10}
        kwargs.update(override)
        slo = _slo(**kwargs)
        assert slo["state"] == "degraded", reason
        assert reason in slo["reasons"], reason


def test_idle_service_with_history_is_not_degraded():
    """No traffic in the last hour is idle, not a failure."""
    slo = _slo(windows={"1h": {"requests": 0}}, lifetime_requests=100)
    assert slo["state"] == "ok"
    assert slo["reasons"] == []


def test_admin_status_exposes_the_slo_view(client):
    from app.core.debug_metrics import metrics

    metrics.reset()
    response = client.get("/admin/status", headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 200
    slo = response.json()["slo"]
    assert slo["state"] in ("ok", "degraded", "unknown")
    assert "windows" in slo and "pool" in slo and "breaker" in slo and "budget" in slo
    assert set(slo["pool"]) >= {"capacity", "active_requests", "saturation", "slot_leak_accounts"}
    metrics.reset()


def test_slo_view_never_contains_identifiers(client):
    response = client.get("/admin/status", headers={"Authorization": "Bearer sk-test-key"})
    body = response.text.lower()
    assert "psid" not in body
    assert "cookie" not in body


# --------------------------------------------------------------------------------------
# i18n: five locales stay in sync
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


def test_slo_keys_exist_in_every_language():
    blocks = _locale_keys("dashboard.slo")
    assert set(_LANGS) <= set(blocks), sorted(blocks)
    needed = {
        "dashboard.sloTitle", "dashboard.sloStateOk", "dashboard.sloStateDegraded",
        "dashboard.sloStateUnknown", "dashboard.sloReasonsLabel", "dashboard.sloVolume",
        "dashboard.sloWindow", "dashboard.sloRequests", "dashboard.sloErrorRate",
        "dashboard.sloFallbackRate", "dashboard.sloLatency", "dashboard.sloTtftP50",
        "dashboard.sloTtftP95", "dashboard.sloLatencyP50", "dashboard.sloLatencyP95",
        "dashboard.sloMaxGap", "dashboard.sloPool", "dashboard.sloBreaker",
        "dashboard.sloBreakerOpen", "dashboard.sloBreakerClosed", "dashboard.sloCapacity",
        "dashboard.sloSaturation", "dashboard.sloCooldown", "dashboard.sloSlotLeak",
        "dashboard.sloRegenerations", "dashboard.sloNoData", "dashboard.sloReasonBreaker",
        "dashboard.sloReasonErrorRate", "dashboard.sloReasonFallbackRate",
        "dashboard.sloReasonTtft", "dashboard.sloReasonGap", "dashboard.sloReasonSaturated",
        "dashboard.sloReasonSlotLeak", "dashboard.sloReasonBudget",
    }
    missing = {lang: sorted(needed - blocks[lang]) for lang in _LANGS if needed - blocks[lang]}
    assert not missing, missing


def test_all_language_blocks_share_the_same_slo_keys():
    blocks = _locale_keys("dashboard.slo")
    baseline = blocks["zh-CN"]
    assert baseline, "no dashboard.slo* keys parsed - i18n.js format changed?"
    drift = {
        lang: {"missing": sorted(baseline - blocks[lang]), "extra": sorted(blocks[lang] - baseline)}
        for lang in _LANGS
        if blocks[lang] != baseline
    }
    assert not drift, drift


# --------------------------------------------------------------------------------------
# Markup: the panel and the ids the script writes to
# --------------------------------------------------------------------------------------

def test_dashboard_markup_contains_the_slo_panel():
    html = _DASH_HTML.read_text(encoding="utf-8")
    for element_id in ("slo-state", "slo-reasons", "slo-window-rows", "slo-latency", "slo-pool"):
        assert f'id="{element_id}"' in html, element_id
    # The state chip has to be announced by screen readers.
    assert 'id="slo-state"' in html
    assert 'aria-live="polite"' in html
    assert 'role="status"' in html


def test_slo_script_only_writes_to_ids_that_exist():
    html = _DASH_HTML.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    src = _APP_JS.read_text(encoding="utf-8")
    body = _extract_function(src, "renderSloOverview") + _extract_function(src, "renderSloMetrics")
    targets = set(re.findall(r"getElementById\('([^']+)'\)", body))
    assert targets, "no ids found - the extraction is broken"
    assert targets <= ids, sorted(targets - ids)


# --------------------------------------------------------------------------------------
# Frontend rendering (node)
# --------------------------------------------------------------------------------------

_PRELUDE = textwrap.dedent("""
    const _ARGV = JSON.parse(process.argv[2]);
    function t(k) { return 'T:' + k; }
    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function formatNumber(num) {
        if (num === null || num === undefined) return '0';
        return String(num);
    }
    function makeEl() {
        return { id: '', className: '', textContent: '', innerHTML: '', _attrs: {} };
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


def _run(body: str, argv: dict, tmp_path: Path):
    src = _APP_JS.read_text(encoding="utf-8")
    const_block = src[src.index("const SLO_REASON_KEYS"):]
    const_block = const_block[:const_block.index("};") + 2]
    functions = [
        _extract_function(src, name)
        for name in ("sloStateDescriptor", "formatRatio", "formatDurationMs",
                     "renderSloMetrics", "renderSloOverview")
    ]
    script = "\n".join([_PRELUDE, const_block, *functions, textwrap.dedent(body)])
    path = tmp_path / "slo.mjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([_NODE, str(path), json.dumps(argv)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@_needs_node
def test_state_descriptor_never_falls_back_to_ok(tmp_path):
    out = _run(
        """
        process.stdout.write(JSON.stringify({
            ok: sloStateDescriptor('ok'),
            degraded: sloStateDescriptor('degraded'),
            unknown: sloStateDescriptor('unknown'),
            garbage: sloStateDescriptor('gibberish'),
            missing: sloStateDescriptor(undefined),
        }));
        """,
        {}, tmp_path,
    )
    assert out["ok"]["state"] == "ok" and out["ok"]["labelKey"] == "dashboard.sloStateOk"
    assert out["degraded"]["labelKey"] == "dashboard.sloStateDegraded"
    assert out["unknown"]["state"] == "unknown"
    assert out["garbage"]["state"] == "unknown"
    assert out["missing"]["state"] == "unknown"


@_needs_node
def test_format_helpers(tmp_path):
    out = _run(
        """
        process.stdout.write(JSON.stringify({
            ratio: formatRatio(0.125), zero: formatRatio(0), bad: formatRatio('x'),
            ms: formatDurationMs(250), sec: formatDurationMs(1500), min: formatDurationMs(125000),
            none: formatDurationMs(0), nan: formatDurationMs('x'),
        }));
        """,
        {}, tmp_path,
    )
    assert out["ratio"] == "12.5%" and out["zero"] == "0.0%"
    assert out["bad"] == "-"
    assert out["ms"] == "250 ms" and out["sec"] == "1.5 s" and out["min"] == "2.1 min"
    assert out["none"] == "-" and out["nan"] == "-"


@_needs_node
def test_render_slo_overview_fills_the_panel(tmp_path):
    out = _run(
        """
        for (const id of ['slo-state', 'slo-reasons', 'slo-window-rows', 'slo-latency', 'slo-pool']) {
            _els[id] = makeEl();
        }
        renderSloOverview({
            state: 'degraded',
            reasons: ['breaker_open', 'error_rate'],
            windows: { '1h': { requests: 4, error_rate: 0.25, fallback_rate: 0.5,
                               ttft_ms: { p50: 1200, p95: 90000 },
                               latency_ms: { p50: 3000, p95: 40000 },
                               max_inter_chunk_gap_ms: 5000 } },
            breaker: { open: true },
            pool: { active_requests: 2, capacity: 8, saturation: 0.25, cooling_accounts: 1,
                    slot_leak_accounts: 0 },
            budget: { allowed: 3, blocked: 1 },
        });
        process.stdout.write(JSON.stringify({
            chipClass: _els['slo-state'].className,
            chipText: _els['slo-state'].textContent,
            reasons: _els['slo-reasons'].textContent,
            rows: _els['slo-window-rows'].innerHTML,
            latency: _els['slo-latency'].innerHTML,
            pool: _els['slo-pool'].innerHTML,
        }));
        """,
        {}, tmp_path,
    )
    assert out["chipClass"] == "slo-state slo-state--degraded"
    assert out["chipText"] == "T:dashboard.sloStateDegraded"
    assert "T:dashboard.sloReasonBreaker" in out["reasons"]
    assert "T:dashboard.sloReasonErrorRate" in out["reasons"]
    assert "1h" in out["rows"] and "25.0%" in out["rows"]
    assert "1.2 s" in out["latency"] and "1.5 min" in out["latency"]
    assert "2 / 8" in out["pool"] and "T:dashboard.sloBreakerOpen" in out["pool"]


@_needs_node
def test_render_slo_overview_escapes_hostile_values(tmp_path):
    out = _run(
        """
        for (const id of ['slo-state', 'slo-reasons', 'slo-window-rows', 'slo-latency', 'slo-pool']) {
            _els[id] = makeEl();
        }
        renderSloOverview({
            state: 'degraded',
            reasons: ['<img src=x onerror=alert(1)>'],
            windows: { '<b>1h</b>': { requests: 1 } },
            pool: {}, breaker: {}, budget: {},
        });
        process.stdout.write(JSON.stringify({
            reasons: _els['slo-reasons'].textContent,
            reasonsHtml: _els['slo-reasons'].innerHTML,
            rows: _els['slo-window-rows'].innerHTML,
        }));
        """,
        {}, tmp_path,
    )
    # The reasons are written as text (textContent), never as markup.
    assert out["reasonsHtml"] == ""
    assert "<img" in out["reasons"]
    # The window label is written into markup, so it must be escaped there.
    assert "<b>1h</b>" not in out["rows"]
    assert "&lt;b&gt;" in out["rows"]


@_needs_node
def test_render_slo_overview_survives_a_missing_payload(tmp_path):
    out = _run(
        """
        for (const id of ['slo-state', 'slo-reasons', 'slo-window-rows', 'slo-latency', 'slo-pool']) {
            _els[id] = makeEl();
        }
        renderSloOverview(undefined);
        process.stdout.write(JSON.stringify({
            chipClass: _els['slo-state'].className,
            chipText: _els['slo-state'].textContent,
            reasons: _els['slo-reasons'].textContent,
            rows: _els['slo-window-rows'].innerHTML,
        }));
        """,
        {}, tmp_path,
    )
    assert out["chipClass"] == "slo-state slo-state--unknown"
    assert out["chipText"] == "T:dashboard.sloStateUnknown"
    assert out["reasons"] == ""
    assert "sloNoData" in out["rows"]
