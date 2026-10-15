"""Fixtures shared by the unit tests in this directory."""

import builtins
import io

import pytest


@pytest.fixture
def cp1252_locale(monkeypatch):
    """Simulate a native-Windows ANSI locale for text-mode I/O.

    Text-mode ``open()`` / ``Path.read_text()`` / ``Path.write_text()`` calls that do not
    pass ``encoding=`` fall back to cp1252 instead of the interpreter's real default.
    This makes encoding regressions reproducible on any OS and in any UTF-8 mode.
    Calls with an explicit ``encoding=`` are untouched.
    """
    real_open = io.open

    def _open(file, mode="r", buffering=-1, encoding=None, errors=None,
              newline=None, closefd=True, opener=None):
        # pathlib passes encoding="locale" (via io.text_encoding) when none was given.
        if encoding in (None, "locale") and "b" not in mode:
            encoding = "cp1252"
        return real_open(file, mode, buffering, encoding, errors, newline, closefd, opener)

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(io, "open", _open)
    yield
