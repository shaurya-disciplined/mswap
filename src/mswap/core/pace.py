"""Pace forecasting for quota buckets.

Pure calculations of consumption pace, projected exhaustion, and pace indicators
for weekly quota windows. Must never perform I/O or access external resources.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mswap.core.models import Bucket

WINDOW: dict[str, timedelta] = {
    "weekly": timedelta(days=7),
    "5h": timedelta(hours=5),
}


@dataclass(frozen=True, slots=True)
class Pace:
    """Quota pace forecast for a consumption window."""

    expected_used: float
    actual_used: float
    ahead: bool
    exhaust_at: datetime | None
    lasts_to_reset: bool

    def to_json(self) -> dict[str, Any]:
        """Serialize Pace to a JSON-compatible dictionary."""
        return {
            "expected_used": self.expected_used,
            "actual_used": self.actual_used,
            "ahead": self.ahead,
            "exhaust_at": self.exhaust_at.isoformat() if self.exhaust_at is not None else None,
            "lasts_to_reset": self.lasts_to_reset,
        }


def pace(bucket: Bucket, now: datetime) -> Pace | None:
    """Calculate quota pace and projection for weekly windows.

    Returns None for non-weekly windows, missing reset_at, or within the first day.
    """
    if bucket.window != "weekly" or bucket.reset_at is None:
        return None

    reset_at = bucket.reset_at
    if reset_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=reset_at.tzinfo)
    elif reset_at.tzinfo is None and now.tzinfo is not None:
        reset_at = reset_at.replace(tzinfo=now.tzinfo)

    window_duration = WINDOW["weekly"]
    start = reset_at - window_duration
    elapsed_seconds = (now - start).total_seconds()
    window_seconds = window_duration.total_seconds()

    elapsed = max(0.0, min(1.0, elapsed_seconds / window_seconds))

    if elapsed < (1.0 / 7.0):
        return None

    expected_used = elapsed
    actual_used = 1.0 - bucket.remaining
    ahead = actual_used > (expected_used + 0.10)

    rate = actual_used / elapsed_seconds

    if rate <= 0:
        exhaust_at = None
        lasts_to_reset = True
    else:
        seconds_to_empty = bucket.remaining / rate
        exhaust_at = now + timedelta(seconds=seconds_to_empty)
        lasts_to_reset = exhaust_at >= reset_at

    return Pace(
        expected_used=expected_used,
        actual_used=actual_used,
        ahead=ahead,
        exhaust_at=exhaust_at,
        lasts_to_reset=lasts_to_reset,
    )
