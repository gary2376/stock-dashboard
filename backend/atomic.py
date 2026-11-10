"""Crash-safe writes and a per-name non-blocking file lock.

Every JSON the dashboard keeps (holdings, analysis, caches) used to be written in place, so a
crash or two concurrent writers could leave a truncated file. write_text_atomic writes a sibling
temp file and os.replace()s it, which is atomic on the same filesystem."""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no locking, writes are still atomic
    fcntl = None


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text_exclusive(path: Path, text: str) -> None:
    """Like write_text_atomic but refuses to replace: raises FileExistsError if path exists.
    For history files that must never be overwritten (os.link fails atomically if the name is taken)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.link(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@contextmanager
def try_lock(lock_path: Path):
    """Yields True if this process got the exclusive lock, False if another holds it."""
    if fcntl is None:
        yield True
        return
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
