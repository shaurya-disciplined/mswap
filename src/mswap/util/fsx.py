"""Private-file helpers for mswap's data directory.

Everything mswap keeps on disk (account metadata, journal, event log, the cached OAuth client)
is only for the current user. On POSIX that means a ``0700`` directory and ``0600`` files, no
matter what umask the process inherited. On Windows the data directory sits under
``%LOCALAPPDATA%``, which already carries a user-only ACL, so these helpers only add the
atomic-write behaviour there.

Only call these for files inside the mswap data directory. They must never be pointed at
directories that belong to another program (``~/.gemini``) or to the user (export targets).
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) and, on POSIX, make it ``0700``. Returns ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            path.chmod(PRIVATE_DIR_MODE)
    return path


def write_private_text(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text``; the file is created ``0600`` on POSIX."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        tmp.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def append_private_text(path: Path, text: str) -> None:
    """Append ``text`` to ``path``, creating it ``0600`` on POSIX."""
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE_MODE)
    with os.fdopen(fd, "a", encoding="utf-8", newline="") as handle:
        handle.write(text)
