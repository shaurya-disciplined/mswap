"""Rendering of accounts and quota information for CLI display."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

from mswap.core.models import Account, Pool, QuotaSnapshot
from mswap.core.usage_cache import CacheEntry
from mswap.ui.theme import Theme, _bar, dim
from mswap.ui.timefmt import age_text, reset_text

_GROUP_NAMES = {"Gemini Models": "Gemini", "Claude and GPT models": "Claude & GPT"}
_WINDOW_NAMES = {"5h": "5h", "weekly": "week"}

PLAN_MAP: dict[str, str] = {
    "g1-pro-tier": "pro",
    "g1-ultra-tier": "ultra",
    "free-tier": "free",
    "standard-tier": "standard",
}


def _format_window(window: str) -> str:
    if window == "5h":
        return "5h"
    if window == "weekly":
        return "week"
    return window[:5]


@dataclass
class AccountRow:
    """Row data for rendering a single account in mswap list."""

    account: Account
    active: bool
    entry: CacheEntry | None
    stale: bool


def render_list(
    rows: list[AccountRow],
    *,
    now: datetime,
    theme: Theme,
    width: int,
    tz: tzinfo | None = None,
) -> list[str]:
    """Pure rendering of accounts and quota information for CLI list output."""
    header = theme.bold("mswap") + theme.dim(" · agy accounts")
    if not rows:
        return [header]

    lines: list[str] = [header, ""]
    arrow = "->" if theme.ascii else "→"

    for idx, row in enumerate(rows):
        if idx > 0:
            lines.append("")

        acc = row.account
        mark = theme.accent(theme.active) if row.active else " "
        slot_str = theme.bold(str(acc.slot))
        line1 = f" {mark} {slot_str}  {acc.email}"

        if row.active:
            line1 += f" {theme.ok('(active)')}"

        if acc.plan:
            plan_label = PLAN_MAP.get(acc.plan, acc.plan)
            line1 += f"  [{plan_label}]"

        markers: list[str] = []
        if acc.alias:
            markers.append(f"alias {acc.alias}")
        if acc.disabled:
            markers.append("disabled")
        if acc.quarantined is not None:
            markers.append("quarantined")

        if markers:
            line1 += f"  · {' · '.join(markers)}"

        if row.stale and row.entry is not None:
            elapsed = max(0, int((now - row.entry.fetched_at).total_seconds()))
            age_str = f"{age_text(elapsed)} ago"
            if markers:
                line1 += theme.dim(f" · {age_str}")
            else:
                line1 += f"  {theme.dim(f'· {age_str}')}"

        lines.append(line1)

        snap = row.entry.snapshot if row.entry else None

        if snap is not None and snap.pools:
            if snap.source == "models":
                lines.append(theme.dim("  (per-model view: summary unavailable)"))

            for pool in snap.pools:
                for b_idx, b in enumerate(pool.buckets):
                    label = pool.name if b_idx == 0 else ""
                    window = _format_window(b.window)
                    rem = b.remaining
                    pct = int(rem * 100)

                    if width >= 70:
                        filled = int(rem * 12)
                        empty = 12 - filled
                        filled_chars = theme.bar_full * filled
                        empty_chars = theme.bar_empty * empty

                        if filled > 0:
                            if rem > 0.5:
                                col_filled = theme.green(filled_chars)
                            elif rem > 0.15:
                                col_filled = theme.yellow(filled_chars)
                            else:
                                col_filled = theme.red(filled_chars)
                        else:
                            col_filled = ""

                        dim_empty = theme.dim(empty_chars) if empty > 0 else ""
                        bar_col = f"{col_filled}{dim_empty} "
                    else:
                        bar_col = ""

                    line_str = f"     {label:<13} {window:<5} {bar_col}{pct:>3}% left"

                    if rem < 1.0:
                        reset_val = reset_text(b.reset_at, now, tz=tz)
                        if reset_val:
                            line_str += f"  {theme.dim(reset_val)}"

                    lines.append(line_str)

            if row.entry and row.entry.error:
                err_msg = row.entry.error.get("message", "")
                if err_msg:
                    lines.append(f"     {theme.dim(f'last check failed: {err_msg}')}")

        else:
            # No quota pool data: show error / quarantine line
            na_str = theme.err("n/a")
            msg = ""
            hint = ""

            if row.entry and row.entry.error:
                raw_msg = row.entry.error.get("message", "n/a")
                hint = row.entry.error.get("hint", "")
                if not hint and "  → " in raw_msg:
                    msg, hint = raw_msg.split("  → ", 1)
                elif not hint and "  -> " in raw_msg:
                    msg, hint = raw_msg.split("  -> ", 1)
                else:
                    msg = raw_msg
            elif acc.quarantined is not None:
                msg = f"token expired and refresh failed: {acc.quarantined.reason}"
                hint = "sign in as it in agy, then `mswap add`"
            else:
                msg = "no usage data"

            hint_str = f"  {arrow} {hint}" if hint else ""
            lines.append(f"     {na_str}  {msg}{hint_str}")

    return lines


def print_quota(
    quota: QuotaSnapshot | Sequence[Pool] | list[dict[str, Any]],
    now: datetime,
    *,
    tz: tzinfo | None = None,
) -> list[str]:
    """Format quota buckets as lines (legacy compatibility helper)."""
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
