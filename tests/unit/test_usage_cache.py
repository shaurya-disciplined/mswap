"""Unit tests for core/usage_cache.py storage, locking, round-trip, and pruning."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mswap.core.locking import FileLock
from mswap.core.models import Bucket, Pool, QuotaSnapshot
from mswap.core.usage_cache import UsageCache


def _make_snapshot(rem: float = 0.85) -> QuotaSnapshot:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    bucket = Bucket(window="5h", remaining=rem, reset_at=now + timedelta(hours=3))
    pool = Pool(key="gemini", name="Gemini", buckets=(bucket,))
    return QuotaSnapshot(fetched_at=now, pools=(pool,))


def test_cache_round_trip(tmp_path: Path) -> None:
    # Arrange
    cache_path = tmp_path / "usage.json"
    cache = UsageCache(cache_path)
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    snap = _make_snapshot(0.75)

    # Act: put snapshot
    cache.put_snapshot("fp12345678901234", snap, now)

    # Assert: read back with a new UsageCache instance
    cache2 = UsageCache(cache_path)
    entry = cache2.get("fp12345678901234")
    assert entry is not None
    assert entry.fetched_at == now
    assert entry.error is None
    assert entry.backoff_until is None
    assert entry.backoff_seconds is None
    assert entry.snapshot is not None
    assert len(entry.snapshot.pools) == 1
    assert entry.snapshot.pools[0].buckets[0].remaining == 0.75


def test_put_snapshot_clears_error_and_backoff(tmp_path: Path) -> None:
    # Arrange
    cache_path = tmp_path / "usage.json"
    cache = UsageCache(cache_path)
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    # Put a rate-limit error first
    cache.put_error("fp1", "rate_limited", "Too many requests", t0)
    entry_err = cache.get("fp1")
    assert entry_err is not None
    assert entry_err.error is not None
    assert entry_err.backoff_until is not None
    assert entry_err.backoff_seconds == 60

    # Act: Successful snapshot update
    t1 = t0 + timedelta(seconds=70)
    snap = _make_snapshot(0.9)
    cache.put_snapshot("fp1", snap, t1)

    # Assert: Cleared error and backoff
    entry_ok = cache.get("fp1")
    assert entry_ok is not None
    assert entry_ok.error is None
    assert entry_ok.backoff_until is None
    assert entry_ok.backoff_seconds is None
    assert entry_ok.snapshot is not None
    assert entry_ok.fetched_at == t1


def test_put_error_rate_limited_backoff_doubling(tmp_path: Path) -> None:
    # Arrange
    cache_path = tmp_path / "usage.json"
    cache = UsageCache(cache_path)
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    # First 429 error
    cache.put_error("fp1", "rate_limited", "HTTP 429", t0)
    e1 = cache.get("fp1")
    assert e1 is not None
    assert e1.backoff_seconds == 60
    assert e1.backoff_until == t0 + timedelta(seconds=60)
    assert e1.error is not None
    assert "rate-limited, retrying after" in e1.error["message"]

    # Second 429 error after first backoff expires
    t1 = t0 + timedelta(seconds=65)
    cache.put_error("fp1", "rate_limited", "HTTP 429", t1)
    e2 = cache.get("fp1")
    assert e2 is not None
    assert e2.backoff_seconds == 120
    assert e2.backoff_until == t1 + timedelta(seconds=120)


def test_put_error_preserves_older_snapshot(tmp_path: Path) -> None:
    # Arrange
    cache_path = tmp_path / "usage.json"
    cache = UsageCache(cache_path)
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    snap = _make_snapshot(0.55)
    cache.put_snapshot("fp1", snap, t0)

    # Act: Network error occurs later
    t1 = t0 + timedelta(minutes=5)
    cache.put_error("fp1", "network_error", "Connection timeout", t1)

    # Assert: Older snapshot is preserved and fetched_at stays t0
    entry = cache.get("fp1")
    assert entry is not None
    assert entry.snapshot is not None
    assert entry.snapshot.pools[0].buckets[0].remaining == 0.55
    assert entry.fetched_at == t0
    assert entry.error is not None
    assert entry.error["kind"] == "network_error"
    assert entry.error["message"] == "Connection timeout"


def test_prune_removes_untracked_fingerprints(tmp_path: Path) -> None:
    # Arrange
    cache_path = tmp_path / "usage.json"
    cache = UsageCache(cache_path)
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    cache.put_snapshot("fp_keep1", _make_snapshot(0.8), now)
    cache.put_snapshot("fp_keep2", _make_snapshot(0.7), now)
    cache.put_snapshot("fp_remove", _make_snapshot(0.6), now)

    # Act
    cache.prune(["fp_keep1", "fp_keep2"])

    # Assert
    assert cache.get("fp_keep1") is not None
    assert cache.get("fp_keep2") is not None
    assert cache.get("fp_remove") is None

    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "fp_remove" not in raw["accounts"]


def test_lock_contention_skips_write_without_error(tmp_path: Path) -> None:
    # Arrange
    cache_path = tmp_path / "usage.json"
    lock_path = tmp_path / "usage.lock"
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    # Hold the lock from an external process/file descriptor
    with FileLock(lock_path, timeout=5.0):
        # Create cache with tiny timeout
        cache = UsageCache(cache_path, lock_timeout=0.05)

        # Act: Attempt to write snapshot while locked
        cache.put_snapshot("fp_contended", _make_snapshot(0.5), now)

        # Attempt to write error while locked
        cache.put_error("fp_contended", "rate_limited", "429", now)

        # Attempt to prune while locked
        cache.prune(["fp_contended"])

    # Assert: File was not created / no exception raised
    assert not cache_path.exists()
