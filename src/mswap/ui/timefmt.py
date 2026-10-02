"""Time and reset formatting helpers for CLI display."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

from mswap.agy.tokens import parse_go_time


def reset_text(
    reset_at: str | datetime | None,
    now: datetime,
    tz: tzinfo | None = None,
) -> str:
    """Format quota reset time relative to now in local time per §A18.

    Examples:
        resets 05:51 (4h 25m) when under 20h
        resets Thu 08 Oct 10:29 (6d 9h) when >= 20h
    """
    if not reset_at:
        return ""
    try:
        t = reset_at if isinstance(reset_at, datetime) else parse_go_time(reset_at)
    except Exception:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    t_utc = t.astimezone(UTC)
    now_utc = now_utc.astimezone(UTC)
    left = t_utc - now_utc

    local = t.astimezone(tz)
    hours = max(0, int(left.total_seconds() // 3600))
    mins = max(0, int(left.total_seconds() % 3600 // 60))
    when = (
        local.strftime("%H:%M") if left < timedelta(hours=20) else local.strftime("%a %d %b %H:%M")
    )
    span = f"{hours // 24}d {hours % 24}h" if hours >= 24 else f"{hours}h {mins}m"
    return f"resets {when} ({span})"


def age_text(seconds: int) -> str:
    """Format elapsed seconds as a compact age string ('45s', '6m', '2h', '3d') per §A18."""
    s = max(0, seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


# Backward compatibility alias
format_age = age_text
