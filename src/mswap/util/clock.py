"""Clock protocols and implementations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Protocol for time sources."""

    def now(self) -> datetime:
        """Return the current aware UTC datetime."""
        ...


class SystemClock:
    """Real system clock returning aware UTC datetime."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Deterministic frozen clock for testing."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            self._at = at.replace(tzinfo=UTC)
        else:
            self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, seconds: float) -> None:
        self._at += timedelta(seconds=seconds)
