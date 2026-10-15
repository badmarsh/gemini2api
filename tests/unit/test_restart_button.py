"""Restart button must not lie (hardening follow-up A2).

Backend: GET /admin/system-info exposes ``restart_supported`` (False on Windows, where the
restart endpoint answers 501). Frontend: the click handler used to swallow every error and show
"Restarting service..." even on 501/5xx. The decision logic now lives in two small functions
(``restartButtonVisible`` / ``requestRestart``) that are cut out of the real app.js and run in node.

app.js contains NUL bytes: read it with read_text, never shell-grep it.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_AUTH = {"Authorization": "Bearer sk-test-key"}
_APP_JS = Path(__file__).resolve().parents[2] / "static" / "app" / "app.js"
_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node is not available")


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


def _run_node(body: str, tmp_path: Path):
    src = _APP_JS.read_text(encoding="utf-8")
    script = "\n".join(
        [_extract_function(src, "restartButtonVisible"), _extract_function(src, "requestRestart"),
         textwrap.dedent(body)]
    )
    path = tmp_path / "restart.mjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([_NODE, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ---- backend -------------------------------------------------------------------------------

def test_system_info_exposes_restart_supported(client, monkeypatch):
    from app.routers import admin

    monkeypatch.setattr(admin, "_restart_supported", lambda: True)
    resp = client.get("/admin/system-info", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["restart_supported"] is True

    monkeypatch.setattr(admin, "_restart_supported", lambda: False)
    resp = client.get("/admin/system-info", headers=_AUTH)
    assert resp.json()["restart_supported"] is False


# ---- frontend (node) -----------------------------------------------------------------------

@_needs_node
def test_button_hidden_only_when_backend_says_unsupported(tmp_path):
    out = _run_node(
        """
        process.stdout.write(JSON.stringify({
            no: restartButtonVisible({ restart_supported: false }),
            yes: restartButtonVisible({ restart_supported: true }),
            oldBackend: restartButtonVisible({ version: '1.0' }),
            nothing: restartButtonVisible(null),
        }));
        """,
        tmp_path,
    )
    assert out == {"no": False, "yes": True, "oldBackend": True, "nothing": True}


@_needs_node
def test_request_restart_reports_success_and_real_error_message(tmp_path):
    out = _run_node(
        """
        (async () => {
            const ok = await requestRestart(async () => ({ status: 'ok' }));
            const unsupported = await requestRestart(async () => {
                throw new Error('Restart is not supported on Windows');
            });
            const blank = await requestRestart(async () => { throw new Error(''); });
            const weird = await requestRestart(async () => { throw 'boom'; });
            process.stdout.write(JSON.stringify({ ok, unsupported, blank, weird }));
        })();
        """,
        tmp_path,
    )
    assert out["ok"] == {"ok": True, "message": ""}
    assert out["unsupported"] == {"ok": False, "message": "Restart is not supported on Windows"}
    assert out["blank"] == {"ok": False, "message": ""}
    assert out["weird"]["ok"] is False


def test_click_handler_stops_before_polling_when_restart_failed():
    src = _APP_JS.read_text(encoding="utf-8")
    start = src.index("const restartBtn = document.getElementById('restartBtn');")
    handler = src[start:start + 2500]
    assert "requestRestart(" in handler
    assert "catch (e) {}\n            setTimeout" not in handler
    failed = handler.index("if (!result.ok)")
    # the failure branch returns before the success toast and the /health polling start
    assert handler.index("return;", failed) < handler.index("toast.restarting")
    assert handler.index("return;", failed) < handler.index("/health")
