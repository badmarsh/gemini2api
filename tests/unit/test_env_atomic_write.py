"""Atomic rewrites of the user-editable .env file (audit item 7d).

``_update_env_file`` (panel settings) and ``_persist_api_key`` (generated key) used to truncate
.env in place, so a crash mid-write left a half-written file. They now go through
``atomic_io.atomic_rewrite_text``: temp file next to the target, copy the mode, ``os.replace``.
"""

import errno
import logging
import os
import stat
import sys
from pathlib import Path

import pytest

import app.config as config
from app.routers import settings as settings_router
from app.utils import atomic_io

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
NOT_ROOT = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores file permissions"
)


def _leftovers(directory: Path, name: str = ".env"):
    return [p.name for p in directory.iterdir() if p.name != name]


# --------------------------------------------------------------------------- helper itself


def test_rewrite_replaces_content_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / ".env"
    target.write_bytes(b"A=1\n")
    atomic_io.atomic_rewrite_text(target, "A=2\nB=3\n")
    assert target.read_text(encoding="utf-8").splitlines() == ["A=2", "B=3"]
    assert _leftovers(tmp_path) == []


def test_rewrite_creates_a_missing_file_and_parent(tmp_path):
    target = tmp_path / "sub" / ".env"
    atomic_io.atomic_rewrite_text(target, "A=1\n")
    assert target.read_text(encoding="utf-8").splitlines() == ["A=1"]
    assert _leftovers(target.parent) == []


def test_rewrite_keeps_the_newline_behaviour_of_path_write_text(tmp_path):
    reference = tmp_path / "reference"
    reference.write_text("A=1\nB=2\n", encoding="utf-8")
    target = tmp_path / ".env"
    atomic_io.atomic_rewrite_text(target, "A=1\nB=2\n")
    assert target.read_bytes() == reference.read_bytes()


def test_unwritable_target_raises_permission_error_and_is_untouched(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_bytes(b"A=1\n")
    monkeypatch.setattr(atomic_io.os, "access", lambda *a, **k: False)
    with pytest.raises(PermissionError):
        atomic_io.atomic_rewrite_text(target, "A=2\n")
    assert target.read_bytes() == b"A=1\n"
    assert _leftovers(tmp_path) == []


@NOT_ROOT
def test_really_read_only_file_is_rejected_up_front(tmp_path):
    target = tmp_path / ".env"
    target.write_bytes(b"A=1\n")
    target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            atomic_io.atomic_rewrite_text(target, "A=2\n")
        assert target.read_bytes() == b"A=1\n"
        assert _leftovers(tmp_path) == []
    finally:
        target.chmod(stat.S_IREAD | stat.S_IWRITE)


@POSIX_ONLY
def test_existing_file_mode_is_preserved(tmp_path):
    target = tmp_path / ".env"
    target.write_bytes(b"A=1\n")
    target.chmod(0o640)
    atomic_io.atomic_rewrite_text(target, "A=2\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@POSIX_ONLY
def test_new_file_is_private(tmp_path):
    target = tmp_path / ".env"
    atomic_io.atomic_rewrite_text(target, "A=1\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("code", [errno.EBUSY, errno.EXDEV])
def test_falls_back_to_a_direct_write_when_the_file_cannot_be_replaced(tmp_path, monkeypatch, code):
    """A bind-mounted single file (Docker) refuses rename-over: EBUSY / EXDEV."""
    target = tmp_path / ".env"
    target.write_bytes(b"A=1\n")

    def refuse(src, dst):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(atomic_io.os, "replace", refuse)
    atomic_io.atomic_rewrite_text(target, "A=2\n")
    assert target.read_text(encoding="utf-8").splitlines() == ["A=2"]
    assert _leftovers(tmp_path) == []


def test_other_replace_errors_propagate_and_leave_the_original(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_bytes(b"A=1\n")

    def explode(src, dst):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(atomic_io.os, "replace", explode)
    with pytest.raises(OSError):
        atomic_io.atomic_rewrite_text(target, "A=2\n")
    assert target.read_bytes() == b"A=1\n"
    assert _leftovers(tmp_path) == []


def test_failure_while_writing_the_temp_file_leaves_the_original(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_bytes(b"A=1\n")

    def boom(self, *args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError):
        atomic_io.atomic_rewrite_text(target, "A=2\n")
    monkeypatch.undo()
    assert target.read_bytes() == b"A=1\n"
    assert _leftovers(tmp_path) == []


# ------------------------------------------------------------- _update_env_file (settings)


@pytest.fixture
def panel_env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_router, "ENV_PATH", path)
    return path


def test_update_env_file_still_updates_and_adds_keys(panel_env):
    panel_env.write_bytes(b"# comment\nMAX_RETRIES=3\nKEEP=me\n")
    settings_router._update_env_file({"max_retries": 7, "log_bodies_enabled": True})
    lines = panel_env.read_text(encoding="utf-8").splitlines()
    assert lines == ["# comment", "MAX_RETRIES=7", "KEEP=me", "LOG_BODIES_ENABLED=True"]
    assert _leftovers(panel_env.parent) == []


def test_update_env_file_creates_a_missing_file(panel_env):
    settings_router._update_env_file({"max_retries": 7})
    assert panel_env.read_text(encoding="utf-8").splitlines() == ["MAX_RETRIES=7"]


def test_update_env_file_is_all_or_nothing_when_the_swap_fails(panel_env, monkeypatch):
    original = b"MAX_RETRIES=3\nKEEP=me\n"
    panel_env.write_bytes(original)

    def explode(src, dst):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        settings_router._update_env_file({"max_retries": 7})
    monkeypatch.undo()
    assert panel_env.read_bytes() == original
    assert _leftovers(panel_env.parent) == []


def test_update_env_file_on_unwritable_env_raises_permission_error(panel_env, monkeypatch):
    panel_env.write_bytes(b"MAX_RETRIES=3\n")
    monkeypatch.setattr(atomic_io.os, "access", lambda *a, **k: False)
    with pytest.raises(PermissionError):
        settings_router._update_env_file({"max_retries": 7})
    monkeypatch.undo()
    assert panel_env.read_bytes() == b"MAX_RETRIES=3\n"


# --------------------------------------------------------------- _persist_api_key (config)

GENERATED = "sk-generated-for-test"


@pytest.fixture
def key_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(config.settings, "api_key", GENERATED)
    return tmp_path / ".env"


def test_persist_api_key_swap_failure_keeps_env_intact_and_warns(key_env, monkeypatch, caplog):
    original = b"ADMIN_API_KEY=abc\nPORT=8000\n"
    key_env.write_bytes(original)

    def explode(src, dst):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(os, "replace", explode)
    with caplog.at_level(logging.INFO, logger=config.__name__):
        config._persist_api_key()  # must not raise
    monkeypatch.undo()

    assert key_env.read_bytes() == original
    assert _leftovers(key_env.parent) == []
    messages = [r.getMessage() for r in caplog.records]
    assert not any("saved to .env" in m for m in messages)
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_persist_api_key_new_file_goes_through_the_atomic_path(key_env, monkeypatch):
    calls = []
    real = atomic_io.atomic_rewrite_text

    def spy(path, data, *args, **kwargs):
        calls.append(str(path))
        return real(path, data, *args, **kwargs)

    monkeypatch.setattr(atomic_io, "atomic_rewrite_text", spy)
    config._persist_api_key()
    assert key_env.read_text(encoding="utf-8").splitlines() == [f"API_KEY={GENERATED}"]
    assert len(calls) == 1
