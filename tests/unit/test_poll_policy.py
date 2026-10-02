"""Unit tests for core/poll_policy.py TTL calculations, staleness, and backoff sequence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mswap.core.models import Bucket, Pool, QuotaSnapshot
from mswap.core.poll_policy import (
    ACTIVE_TTL,
    BACKOFF_BASE,
    BACKOFF_CAP,
    EXHAUSTED_TTL,
    IDLE_TTL,
    NEAR_LIMIT_TTL,
    is_stale,
    next_backoff,
    ttl,
)
from mswap.core.usage_cache import CacheEntry


def _make_snapshot(remaining_values: list[float], now: datetime | None = None) -> QuotaSnapshot:
    dt = now or datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    buckets = tuple(
        Bucket(window=f"w{i}", remaining=r, reset_at=dt + timedelta(hours=i + 1))
        for i, r in enumerate(remaining_values)
    )
    pool = Pool(key="gemini", name="Gemini", buckets=buckets)
    return QuotaSnapshot(fetched_at=dt, pools=(pool,))


def test_ttl_none() -> None:
    # Arrange & Act & Assert
    assert ttl(None, active=False, near_limit=False) == 0
    assert ttl(None, active=True, near_limit=False) == 0


def test_ttl_exhausted_vs_active_precedence() -> None:
    # Arrange: All buckets are 0.0
    snap = _make_snapshot([0.0, 0.0])

    # Act & Assert: Exhausted wins over active
    assert ttl(snap, active=True, near_limit=False) == EXHAUSTED_TTL
    assert ttl(snap, active=False, near_limit=False) == EXHAUSTED_TTL
    assert EXHAUSTED_TTL == 600


def test_ttl_active_account() -> None:
    # Arrange: Healthy buckets, active=True
    snap = _make_snapshot([0.8, 0.9])

    # Act & Assert
    assert ttl(snap, active=True, near_limit=False) == ACTIVE_TTL
    assert ACTIVE_TTL == 60

    # Active wins over near-limit bucket
    snap_low = _make_snapshot([0.15, 0.9])
    assert ttl(snap_low, active=True, near_limit=False) == ACTIVE_TTL


def test_ttl_near_limit_boundary() -> None:
    # Arrange
    # Not near limit
    snap_exact = _make_snapshot([0.20, 0.9])
    assert ttl(snap_exact, active=False, near_limit=False) == IDLE_TTL
    assert IDLE_TTL == 300

    # Near limit
    snap_near = _make_snapshot([0.199, 0.9])
    assert ttl(snap_near, active=False, near_limit=True) == NEAR_LIMIT_TTL
    assert NEAR_LIMIT_TTL == 90


def test_ttl_idle_healthy() -> None:
    # Arrange: All buckets >= 0.20, active=False
    snap = _make_snapshot([0.5, 0.8])

    # Act & Assert
    assert ttl(snap, active=False, near_limit=False) == IDLE_TTL


def test_ttl_empty_pools_or_buckets() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    snap_empty_pools = QuotaSnapshot(fetched_at=now, pools=())
    assert ttl(snap_empty_pools, active=True, near_limit=False) == ACTIVE_TTL
    assert ttl(snap_empty_pools, active=False, near_limit=False) == IDLE_TTL

    snap_empty_buckets = QuotaSnapshot(
        fetched_at=now, pools=(Pool(key="gemini", name="Gemini", buckets=()),)
    )
    assert ttl(snap_empty_buckets, active=True, near_limit=False) == ACTIVE_TTL
    assert ttl(snap_empty_buckets, active=False, near_limit=False) == IDLE_TTL


def test_is_stale_none_entry() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    assert is_stale(None, now, active=False, near_limit=False) is True
    assert is_stale(None, now, active=True, near_limit=False) is True


def test_is_stale_active_boundaries() -> None:
    # ACTIVE_TTL is 60s
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    snap = _make_snapshot([0.8, 0.9], now=t0)
    entry = CacheEntry(
        fetched_at=t0,
        snapshot=snap,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )

    # 59 seconds: TTL - 1s -> not stale
    assert is_stale(entry, t0 + timedelta(seconds=59), active=True, near_limit=False) is False
    # 60 seconds: exactly TTL -> stale
    assert is_stale(entry, t0 + timedelta(seconds=60), active=True, near_limit=False) is True
    # 61 seconds: TTL + 1s -> stale
    assert is_stale(entry, t0 + timedelta(seconds=61), active=True, near_limit=False) is True


def test_is_stale_near_limit_boundaries() -> None:
    # NEAR_LIMIT_TTL is 90s
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    snap = _make_snapshot([0.15, 0.9], now=t0)
    entry = CacheEntry(
        fetched_at=t0,
        snapshot=snap,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )

    assert is_stale(entry, t0 + timedelta(seconds=89), active=False, near_limit=True) is False
    assert is_stale(entry, t0 + timedelta(seconds=90), active=False, near_limit=True) is True
    assert is_stale(entry, t0 + timedelta(seconds=91), active=False, near_limit=True) is True


def test_is_stale_idle_boundaries() -> None:
    # IDLE_TTL is 300s
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    snap = _make_snapshot([0.8, 0.9], now=t0)
    entry = CacheEntry(
        fetched_at=t0,
        snapshot=snap,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )

    assert is_stale(entry, t0 + timedelta(seconds=299), active=False, near_limit=False) is False
    assert is_stale(entry, t0 + timedelta(seconds=300), active=False, near_limit=False) is True
    assert is_stale(entry, t0 + timedelta(seconds=301), active=False, near_limit=False) is True


def test_is_stale_exhausted_boundaries() -> None:
    # EXHAUSTED_TTL is 600s
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    snap = _make_snapshot([0.0, 0.0], now=t0)
    entry = CacheEntry(
        fetched_at=t0,
        snapshot=snap,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )

    assert is_stale(entry, t0 + timedelta(seconds=599), active=False, near_limit=False) is False
    assert is_stale(entry, t0 + timedelta(seconds=600), active=False, near_limit=False) is True
    assert is_stale(entry, t0 + timedelta(seconds=601), active=False, near_limit=False) is True


def test_is_stale_respects_backoff() -> None:
    # Even if data is 1 hour old, if now < backoff_until -> not stale (respect backoff)
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    backoff_end = t0 + timedelta(minutes=10)
    snap = _make_snapshot([0.8], now=t0)
    entry = CacheEntry(
        fetched_at=t0,
        snapshot=snap,
        error={"kind": "rate_limited", "message": "rate-limited, retrying after 12:10"},
        backoff_until=backoff_end,
        backoff_seconds=600,
    )

    # During backoff window (9 min after t0, well past 60s active TTL)
    assert is_stale(entry, t0 + timedelta(minutes=9), active=True, near_limit=False) is False
    # At backoff expiry -> checks TTL (which has elapsed) -> True
    assert is_stale(entry, backoff_end, active=True, near_limit=False) is True
    # After backoff expiry -> True
    assert (
        is_stale(entry, backoff_end + timedelta(seconds=1), active=True, near_limit=False) is True
    )


def test_next_backoff_doubling_and_cap() -> None:
    # Backoff sequence: 60, 120, 240, 480, 960, 1800, 1800
    b0 = next_backoff(None)
    assert b0 == 60
    assert b0 == BACKOFF_BASE

    b1 = next_backoff(b0)
    assert b1 == 120

    b2 = next_backoff(b1)
    assert b2 == 240

    b3 = next_backoff(b2)
    assert b3 == 480

    b4 = next_backoff(b3)
    assert b4 == 960

    b5 = next_backoff(b4)
    assert b5 == 1800
    assert b5 == BACKOFF_CAP

    b6 = next_backoff(b5)
    assert b6 == 1800

    # Over cap clamps to cap
    assert next_backoff(5000) == 1800
    assert next_backoff(-10) == 60
    assert next_backoff(0) == 60
