"""Unit tests for clock utilities."""

from __future__ import annotations

from datetime import UTC, datetime

from mswap.util.clock import FrozenClock, SystemClock


def test_frozen_clock_now_is_aware_utc() -> None:
    # Arrange
    start = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    clock = FrozenClock(start)

    # Act
    current = clock.now()

    # Assert
    assert current == start
    assert current.tzinfo is not None
    assert current.tzinfo == UTC


def test_frozen_clock_advance() -> None:
    # Arrange
    start = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    clock = FrozenClock(start)

    # Act
    clock.advance(90)
    advanced = clock.now()

    # Assert
    expected = datetime(2026, 10, 2, 12, 1, 30, tzinfo=UTC)
    assert advanced == expected


def test_frozen_clock_naive_datetime() -> None:
    # Arrange
    naive = datetime(2026, 10, 2, 12, 0)
    clock = FrozenClock(naive)

    # Act
    current = clock.now()

    # Assert
    assert current.tzinfo == UTC
    assert current.year == 2026


def test_system_clock_now_is_aware_utc() -> None:
    # Arrange
    clock = SystemClock()

    # Act
    now = clock.now()

    # Assert
    assert now.tzinfo is not None
    assert now.tzinfo == UTC
