"""Hardening follow-ups A3-A5: generic fallback error, dir fsync, mkdir failure.

A3: PATCH /admin/api-keys/fallback must not echo the raw exception (local paths) to the client.
A4: atomic_rewrite_text fsyncs the parent directory (best effort) after a successful rename.
A5: a failing parent mkdir raises and leaves no temp file behind.
"""

import os
from pathlib import Path

import pytest

import app.routers.api_keys as ak
from app.config import settings
from app.core import settings_overrides as so
from app.utils import atomic_io
from app.utils.atomic_io import atomic_rewrite_text

_AUTH = {"Authorization": "Bearer sk-test-key"}
_SECRET_PATH = r"C:\Users\someone\private\project\.env"


# ---- A3 ------------------------------------------------------------------------------------

@pytest.fixture
def _fallback_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(so, "OVERRIDES_PATH", tmp_path / "settings-overrides.json")
    old = settings.fallback_enabled
    yield
    object.__setattr__(settings, "fallback_enabled", old)


def test_fallback_persist_failure_does_not_leak_the_exception_text(
    client, monkeypatch, _fallback_isolated
):
    def boom(updates):
        raise PermissionError(13, "Permission denied", _SECRET_PATH)

    monkeypatch.setattr(ak, "_update_env_file", boom)

    r = client.patch("/admin/api-keys/fallback", json={"enabled": True}, headers=_AUTH)

    assert r.status_code == 500
    assert "Failed to persist fallback setting" in r.text
    assert "someone" not in r.text
    assert "Permission denied" not in r.text
    assert ".env" not in r.text


# ---- A4 ------------------------------------------------------------------------------------

def test_docstring_first_line_admits_the_non_atomic_fallback():
    first = atomic_rewrite_text.__doc__.strip().splitlines()[0]
    assert "not atomic" in first.lower() or "except" in first.lower()


def test_parent_dir_is_fsynced_after_a_successful_replace(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(atomic_io, "_fsync_parent_dir", lambda p: calls.append(Path(p)))
    target = tmp_path / ".env"
    target.write_text("A=1\n", encoding="utf-8")

    atomic_rewrite_text(target, "A=2\n")

    assert calls == [target.parent]
    assert target.read_text(encoding="utf-8").strip() == "A=2"


def test_parent_dir_fsync_is_skipped_for_the_in_place_fallback(tmp_path, monkeypatch):
    import errno

    calls = []
    monkeypatch.setattr(atomic_io, "_fsync_parent_dir", lambda p: calls.append(p))

    def busy(src, dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(atomic_io.os, "replace", busy)
    target = tmp_path / ".env"
    target.write_text("A=1\n", encoding="utf-8")

    atomic_rewrite_text(target, "A=2\n")

    assert calls == []
    assert target.read_text(encoding="utf-8").strip() == "A=2"


def test_fsync_parent_dir_never_raises(tmp_path, monkeypatch):
    def deny(*a, **k):
        raise OSError("no directory handles here")

    monkeypatch.setattr(atomic_io.os, "open", deny)
    monkeypatch.setattr(atomic_io.os, "O_DIRECTORY", 0, raising=False)
    atomic_io._fsync_parent_dir(tmp_path)  # must not raise

    monkeypatch.delattr(atomic_io.os, "O_DIRECTORY", raising=False)  # Windows-like: attribute missing
    atomic_io._fsync_parent_dir(tmp_path)  # must not raise either


# ---- A5 ------------------------------------------------------------------------------------

def test_failing_parent_mkdir_raises_and_leaves_no_temp_file(tmp_path, monkeypatch):
    target = tmp_path / "newdir" / ".env"

    def deny(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "mkdir", deny)

    with pytest.raises(PermissionError):
        atomic_rewrite_text(target, "A=1\n")

    assert not os.path.exists(target.parent)
    assert list(tmp_path.iterdir()) == []
