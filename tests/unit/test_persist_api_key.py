"""_persist_api_key must write the generated key whenever .env has no real API_KEY= line."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.config as config

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED = "sk-generated-for-test"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(config.settings, "api_key", GENERATED)
    return tmp_path / ".env"


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def test_admin_api_key_line_is_not_mistaken_for_api_key(env_file):
    env_file.write_bytes(b"ADMIN_API_KEY=abc\nPORT=8000\n")

    config._persist_api_key()

    lines = _read(env_file).splitlines()
    assert f"API_KEY={GENERATED}" in lines
    assert "ADMIN_API_KEY=abc" in lines
    assert "PORT=8000" in lines


def test_empty_api_key_line_is_replaced_in_place(env_file):
    env_file.write_bytes(b"A=1\nAPI_KEY=\nB=2\n")

    config._persist_api_key()

    assert _read(env_file).splitlines() == ["A=1", f"API_KEY={GENERATED}", "B=2"]


@pytest.mark.parametrize("line", ["export API_KEY=", "  API_KEY = ", 'API_KEY=""'])
def test_empty_api_key_variants_are_replaced(env_file, line):
    env_file.write_bytes(f"{line}\nB=2\n".encode())

    config._persist_api_key()

    text = _read(env_file)
    assert f"API_KEY={GENERATED}" in text.splitlines()
    assert "B=2" in text.splitlines()
    assert text.count("API_KEY") == 1


def test_existing_non_empty_api_key_is_left_alone(env_file):
    original = b"ADMIN_API_KEY=abc\nAPI_KEY=sk-real\n"
    env_file.write_bytes(original)

    config._persist_api_key()

    assert env_file.read_bytes() == original


def test_missing_env_file_is_created(env_file):
    config._persist_api_key()

    assert _read(env_file).splitlines() == [f"API_KEY={GENERATED}"]


def test_saved_is_logged_only_after_a_successful_write(env_file, monkeypatch, caplog):
    env_file.write_bytes(b"ADMIN_API_KEY=abc\n")

    def boom(self, *args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_text", boom)
    with caplog.at_level(logging.INFO, logger=config.__name__):
        config._persist_api_key()  # must not raise

    messages = [r.getMessage() for r in caplog.records]
    assert not any("saved to .env" in m for m in messages)
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_generated_key_is_stable_across_two_fresh_interpreters(tmp_path):
    """End to end: .env has only ADMIN_API_KEY, API_KEY unset; restarts must reuse the same key."""
    (tmp_path / ".env").write_bytes(b"ADMIN_API_KEY=abc\n")
    env = {k: v for k, v in os.environ.items() if k.upper() != "API_KEY"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    code = "import app.config as c; print(c.settings.api_key)"

    def start() -> str:
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip().splitlines()[-1]

    first, second = start(), start()

    assert first.startswith("sk-")
    assert first == second
