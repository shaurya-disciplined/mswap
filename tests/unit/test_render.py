"""Unit tests for UI rendering and quota formatting."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from mswap.ui.render import print_quota, reset_text


def test_reset_text_under_20h() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    tz = timezone(timedelta(hours=5, minutes=30))
    # 4 hours 25 minutes in the future
    iso = "2026-10-02T16:25:00Z"
    result = reset_text(iso, now, tz=tz)
    assert result == "resets 21:55 (4h 25m)"


def test_reset_text_over_20h() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    tz = timezone(timedelta(hours=5, minutes=30))
    iso = "2026-10-08T04:59:12Z"
    result = reset_text(iso, now, tz=tz)
    assert result == "resets Thu 08 Oct 10:29 (5d 16h)"


def test_reset_text_invalid_or_none() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    assert reset_text(None, now) == ""
    assert reset_text("not-a-valid-timestamp", now) == ""


def test_print_quota_exact_lines() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    tz = timezone(timedelta(hours=5, minutes=30))

    fixture_path = (
        Path(__file__).resolve().parent.parent / "fixtures" / "api" / "quota_summary.json"
    )
    quota_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    groups = quota_data.get("groups", [])

    lines = print_quota(groups, now, tz=tz)

    expected = [
        "     Gemini        5h    ━━━━━━━━━━━━ 100% left  ",
        "                   week  ━━━━━━━━━━━─  99% left  resets Thu 08 Oct 10:29 (5d 16h)",
        "     Claude & GPT  5h    ━━━━━━━━━━━━ 100% left  ",
        "                   week  ━━━━━━━━━━━━ 100% left  ",
    ]

    assert lines == expected
