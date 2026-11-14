"""First-run seed: a fresh clone has an empty data/, so the first start gets one starter ticker and the example
strategy notes. Runs only from the server's startup hook.

Invariant: seeding only ADDS files, by exclusive no-follow creation (O_CREAT|O_EXCL|O_NOFOLLOW relative to directory
fds opened with O_NOFOLLOW|O_DIRECTORY). It never overwrites, deletes, renames or follows a symlink, and any data
dir that holds anything beyond the two items a fresh clone ships is never seeded.

A data dir is fresh iff it does not exist, or every top-level entry is (a) '.DS_Store' as a regular file, or
(b) 'backups' as a real directory whose only entry is 'README.md' as a regular file. Anything else (including
'.locks', '.seeded', any other name, any symlink, any other directory) means: do nothing.

Ownership is CLAIMED by exclusively creating '.seeded' first; FileExistsError means someone else claimed or seeded, so
we stop. After the claim every step is best-effort: a failure is logged and seeding just stops (no retry, no progress
file, no temp files, no locks: the exclusive creation of '.seeded' is the only coordination). Honest residual: user
data created concurrently after the claim can at worst get an extra seed file added next to it, never overwritten."""
from __future__ import annotations

import json
import logging
import os
import stat
from datetime import datetime, timezone

import config
import strategy
from config import DATA_DIR

logger = logging.getLogger("seed")
EXAMPLES = config.ROOT / "examples" / "strategy"
STARTER_TICKERS = ["NVDA"]
MARKER = ".seeded"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
SUPPORTED = bool(_NOFOLLOW and _DIRECTORY and os.open in getattr(os, "supports_dir_fd", set()))


def _open_dir(name, dir_fd=None) -> int:
    """A directory fd that refuses symlinks (OSError for a symlink or a non-directory)."""
    return os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=dir_fd)


def _kind(name, dir_fd):
    try:
        return stat.S_IFMT(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return None


def _fresh(fd: int) -> bool:
    """True iff the (already opened, non-symlink) data dir holds nothing but the shipped fresh-clone items."""
    try:
        names = os.listdir(fd)
        for name in names:
            kind = _kind(name, fd)
            if name == ".DS_Store" and kind == stat.S_IFREG:
                continue
            # the app mkdirs these EMPTY at import time, before the startup hook runs: empty dirs hold no data
            if name in ("analysis", "price_cache", "market_cache") and kind == stat.S_IFDIR:
                sfd = _open_dir(name, fd)
                try:
                    if not os.listdir(sfd):
                        continue
                finally:
                    os.close(sfd)
                return False
            if name == "backups" and kind == stat.S_IFDIR:
                bfd = _open_dir(name, fd)
                try:
                    inner = os.listdir(bfd)
                    # the app creates an EMPTY backups/ at import time (before this runs) for a brand-new data dir
                    if not inner or (inner == ["README.md"] and _kind("README.md", bfd) == stat.S_IFREG):
                        continue
                finally:
                    os.close(bfd)
            return False
        return True
    except OSError:
        return False


def _create(dir_fd: int, name: str, text: str, mode: int = 0o644) -> None:
    """Exclusive, no-follow creation of name inside dir_fd; FileExistsError if anything is already there."""
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, mode, dir_fd=dir_fd)
    with os.fdopen(fd, "wb") as f:
        f.write(text.encode("utf-8"))


def _example_notes() -> list:
    out = []
    for p in sorted(EXAMPLES.glob("*")):
        if not strategy.NAME_RE.fullmatch(p.name) or p.name.startswith(".") or not p.is_file() or p.is_symlink():
            continue
        data = p.read_bytes()
        if len(data) > strategy.MAX_FILE or len(out) >= strategy.MAX_FILES:
            continue
        try:
            out.append((p.name, data.decode("utf-8")))
        except UnicodeDecodeError:
            continue
    return out


def maybe_seed():
    """-> the list of items seeded when this call claimed the dir, else None. Never raises."""
    fd = None
    try:
        if config.DEMO or os.environ.get("SD_NO_SEED") == "1" or not SUPPORTED:
            return None
        try:
            mode = os.lstat(DATA_DIR).st_mode
            if not stat.S_ISDIR(mode):  # a symlinked data dir is never touched
                return None
        except FileNotFoundError:
            os.makedirs(DATA_DIR.parent, exist_ok=True)
            try:
                os.mkdir(DATA_DIR)
            except FileExistsError:
                pass
        fd = _open_dir(DATA_DIR)
        if not _fresh(fd):
            return None
        try:  # CLAIM: whoever creates '.seeded' first owns the seeding; nobody else proceeds
            _create(fd, MARKER, "")
        except FileExistsError:
            return None
        try:
            notes = _example_notes()
            what = ["tickers.json"] + (["strategy/" + n for n, _ in notes] if notes else [])
            marker_fd = os.open(MARKER, os.O_WRONLY | _NOFOLLOW, dir_fd=fd)  # our own just-created file
            with os.fdopen(marker_fd, "wb") as f:
                f.write(json.dumps({"seeded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "what": what}, ensure_ascii=False).encode("utf-8"))
        except OSError as e:
            logger.warning("writing the seed marker failed (continuing): %s", e)
            notes = _example_notes()
        done = []
        try:
            _create(fd, "tickers.json", json.dumps(STARTER_TICKERS, ensure_ascii=False, indent=2))
            done.append("tickers.json")
            os.mkdir("strategy", dir_fd=fd)  # FileExistsError: someone else's strategy dir, skip the notes
            sfd = _open_dir("strategy", fd)
            try:
                for name, text in notes:
                    _create(sfd, name, text)
                    done.append("strategy/" + name)
            finally:
                os.close(sfd)
        except OSError as e:
            logger.warning("first-run seeding stopped: %s", e)
        return done
    except Exception as e:  # noqa: BLE001 — starting the server matters more than the starter data
        logger.warning("first-run seed skipped: %s", e)
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
