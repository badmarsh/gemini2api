"""原子化落盘工具（VULN-010）。

写「同目录临时文件 + os.replace」覆盖目标，避免进程在写入中途崩溃/断电导致
凭据文件（accounts/cookies/api-keys）被截断成半截 JSON 而损坏。
os.replace 在同一文件系统上是原子操作，故临时文件必须与目标同目录。
"""

import errno
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Union


def atomic_write_text(path: Union[str, Path], data: str, encoding: str = "utf-8") -> None:
    """原子写文本：同目录写 .tmp（fsync 落盘）后 os.replace 覆盖目标。失败不留残缺目标文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        # rename 本身（目录项更新）在多数 POSIX 文件系统上要 fsync 父目录后才保证
        # 崩溃后持久；否则断电可能丢失这次 rename，目标回退到旧版本。补 fsync 父目录。
        # （Windows 无 O_DIRECTORY，整体 try/except 兜底为 no-op，不破坏默认部署。）
        try:
            dfd = os.open(str(target.parent), os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except (OSError, AttributeError):
            pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: Union[str, Path],
    obj: Any,
    *,
    indent: Union[int, None] = None,
    ensure_ascii: bool = False,
) -> None:
    """原子写 JSON（序列化参数与原生 json.dump 对齐，保持文件格式不变）。"""
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii))


def _fsync_parent_dir(parent: Union[str, Path]) -> None:
    """Best-effort fsync of a directory so a completed rename survives a crash (POSIX only)."""
    try:
        dfd = os.open(str(parent), os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except (OSError, AttributeError):
        pass  # Windows has no O_DIRECTORY; durability is best effort


def atomic_rewrite_text(path: Union[str, Path], data: str, encoding: str = "utf-8") -> None:
    """Rewrite a config file (``.env``) via temp file + rename, keeping its mode; NOT atomic in the in-place fallback.

    Unlike :func:`atomic_write_text` this is meant for files an operator may deliberately
    make read-only or bind-mount into a container:

    * An existing target that is not writable raises ``PermissionError`` up front. A plain
      rename would otherwise silently replace a read-only file on POSIX, and callers rely on
      the error to roll back their other persisted layers.
    * The data goes to a temp file next to the target (fsync, mode copied from the target),
      then ``os.replace`` swaps it in, so a crash never leaves a truncated file behind.
    * ``EBUSY`` / ``EXDEV`` from the swap (a single file bind-mounted into a container cannot
      be renamed over) fall back to a direct in-place write, which is NOT atomic.
    * After a successful rename the parent directory is fsynced (best effort, POSIX only).

    Text is written with ``Path.write_text``, so newline translation matches the direct writes
    this replaces. A new file is created private (0600) by ``mkstemp``.
    """
    target = Path(path)
    existing = target.exists()
    if existing and not os.access(target, os.W_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        Path(tmp).write_text(data, encoding=encoding)
        try:
            sync_fd = os.open(tmp, os.O_RDWR | getattr(os, "O_BINARY", 0))
            try:
                os.fsync(sync_fd)
            finally:
                os.close(sync_fd)
        except OSError:
            pass  # durability is best effort; atomicity does not depend on it
        if existing:
            shutil.copymode(target, tmp)
        try:
            os.replace(tmp, target)
        except OSError as e:
            if e.errno not in (errno.EBUSY, errno.EXDEV):
                raise
            target.write_text(data, encoding=encoding)
        else:
            _fsync_parent_dir(target.parent)
    finally:
        try:
            os.unlink(tmp)  # already gone after a successful os.replace
        except OSError:
            pass
