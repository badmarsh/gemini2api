"""POST /admin/restart: confirm gate, Windows 501, log flush before the signal (audit item 7c).

On Windows ``os.kill(pid, SIGTERM)`` is a hard TerminateProcess: no graceful shutdown (so the
lifespan never flushes the log store) and nothing brings the process back, so the endpoint
refuses there instead of silently killing the service.
"""

import os
import signal
import sys
import threading
from pathlib import Path

import pytest

_AUTH = {"Authorization": "Bearer sk-test-key"}
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class _RecordingLogStore:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail

    def add(self, *a, **k):
        return None

    def flush(self, *a, **k):
        self.events.append("flush")
        if self.fail:
            raise OSError("disk full")


@pytest.fixture
def restart_env(client, app_main, monkeypatch):
    """TestClient plus recorders for the log flush and the self-termination signal."""
    from app.routers import admin

    events = []
    done = threading.Event()

    def fake_terminate():
        events.append("terminate")
        done.set()

    monkeypatch.setattr(admin, "_terminate_self", fake_terminate)
    monkeypatch.setattr(admin, "_RESTART_DELAY_SECONDS", 0)
    monkeypatch.setattr(admin, "_restart_supported", lambda: True)
    monkeypatch.setattr(app_main.app.state, "log_store", _RecordingLogStore(events))
    return client, events, done, admin, app_main


def test_without_confirm_returns_400_and_does_nothing(restart_env):
    client, events, done, _admin, _main = restart_env
    resp = client.post("/admin/restart", headers=_AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "confirmation_required"
    assert not done.wait(0.3)
    assert events == []


def test_confirm_gate_comes_before_the_platform_check(restart_env, monkeypatch):
    client, events, done, admin, _main = restart_env
    monkeypatch.setattr(admin, "_restart_supported", lambda: False)
    resp = client.post("/admin/restart", headers=_AUTH)
    assert resp.status_code == 400
    assert events == []


def test_windows_returns_501_without_flush_or_signal(restart_env, monkeypatch):
    client, events, done, admin, _main = restart_env
    monkeypatch.setattr(admin, "_restart_supported", lambda: False)
    resp = client.post("/admin/restart?confirm=true", headers=_AUTH)
    assert resp.status_code == 501
    body = resp.json()["error"]
    assert body["type"] == "not_implemented"
    assert "Windows" in body["message"]
    assert not done.wait(0.3)
    assert events == []


def test_supported_platform_flushes_logs_before_signalling(restart_env):
    client, events, done, _admin, _main = restart_env
    resp = client.post("/admin/restart?confirm=true", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert done.wait(3), "terminate signal was never sent"
    assert events == ["flush", "terminate"]


def test_flush_failure_does_not_block_the_restart(restart_env, app_main, monkeypatch):
    client, events, done, _admin, _main = restart_env
    monkeypatch.setattr(app_main.app.state, "log_store", _RecordingLogStore(events, fail=True))
    resp = client.post("/admin/restart?confirm=true", headers=_AUTH)
    assert resp.status_code == 200
    assert done.wait(3)
    assert events == ["flush", "terminate"]


def test_missing_log_store_does_not_block_the_restart(restart_env):
    """Call the handler directly: the logging middleware itself needs a log store, the handler must not."""
    import asyncio
    from types import SimpleNamespace

    _client, events, done, admin, _main = restart_env
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    result = asyncio.run(admin.restart_server(request, confirm=True))
    assert result["status"] == "ok"
    assert done.wait(3)
    assert events == ["terminate"]


def test_requires_admin_auth(restart_env):
    client, events, done, _admin, _main = restart_env
    resp = client.post("/admin/restart?confirm=true")
    assert resp.status_code in (401, 403)
    assert not done.wait(0.3)
    assert events == []


def test_terminate_self_sends_sigterm_to_own_pid(monkeypatch):
    from app.routers import admin

    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
    admin._terminate_self()
    assert calls == [(os.getpid(), signal.SIGTERM)]


@pytest.mark.parametrize(
    "platform, expected",
    [("win32", False), ("linux", True), ("darwin", True)],
)
def test_restart_supported_follows_sys_platform(monkeypatch, platform, expected):
    from app.routers import admin

    monkeypatch.setattr(sys, "platform", platform)
    assert admin._restart_supported() is expected


def test_ui_restart_call_supplies_the_confirm_param():
    """The UI confirms in a modal first, then must pass ?confirm=true or it always gets 400."""
    js = (_REPO_ROOT / "static" / "app" / "app.js").read_text(encoding="utf-8")
    assert "'/admin/restart?confirm=true'" in js
    assert "'/admin/restart')" not in js
