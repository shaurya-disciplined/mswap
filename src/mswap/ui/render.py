"""Rendering of accounts and quota information for CLI display."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from mswap.agy.tokens import parse_go_time
from mswap.core.models import Pool, QuotaSnapshot
from mswap.ui.theme import _bar, dim

_GROUP_NAMES = {"Gemini Models": "Gemini", "Claude and GPT models": "Claude & GPT"}
_WINDOW_NAMES = {"5h": "5h", "weekly": "week"}


def reset_text(iso: str | datetime | None, now: datetime, *, tz: tzinfo | None = None) -> str:
    """Format reset time relative to now."""
    if not iso:
        return ""
    try:
        t = iso if isinstance(iso, datetime) else parse_go_time(iso)
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


def format_age(seconds: int) -> str:
    """Format elapsed seconds as a compact age string ('45s', '6m', '2h', '3d')."""
    s = max(0, seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def print_quota(
    quota: QuotaSnapshot | Sequence[Pool] | list[dict[str, Any]],
    now: datetime,
    *,
    tz: tzinfo | None = None,
) -> list[str]:
    """Format quota buckets as lines."""
    lines: list[str] = []
    pools: Sequence[Pool] = []
    if isinstance(quota, QuotaSnapshot):
        pools = quota.pools
    elif isinstance(quota, Sequence) and quota and isinstance(quota[0], Pool):
        pools = quota  # type: ignore[assignment]

    if pools:
        for p in pools:
            for i, b in enumerate(p.buckets):
                frac = b.remaining
                label = p.name if i == 0 else ""
                window = _WINDOW_NAMES.get(b.window, b.window)
                reset = reset_text(b.reset_at, now, tz=tz) if frac < 1 else ""
                reset_suffix = dim(reset) if reset else ""
                line_str = (
                    f"     {label:<13} {window:<5} {_bar(frac)} "
                    f"{int(frac * 100):>3}% left  {reset_suffix}"
                )
                lines.append(line_str)
        return lines

    for g in quota:  # type: ignore[union-attr]
        if not isinstance(g, dict):
            continue
        raw_name = g.get("displayName")
        disp_name = str(raw_name) if raw_name is not None else "?"
        name = _GROUP_NAMES.get(disp_name, disp_name)
        buckets = sorted(g.get("buckets", []), key=lambda b: b.get("window") != "5h")
        for i, b in enumerate(buckets):
            frac = float(b.get("remainingFraction", 0))
            label = name if i == 0 else ""
            raw_win = b.get("window")
            win_str = str(raw_win) if raw_win is not None else "?"
            window = _WINDOW_NAMES.get(win_str, win_str)
            reset = reset_text(b.get("resetTime"), now, tz=tz) if frac < 1 else ""
            reset_suffix = dim(reset) if reset else ""
            line_str = (
                f"     {label:<13} {window:<5} {_bar(frac)} "
                f"{int(frac * 100):>3}% left  {reset_suffix}"
            )
            lines.append(line_str)
    return lines
