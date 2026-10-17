"""Request explorer: redacted per-request timeline, drill-down endpoint and rendering.

The explorer must correlate one request end to end (ingress, first token, egress)
without ever exposing a body, a prompt, a cookie, an Authorization header or an
account identifier.
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


def _record(**overrides) -> dict:
    record = {
        "request_id": "abc12345",
        "ts": "2026-10-16T12:00:05.000000+00:00",
        "ingress_ts": "2026-10-16T12:00:00.000000+00:00",
        "model": "gemini-4-argon",
        "served_by": "gemini-flash-thinking",
        "account_hash": "9f8e7d6c",
        "status": 200,
        "stream": True,
        "path": "/v1/chat/completions",
        "ttft_ms": 1500.0,
        "total_latency_ms": 5000.0,
        "max_inter_chunk_gap_ms": 250.0,
        "error_class": "",
        "fallback_used": True,
        "fallback_reason": "stall_breaker",
        "regenerations": 1,
        "finish_reason": "stop",
        "parser_failure": "tool_json_invalid",
        "parser_response_length": 512,
        "parser_repaired": True,
        "truncated_suspected": False,
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------------------

def test_timeline_correlates_ingress_first_token_and_egress():
    from app.core.request_timeline import build_timeline

    timeline = build_timeline(_record())
    assert timeline is not None
    assert timeline["ingress_ts"] == "2026-10-16T12:00:00.000000+00:00"
    assert timeline["egress_ts"] == "2026-10-16T12:00:05.000000+00:00"
    kinds = [event["kind"] for event in timeline["events"]]
    assert kinds[0] == "ingress" and kinds[-1] == "egress"
    assert "first_token" in kinds and "max_gap" in kinds
    offsets = {event["kind"]: event["at_ms"] for event in timeline["events"]}
    assert offsets["first_token"] == 1500.0
    assert offsets["egress"] == 5000.0


def test_timeline_shows_requested_and_serving_model():
    from app.core.request_timeline import build_timeline

    timeline = build_timeline(_record())
    assert timeline["requested_model"] == "gemini-4-argon"
    assert timeline["serving_model"] == "gemini-flash-thinking"
    assert timeline["model_substituted"] is True
    same = build_timeline(_record(served_by="gemini-4-argon"))
    assert same["model_substituted"] is False


def test_timeline_carries_fallback_parser_and_regeneration_markers():
    from app.core.request_timeline import build_timeline

    timeline = build_timeline(_record())
    kinds = [marker["kind"] for marker in timeline["markers"]]
    assert "fallback" in kinds and "regeneration" in kinds
    assert "parser_failure" in kinds and "parser_repair" in kinds
    assert timeline["fallback"]["reason"] == "stall_breaker"
    assert timeline["parser"]["failure_class"] == "tool_json_invalid"
    assert timeline["regenerations"] == 1
    assert timeline["finish_reason"] == "stop"


def test_timeline_never_contains_identifiers_or_bodies():
    from app.core.request_timeline import build_timeline

    record = _record()
    record["request"] = {"messages": [{"content": "secret prompt"}]}
    record["response"] = {"choices": [{"text": "secret answer"}]}
    timeline = build_timeline(record)
    serialized = json.dumps(timeline)
    assert "9f8e7d6c" not in serialized
    assert "secret prompt" not in serialized
    assert "secret answer" not in serialized
    assert "account_hash" not in timeline
    assert "authorization" not in timeline
    assert "cookie" not in timeline


def test_timeline_of_an_empty_record_is_still_well_formed():
    from app.core.request_timeline import build_timeline

    assert build_timeline(None) is None
    timeline = build_timeline({})
    assert timeline["request_id"] == ""
    assert timeline["events"][-1]["kind"] == "egress"


def test_debug_metrics_endpoint_returns_the_timeline(client):
    from app.core.debug_metrics import metrics

    metrics.reset()
    metrics.record(_record())
    response = client.get("/admin/debug-metrics/abc12345", headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 200
    body = response.json()["request"]
    assert body["request_id"] == "abc12345"
    assert "9f8e7d6c" not in response.text
    metrics.reset()


def test_debug_metrics_endpoint_reports_unknown_requests(client):
    response = client.get("/admin/debug-metrics/does-not-exist", headers={"Authorization": "Bearer sk-test-key"})
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"


def test_debug_metrics_endpoint_requires_the_admin_key(client):
    assert client.get("/admin/debug-metrics/abc12345").status_code == 401


def test_store_lookup_is_exact_and_bounded():
    from app.core.debug_metrics import DebugMetricsStore

    store = DebugMetricsStore(capacity=2)
    store.record(_record(request_id="one"))
    store.record(_record(request_id="two"))
    store.record(_record(request_id="three"))
    assert store.record_by_id("three")["request_id"] == "three"
    # "one" was evicted from the bounded buffer.
    assert store.record_by_id("one") is None
    assert store.record_by_id("") is None
    assert store.record_by_id(None) is None


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


def test_explorer_keys_exist_in_every_language():
    blocks = _locale_keys("dashboard.explorer")
    assert set(_LANGS) <= set(blocks), sorted(blocks)
    needed = {
        "dashboard.explorerTitle", "dashboard.explorerPrivacy", "dashboard.explorerFilter",
        "dashboard.explorerFilterPlaceholder", "dashboard.explorerTableCaption",
        "dashboard.explorerTime", "dashboard.explorerRequest", "dashboard.explorerModel",
        "dashboard.explorerStatus", "dashboard.explorerTtft", "dashboard.explorerDuration",
        "dashboard.explorerNoSelection", "dashboard.explorerNoData", "dashboard.explorerTimeline",
        "dashboard.explorerMarkers", "dashboard.explorerIngress", "dashboard.explorerEgress",
        "dashboard.explorerRequestedModel", "dashboard.explorerServingModel",
        "dashboard.explorerFallback", "dashboard.explorerParser", "dashboard.explorerRegenerations",
        "dashboard.explorerErrorClass", "dashboard.explorerFinishReason", "dashboard.explorerStream",
    }
    missing = {lang: sorted(needed - blocks[lang]) for lang in _LANGS if needed - blocks[lang]}
    assert not missing, missing


def test_all_language_blocks_share_the_same_explorer_keys():
    blocks = _locale_keys("dashboard.explorer")
    baseline = blocks["zh-CN"]
    assert baseline, "no dashboard.explorer* keys parsed - i18n.js format changed?"
    drift = {
        lang: {"missing": sorted(baseline - blocks[lang]), "extra": sorted(blocks[lang] - baseline)}
        for lang in _LANGS
        if blocks[lang] != baseline
    }
    assert not drift, drift


def test_dashboard_markup_contains_the_explorer():
    html = _DASH_HTML.read_text(encoding="utf-8")
    for element_id in ("explorerFilter", "explorerCount", "explorer-rows", "explorer-detail",
                       "explorerRefreshBtn"):
        assert f'id="{element_id}"' in html, element_id
    assert 'aria-live="polite"' in html
    assert 'for="explorerFilter"' in html, "the filter input needs a label"


def test_explorer_script_only_writes_to_ids_that_exist():
    html = _DASH_HTML.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    src = _APP_JS.read_text(encoding="utf-8")
    body = ""
    for name in ("renderRequestExplorer", "renderRequestDetail", "openRequestDetail"):
        body += _extract_function(src, name)
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
    function escapeAttr(text) { return escapeHtml(text).replace(/"/g, '&quot;'); }
    function formatNumber(num) { return String(num == null ? 0 : num); }
    function formatDurationMs(value) {
        const ms = Number(value);
        if (!isFinite(ms) || ms <= 0) return '-';
        if (ms < 1000) return Math.round(ms) + ' ms';
        if (ms < 60000) return (ms / 1000).toFixed(1) + ' s';
        return (ms / 60000).toFixed(1) + ' min';
    }
    function makeEl() {
        return {
            id: '', className: '', textContent: '', innerHTML: '', value: '',
            _attrs: {}, listeners: {},
            setAttribute(k, v) { this._attrs[k] = v; },
            addEventListener(ev, fn) { this.listeners[ev] = fn; },
            querySelectorAll() { return []; },
        };
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
    functions = [
        _extract_function(src, name)
        for name in ("explorerMatchesFilter", "explorerRows", "formatExplorerTime")
    ]
    script = "\n".join([_PRELUDE, *functions, textwrap.dedent(body)])
    path = tmp_path / "explorer.mjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([_NODE, str(path), json.dumps(argv)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@_needs_node
def test_filter_matches_operational_fields_only(tmp_path):
    out = _run(
        """
        const record = { request_id: 'abc', model: 'gemini-4-argon', served_by: 'argon',
                         status: 200, error_class: 'upstream_error' };
        process.stdout.write(JSON.stringify({
            empty: explorerMatchesFilter(record, ''),
            id: explorerMatchesFilter(record, 'abc'),
            model: explorerMatchesFilter(record, 'ARGON'),
            status: explorerMatchesFilter(record, '200'),
            error: explorerMatchesFilter(record, 'upstream'),
            miss: explorerMatchesFilter(record, 'zzz'),
            nullRecord: explorerMatchesFilter(null, 'abc'),
        }));
        """,
        {}, tmp_path,
    )
    assert out["empty"] is True
    assert out["id"] and out["model"] and out["status"] and out["error"]
    assert out["miss"] is False
    assert out["nullRecord"] is False


@_needs_node
def test_rows_escape_hostile_values(tmp_path):
    out = _run(
        """
        const rows = explorerRows([{ request_id: '<b>id</b>', model: '<img src=x onerror=alert(1)>',
                                     status: 200, ttft_ms: 10, total_latency_ms: 20 }], '', '');
        process.stdout.write(JSON.stringify(rows));
        """,
        {}, tmp_path,
    )
    assert out["count"] == 1
    assert "<b>id</b>" not in out["html"]
    assert "<img" not in out["html"]
    assert "&lt;b&gt;" in out["html"]


@_needs_node
def test_rows_report_no_data(tmp_path):
    out = _run(
        """
        process.stdout.write(JSON.stringify({
            empty: explorerRows([], '', ''),
            filtered: explorerRows([{ request_id: 'abc' }], 'zzz', ''),
        }));
        """,
        {}, tmp_path,
    )
    assert out["empty"]["count"] == 0
    assert "explorerNoData" in out["empty"]["html"]
    assert out["filtered"]["count"] == 0
