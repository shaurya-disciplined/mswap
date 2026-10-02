"""Unit tests for quota pace calculation and forecasting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from mswap.core.models import Bucket
from mswap.core.pace import Pace, pace


def test_pace_5h_bucket_returns_none() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    b = Bucket(window="5h", remaining=0.50, reset_at=now + timedelta(hours=2))
    assert pace(b, now) is None


def test_pace_missing_reset_at_returns_none() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    b = Bucket(window="weekly", remaining=1.0, reset_at=None)
    assert pace(b, now) is None


def test_pace_other_window_returns_none() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    b = Bucket(window="monthly", remaining=0.50, reset_at=now + timedelta(days=15))
    assert pace(b, now) is None


def test_pace_first_day_cutoff() -> None:
    # 7-day week: start is reset_at - 7d.
    # If elapsed < 1/7 (less than 24 hours into the week), return None.
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    start = reset_at - timedelta(days=7)  # 2026-10-02 12:00:00

    # 23 hours elapsed: < 24 hours
    now_23h = start + timedelta(hours=23)
    b_23h = Bucket(window="weekly", remaining=0.80, reset_at=reset_at)
    assert pace(b_23h, now_23h) is None

    # Exactly 24 hours elapsed: == 1/7
    now_24h = start + timedelta(hours=24)
    b_24h = Bucket(window="weekly", remaining=0.80, reset_at=reset_at)
    p = pace(b_24h, now_24h)
    assert isinstance(p, Pace)
    assert p.expected_used == pytest.approx(1.0 / 7.0)


def test_pace_zero_elapsed_and_negative_clock_skew() -> None:
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    start = reset_at - timedelta(days=7)

    # Zero elapsed time (now == start)
    b = Bucket(window="weekly", remaining=0.80, reset_at=reset_at)
    assert pace(b, start) is None

    # Negative clock skew (now < start)
    now_skew = start - timedelta(hours=2)
    assert pace(b, now_skew) is None


def test_pace_window_end_edges() -> None:
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)

    # At window end (now == reset_at)
    b = Bucket(window="weekly", remaining=0.10, reset_at=reset_at)
    p = pace(b, reset_at)
    assert p is not None
    assert p.expected_used == 1.0

    # Past window end (now > reset_at, clamped to 1.0)
    now_past = reset_at + timedelta(hours=3)
    p_past = pace(b, now_past)
    assert p_past is not None
    assert p_past.expected_used == 1.0


def test_pace_exactly_on_pace_not_ahead() -> None:
    # elapsed = 0.5 (3.5 days into week), used = 0.5 (remaining = 0.5)
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    start = reset_at - timedelta(days=7)
    now = start + timedelta(days=3.5)
    b = Bucket(window="weekly", remaining=0.5, reset_at=reset_at)

    p = pace(b, now)
    assert p is not None
    assert p.expected_used == pytest.approx(0.5)
    assert p.actual_used == pytest.approx(0.5)
    assert p.ahead is False


def test_pace_within_tolerance_not_ahead() -> None:
    # elapsed = 0.5, used = 0.59 (remaining = 0.41) -> within 10-point tolerance (0.59 <= 0.60)
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    start = reset_at - timedelta(days=7)
    now = start + timedelta(days=3.5)
    b_59 = Bucket(window="weekly", remaining=0.41, reset_at=reset_at)

    p_59 = pace(b_59, now)
    assert p_59 is not None
    assert p_59.ahead is False

    # exactly on the 10-point tolerance: used = 0.60 (remaining = 0.40) -> not strictly greater
    b_60 = Bucket(window="weekly", remaining=0.40, reset_at=reset_at)
    p_60 = pace(b_60, now)
    assert p_60 is not None
    assert p_60.ahead is False


def test_pace_ahead_over_tolerance() -> None:
    # elapsed = 0.5, used = 0.65 (remaining = 0.35) -> 0.65 > 0.5 + 0.10 -> ahead True
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    start = reset_at - timedelta(days=7)
    now = start + timedelta(days=3.5)
    b = Bucket(window="weekly", remaining=0.35, reset_at=reset_at)

    p = pace(b, now)
    assert p is not None
    assert p.expected_used == pytest.approx(0.5)
    assert p.actual_used == pytest.approx(0.65)
    assert p.ahead is True


def test_pace_unused_zero_actual_used_lasts_to_reset() -> None:
    # bucket with 0 actual used: rate <= 0, exhaust_at None, lasts_to_reset True
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    start = reset_at - timedelta(days=7)
    now = start + timedelta(days=2)
    b = Bucket(window="weekly", remaining=0.5, reset_at=reset_at)
    object.__setattr__(b, "remaining", 1.0)
    p = pace(b, now)
    assert p is not None
    assert p.actual_used == 0.0
    assert p.ahead is False
    assert p.exhaust_at is None
    assert p.lasts_to_reset is True


def test_pace_already_exhausted() -> None:
    # remaining = 0.0 mid-week
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    start = reset_at - timedelta(days=7)
    now = start + timedelta(days=3.5)
    b = Bucket(window="weekly", remaining=0.0, reset_at=reset_at)

    p = pace(b, now)
    assert p is not None
    assert p.actual_used == 1.0
    assert p.ahead is True
    assert p.exhaust_at == now
    assert p.lasts_to_reset is False


def test_pace_projection_math_fixed_numbers() -> None:
    # start T, now T+2d, used 0.4 (remaining 0.6)
    # rate = 0.4 / 2d = 0.2/day
    # remaining 0.6 / 0.2/day = 3 days
    # exhaust T+5d < reset T+7d -> lasts False
    start = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    reset_at = start + timedelta(days=7)
    now = start + timedelta(days=2)
    b = Bucket(window="weekly", remaining=0.6, reset_at=reset_at)

    p = pace(b, now)
    assert p is not None
    assert p.expected_used == pytest.approx(2.0 / 7.0)
    assert p.actual_used == pytest.approx(0.4)
    assert p.ahead is True  # 0.4 > 2/7 + 0.10 ≈ 0.3857
    assert p.exhaust_at == start + timedelta(days=5)
    assert p.lasts_to_reset is False


def test_pace_projection_math_lasts_to_reset() -> None:
    # start T, now T+2d, used 0.1 (remaining 0.9)
    # rate = 0.1 / 2d = 0.05/day
    # remaining 0.9 / 0.05/day = 18 days
    # exhaust T+20d >= reset T+7d -> lasts True
    start = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    reset_at = start + timedelta(days=7)
    now = start + timedelta(days=2)
    b = Bucket(window="weekly", remaining=0.9, reset_at=reset_at)

    p = pace(b, now)
    assert p is not None
    assert p.ahead is False
    assert p.exhaust_at == start + timedelta(days=20)
    assert p.lasts_to_reset is True


def test_pace_to_json_serialization() -> None:
    start = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    reset_at = start + timedelta(days=7)
    now = start + timedelta(days=2)
    b = Bucket(window="weekly", remaining=0.6, reset_at=reset_at)

    p = pace(b, now)
    assert p is not None
    d = p.to_json()
    assert d["ahead"] is True
    assert d["lasts_to_reset"] is False
    assert d["exhaust_at"] == (start + timedelta(days=5)).isoformat()
    assert d["expected_used"] == pytest.approx(2.0 / 7.0)
    assert d["actual_used"] == pytest.approx(0.4)


def test_pace_tz_awareness_compatibility() -> None:
    # reset_at with timezone, now naive
    reset_at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    now_naive = datetime(2026, 10, 5, 12, 0, 0)
    b = Bucket(window="weekly", remaining=0.5, reset_at=reset_at)
    p = pace(b, now_naive)
    assert p is not None
    assert p.expected_used == pytest.approx(3.0 / 7.0)

    # reset_at naive, now with timezone
    reset_at_naive = datetime(2026, 10, 9, 12, 0, 0)
    now_aware = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
    b_naive = Bucket(window="weekly", remaining=0.5, reset_at=reset_at_naive)
    p_naive = pace(b_naive, now_aware)
    assert p_naive is not None
    assert p_naive.expected_used == pytest.approx(3.0 / 7.0)

    # reset_at in non-UTC timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    reset_at_ist = datetime(2026, 10, 9, 17, 30, 0, tzinfo=ist)
    now_utc = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
    b_ist = Bucket(window="weekly", remaining=0.5, reset_at=reset_at_ist)
    p_ist = pace(b_ist, now_utc)
    assert p_ist is not None
    assert p_ist.expected_used == pytest.approx(3.0 / 7.0)
