"""Unit tests for UI rendering and quota formatting."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from mswap.core.models import Account, Bucket, Pool, Quarantine, QuotaSnapshot
from mswap.core.usage_cache import CacheEntry
from mswap.ui.render import AccountRow, print_quota, render_list, reset_text
from mswap.ui.theme import Theme


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


def test_no_block_glyphs_in_src() -> None:
    """Verify that neither █ nor ░ exists anywhere in src/."""
    src_dir = Path(__file__).resolve().parent.parent.parent / "src"
    banned = ["█", "░"]
    violations: list[str] = []
    for file_path in src_dir.rglob("*.py"):
        content = file_path.read_text(encoding="utf-8")
        for line_idx, line in enumerate(content.splitlines(), start=1):
            for glyph in banned:
                if glyph in line:
                    violations.append(f"{file_path}:{line_idx}: contains banned glyph '{glyph}'")

    assert not violations, "Banned block glyphs found in src/:\n" + "\n".join(violations)


def test_render_list_width_boundaries() -> None:
    """Test that width < 70 drops the bar column and width >= 70 retains it."""
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    acc = Account(slot=1, email="test@example.com", fp="fp1", added_at=now, updated_at=now)
    snap = QuotaSnapshot(
        fetched_at=now,
        pools=(
            Pool(
                key="gemini",
                name="Gemini",
                buckets=(Bucket(window="5h", remaining=0.80, reset_at=now + timedelta(hours=1)),),
            ),
        ),
    )
    entry = CacheEntry(
        fetched_at=now, snapshot=snap, error=None, backoff_until=None, backoff_seconds=None
    )
    row = AccountRow(account=acc, active=False, entry=entry, stale=False)
    theme = Theme(color=False, ascii=False)

    # Width 69 (< 70): No bar column
    lines_69 = render_list([row], now=now, theme=theme, width=69, tz=UTC)
    pool_line_69 = lines_69[3]
    assert "━" not in pool_line_69
    assert "─" not in pool_line_69
    assert "Gemini        5h     80% left" in pool_line_69

    # Width 70 (>= 70): Bar column present
    lines_70 = render_list([row], now=now, theme=theme, width=70, tz=UTC)
    pool_line_70 = lines_70[3]
    assert "━" in pool_line_70
    assert "Gemini        5h    ━━━━━━━━━───  80% left" in pool_line_70


def test_render_list_plan_label_mappings() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    theme = Theme(color=False, ascii=False)

    plans_to_check = [
        ("g1-pro-tier", "[pro]"),
        ("g1-ultra-tier", "[ultra]"),
        ("free-tier", "[free]"),
        ("standard-tier", "[standard]"),
        ("custom-enterprise-tier", "[custom-enterprise-tier]"),
    ]

    for plan_id, expected_label in plans_to_check:
        acc = Account(
            slot=1,
            email="test@example.com",
            fp="fp1",
            added_at=now,
            updated_at=now,
            plan=plan_id,
        )
        row = AccountRow(account=acc, active=False, entry=None, stale=False)
        lines = render_list([row], now=now, theme=theme, width=80, tz=UTC)
        assert f"test@example.com  {expected_label}" in lines[2]

    # None plan: omitted
    acc_none = Account(
        slot=1, email="test@example.com", fp="fp1", added_at=now, updated_at=now, plan=None
    )
    row_none = AccountRow(account=acc_none, active=False, entry=None, stale=False)
    lines_none = render_list([row_none], now=now, theme=theme, width=80, tz=UTC)
    assert "[" not in lines_none[2]


def test_render_list_window_mappings() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    acc = Account(slot=1, email="test@example.com", fp="fp1", added_at=now, updated_at=now)
    snap = QuotaSnapshot(
        fetched_at=now,
        pools=(
            Pool(
                key="gemini",
                name="Gemini",
                buckets=(
                    Bucket(window="5h", remaining=1.0, reset_at=None),
                    Bucket(window="weekly", remaining=1.0, reset_at=None),
                    Bucket(window="monthly", remaining=1.0, reset_at=None),
                ),
            ),
        ),
    )
    entry = CacheEntry(
        fetched_at=now, snapshot=snap, error=None, backoff_until=None, backoff_seconds=None
    )
    row = AccountRow(account=acc, active=False, entry=entry, stale=False)
    theme = Theme(color=False, ascii=False)

    lines = render_list([row], now=now, theme=theme, width=80, tz=UTC)
    assert " 5h    " in lines[3]
    assert " week  " in lines[4]
    assert " month " in lines[5]


def test_render_list_bar_color_thresholds() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    theme = Theme(color=True, ascii=False)

    def check_color(rem: float, expected_code: str) -> None:
        acc = Account(slot=1, email="test@example.com", fp="fp1", added_at=now, updated_at=now)
        bucket = Bucket(window="5h", remaining=rem, reset_at=None)
        snap = QuotaSnapshot(
            fetched_at=now,
            pools=(Pool(key="p", name="P", buckets=(bucket,)),),
        )
        entry = CacheEntry(
            fetched_at=now, snapshot=snap, error=None, backoff_until=None, backoff_seconds=None
        )
        row = AccountRow(account=acc, active=False, entry=entry, stale=False)
        lines = render_list([row], now=now, theme=theme, width=80, tz=UTC)
        assert f"\033[{expected_code}m" in lines[3]

    # > 0.5 -> green (32)
    check_color(0.51, "32")
    # > 0.15 and <= 0.50 -> yellow (33)
    check_color(0.50, "33")
    check_color(0.16, "33")
    # <= 0.15 -> red (31)
    check_color(0.15, "31")
    check_color(0.10, "31")


def test_render_list_markers() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    theme = Theme(color=False, ascii=False)

    # Active + alias + disabled + quarantined
    acc = Account(
        slot=1,
        email="test@example.com",
        fp="fp1",
        added_at=now,
        updated_at=now,
        alias="work",
        disabled=True,
        quarantined=Quarantine(reason="invalid_grant", at=now),
    )
    entry = CacheEntry(
        fetched_at=now - timedelta(seconds=45),
        snapshot=None,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )
    row = AccountRow(account=acc, active=True, entry=entry, stale=True)
    lines = render_list([row], now=now, theme=theme, width=80, tz=UTC)
    line1 = lines[2]
    assert "test@example.com (active)  · alias work · disabled · quarantined · 45s ago" in line1


def test_render_list_empty_rows() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    theme = Theme(color=False, ascii=False)
    lines = render_list([], now=now, theme=theme, width=80, tz=UTC)
    assert lines == ["mswap · agy accounts"]
