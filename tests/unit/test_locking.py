"""Unit tests for cross-process FileLock."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mswap.core.errors import LockTimeout
from mswap.core.locking import FileLock


def test_lock_acquire_and_release(tmp_path: Path) -> None:
    lock_file = tmp_path / "test.lock"
    lock = FileLock(lock_file, timeout=1.0)

    with lock:
        assert lock._fd is not None
        assert lock._locked is True

    assert lock._fd is None
    assert lock._locked is False


def test_lock_release_on_exception(tmp_path: Path) -> None:
    lock_file = tmp_path / "test.lock"
    lock = FileLock(lock_file, timeout=1.0)

    with pytest.raises(RuntimeError, match="boom"), lock:
        assert lock._locked is True
        raise RuntimeError("boom")

    assert lock._fd is None
    assert lock._locked is False


def test_two_locks_timeout(tmp_path: Path) -> None:
    lock_file = tmp_path / "test.lock"
    lock1 = FileLock(lock_file, timeout=1.0)
    lock2 = FileLock(lock_file, timeout=0.1, poll=0.02)

    with lock1:
        start = time.monotonic()
        with pytest.raises(LockTimeout) as exc_info, lock2:
            pass
        duration = time.monotonic() - start
        assert duration >= 0.1
        err = exc_info.value
        assert err.code == 7
        assert "Another mswap command is running." in err.message
        assert err.hint == "Wait a moment and try again."

    # Now that lock1 is released, lock2 can acquire
    with lock2:
        assert lock2._locked is True


def test_lock_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir" / "test.lock"
    lock = FileLock(nested, timeout=1.0)
    with lock:
        assert nested.parent.exists()
        assert lock._locked is True
