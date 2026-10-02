"""Pure adaptive poll policy and TTL calculations for quota checking."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mswap.core.models import QuotaSnapshot

if TYPE_CHECKING:
    from mswap.core.usage_cache import CacheEntry

ACTIVE_TTL: int = 60
NEAR_LIMIT_TTL: int = 90
IDLE_TTL: int = 300
EXHAUSTED_TTL: int = 600
BACKOFF_BASE: int = 60
BACKOFF_CAP: int = 1800
NEAR_LIMIT: float = 0.20


def _normalize_dt(dt: datetime) -> datetime:
    """Ensure datetime is aware and normalized to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def ttl(snapshot: QuotaSnapshot | None, *, active: bool) -> int:
    """Compute TTL in seconds for a quota snapshot based on exhaustion, activity, and quota levels.

    Precedence:
    1. None -> 0
    2. Every bucket of every pool == 0 -> EXHAUSTED_TTL
    3. active -> ACTIVE_TTL
    4. Any bucket < NEAR_LIMIT -> NEAR_LIMIT_TTL
    5. Else -> IDLE_TTL
    """
    if snapshot is None:
        return 0

    buckets = [b for p in snapshot.pools for b in p.buckets]
    if buckets and all(b.remaining <= 0.0 for b in buckets):
        return EXHAUSTED_TTL

    if active:
        return ACTIVE_TTL

    if any(b.remaining < NEAR_LIMIT for b in buckets):
        return NEAR_LIMIT_TTL

    return IDLE_TTL


def is_stale(entry: CacheEntry | None, now: datetime, *, active: bool) -> bool:
    """Determine whether a cache entry is stale and eligible for network refresh.

    Returns False if entry is within an active rate-limit backoff window.
    Otherwise returns True if now - fetched_at >= ttl.
    """
    if entry is None:
        return True

    now_utc = _normalize_dt(now)

    if entry.backoff_until is not None:
        backoff_utc = _normalize_dt(entry.backoff_until)
        if now_utc < backoff_utc:
            return False

    fetched_utc = _normalize_dt(entry.fetched_at)
    elapsed = (now_utc - fetched_utc).total_seconds()
    required_ttl = ttl(entry.snapshot, active=active)
    return elapsed >= required_ttl


def next_backoff(previous_seconds: int | None) -> int:
    """Compute the next backoff interval in seconds doubling from base up to cap."""
    if previous_seconds is None or previous_seconds <= 0:
        return BACKOFF_BASE
    return min(previous_seconds * 2, BACKOFF_CAP)
