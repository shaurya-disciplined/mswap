"""Unit tests for ui/timefmt.py time and reset formatting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from mswap.ui.timefmt import age_text, format_age, reset_text


def test_reset_text_under_20h() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    tz = timezone(timedelta(hours=5, minutes=30))
    iso = "2026-10-02T16:25:00Z"
    assert reset_text(iso, now, tz=tz) == "resets 21:55 (4h 25m)"


def test_reset_text_over_20h() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    tz = timezone(timedelta(hours=5, minutes=30))
    iso = "2026-10-08T04:59:12Z"
    assert reset_text(iso, now, tz=tz) == "resets Thu 08 Oct 10:29 (5d 16h)"


def test_reset_text_datetime_instance() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    dt = datetime(2026, 10, 2, 15, 30, 0, tzinfo=UTC)
    assert reset_text(dt, now, tz=UTC) == "resets 15:30 (3h 30m)"


def test_reset_text_invalid_or_none() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    assert reset_text(None, now) == ""
    assert reset_text("", now) == ""
    assert reset_text("invalid-date", now) == ""


def test_age_text_boundaries() -> None:
    assert age_text(0) == "0s"
    assert age_text(45) == "45s"
    assert age_text(59) == "59s"
    assert age_text(60) == "1m"
    assert age_text(359) == "5m"
    assert age_text(3600) == "1h"
    assert age_text(7200) == "2h"
    assert age_text(86400) == "1d"
    assert age_text(172800) == "2d"


def test_format_age_alias() -> None:
    assert format_age(120) == "2m"
