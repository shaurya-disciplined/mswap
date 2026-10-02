"""Cross-process file locking for coordinating concurrent mswap commands.

Owns advisory file locking on Windows and POSIX systems.
Must never leave unreleased file locks or leaked file descriptors.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Self

from mswap.core.errors import LockTimeout


class FileLock:
    """Cross-process advisory file lock with timeout."""

    def __init__(self, path: Path | str, timeout: float = 10.0, poll: float = 0.05) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self.poll = poll
        self._fd: int | None = None
        self._locked: bool = False

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()

    def acquire(self) -> None:
        """Acquire the file lock, blocking up to timeout seconds."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()

        while True:
            if self._fd is None:
                try:
                    self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT)
                except OSError:
                    self._fd = None

            if self._fd is not None:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        os.lseek(self._fd, 0, os.SEEK_SET)
                        msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                    self._locked = True
                    return
                except OSError:
                    pass

            if (time.monotonic() - start) >= self.timeout:
                self.release()
                raise LockTimeout(
                    "Another mswap command is running.",
                    hint="Wait a moment and try again.",
                )

            time.sleep(self.poll)

    def release(self) -> None:
        """Release the file lock if held, closing the file descriptor."""
        if self._fd is not None:
            try:
                if self._locked:
                    if sys.platform == "win32":
                        import msvcrt

                        with contextlib.suppress(OSError):
                            os.lseek(self._fd, 0, os.SEEK_SET)
                            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        with contextlib.suppress(OSError):
                            fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                self._locked = False
                with contextlib.suppress(OSError):
                    os.close(self._fd)
                self._fd = None
