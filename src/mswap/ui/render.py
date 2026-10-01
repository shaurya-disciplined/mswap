"""Rendering of accounts and quota information for CLI display."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from mswap.agy.tokens import parse_go_time
from mswap.ui.theme import _bar, dim

_GROUP_NAMES = {"Gemini Models": "Gemini", "Claude and GPT models": "Claude & GPT"}
_WINDOW_NAMES = {"5h": "5h", "weekly": "week"}


def reset_text(iso: str | None, now: datetime, *, tz: tzinfo | None = None) -> str:
    """Format reset time relative to now."""
    t = parse_go_time(iso)
    if not t:
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


def print_quota(
    groups: list[dict[str, Any]],
    now: datetime,
    *,
    tz: tzinfo | None = None,
) -> list[str]:
    """Format quota buckets as lines."""
    lines: list[str] = []
    for g in groups:
        name = _GROUP_NAMES.get(g.get("displayName", ""), g.get("displayName", "?"))
        buckets = sorted(g.get("buckets", []), key=lambda b: b.get("window") != "5h")
        for i, b in enumerate(buckets):
            frac = float(b.get("remainingFraction", 0))
            label = name if i == 0 else ""
            window = _WINDOW_NAMES.get(b.get("window", ""), b.get("window", "?"))
            reset = reset_text(b.get("resetTime"), now, tz=tz) if frac < 1 else ""
            reset_suffix = dim(reset) if reset else ""
            line_str = (
                f"     {label:<13} {window:<5} {_bar(frac)} "
                f"{int(frac * 100):>3}% left  {reset_suffix}"
            )
            lines.append(line_str)
    return lines
