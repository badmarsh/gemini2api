"""Static guard: text-mode file I/O must always name its encoding.

On native Windows the default text encoding is the ANSI code page (cp1252 here), not UTF-8.
A bare ``open(path)`` / ``Path.read_text()`` / ``Path.write_text(...)`` therefore corrupts or
rejects non-ASCII data. This test scans the runtime code and fails on any such call.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("app", "refresher")  # plus top-level *.py scripts

# ``<name>.open(...)`` for these names is not file text I/O (or is binary-only).
_NON_TEXT_OPEN_OWNERS = {"os", "webbrowser", "tarfile", "zipfile", "gzip", "bz2", "lzma", "Image"}


def _owner_name(func: ast.Attribute) -> str | None:
    return func.value.id if isinstance(func.value, ast.Name) else None


def _mode_literal(call: ast.Call, mode_pos: int) -> str | None:
    """Literal mode string; '' when omitted (text read); None when not a literal."""
    node = call.args[mode_pos] if len(call.args) > mode_pos else None
    for kw in call.keywords:
        if kw.arg == "mode":
            node = kw.value
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _has_encoding_kw(call: ast.Call) -> bool:
    # ``**kwargs`` may carry encoding; give it the benefit of the doubt.
    return any(kw.arg in ("encoding", None) for kw in call.keywords)


def find_violations(source: str, filename: str = "<src>") -> list[tuple[int, str]]:
    """Return (lineno, description) for each text-mode I/O call lacking an encoding."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        label = None

        if isinstance(func, ast.Name) and func.id == "open":
            mode = _mode_literal(node, 1)
            if not (mode is not None and "b" in mode) and not _has_encoding_kw(node):
                label = "open()"
        elif isinstance(func, ast.Attribute):
            owner = _owner_name(func)
            if func.attr == "open" and owner not in _NON_TEXT_OPEN_OWNERS:
                # io.open(file, mode, ...) vs Path.open(mode, ...)
                mode = _mode_literal(node, 1 if owner == "io" else 0)
                if not (mode is not None and "b" in mode) and not _has_encoding_kw(node):
                    label = f"{ast.unparse(func)}()"
            elif func.attr == "fdopen" and owner == "os":
                mode = _mode_literal(node, 1)
                if not (mode is not None and "b" in mode) and not _has_encoding_kw(node):
                    label = "os.fdopen()"
            elif func.attr == "read_text":
                if not (node.args or _has_encoding_kw(node)):
                    label = f"{ast.unparse(func)}()"
            elif func.attr == "write_text":
                if not (len(node.args) >= 2 or _has_encoding_kw(node)):
                    label = f"{ast.unparse(func)}()"

        if label:
            found.append((node.lineno, label))
    return sorted(found)


def _runtime_sources() -> list[Path]:
    files: list[Path] = []
    for name in SCAN_DIRS:
        files.extend(sorted((REPO_ROOT / name).rglob("*.py")))
    files.extend(sorted(REPO_ROOT.glob("*.py")))
    return [f for f in files if "__pycache__" not in f.parts]


# --------------------------------------------------------------------------- detector self-tests

def test_detector_flags_bare_text_io():
    src = (
        "from pathlib import Path\n"
        "import os\n"
        "open('a.txt')\n"
        "open('a.txt', 'w')\n"
        "Path('a').read_text()\n"
        "Path('a').write_text('x')\n"
        "Path('a').open('w')\n"
        "os.fdopen(3, 'w')\n"
    )
    assert [line for line, _ in find_violations(src)] == [3, 4, 5, 6, 7, 8]


def test_detector_flags_multiline_calls():
    src = (
        "from pathlib import Path\n"
        "p = Path('a')\n"
        "p.write_text(\n"
        "    'x',\n"
        ")\n"
        "with open(\n"
        "    'a.txt',\n"
        "    'w',\n"
        ") as f:\n"
        "    pass\n"
    )
    assert [line for line, _ in find_violations(src)] == [3, 6]


def test_detector_accepts_explicit_encoding_and_binary_and_os_open():
    src = (
        "from pathlib import Path\n"
        "import os, io\n"
        "open('a.txt', encoding='utf-8')\n"
        "open('a.txt', 'w', encoding='utf-8')\n"
        "open('a.bin', 'rb')\n"
        "open('a.bin', mode='wb')\n"
        "Path('a').read_text(encoding='utf-8')\n"
        "Path('a').read_text('utf-8')\n"
        "Path('a').write_text('x', encoding='utf-8')\n"
        "Path('a').write_text('x', 'utf-8')\n"
        "Path('a').open('rb')\n"
        "Path('a').open('w', encoding='utf-8')\n"
        "io.open('a', 'rb')\n"
        "os.open('a', os.O_RDONLY)\n"
        "os.fdopen(3, 'w', encoding='utf-8')\n"
        "os.fdopen(3, 'wb')\n"
    )
    assert find_violations(src) == []


# --------------------------------------------------------------------------- the actual guard

def test_runtime_code_always_names_a_text_encoding():
    offenders = []
    for path in _runtime_sources():
        source = path.read_text(encoding="utf-8-sig")
        for lineno, label in find_violations(source, str(path)):
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: {label}")
    assert not offenders, (
        "Text-mode file I/O without encoding= (breaks non-ASCII data on native Windows):\n  "
        + "\n  ".join(offenders)
    )
