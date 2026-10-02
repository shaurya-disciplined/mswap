"""Command to print a cache-only one-liner for shell prompts."""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

from mswap.cli.context import AppContext
from mswap.core.errors import UsageError
from mswap.core.models import Account, QuotaSnapshot
from mswap.core.store import find_active, live_target
from mswap.core.usage_cache import CacheEntry, UsageCache
from mswap.ui import jsonout
from mswap.ui.timefmt import age_text

DEFAULT_FORMAT = "{slot}:{email_short} G{gemini_5h}% C{3p_5h}%"


def _get_email_short(email: str) -> str:
    """Return local part of email truncated to 8 chars, or '?' if missing."""
    if not email:
        return "?"
    local = email.split("@", 1)[0] if "@" in email else email
    truncated = local[:8]
    return truncated if truncated else "?"


def _get_bucket_info(
    snap: QuotaSnapshot | None, pool_key: str, window: str
) -> tuple[int | None, str]:
    """Return (floored_int_pct, str_pct) for a bucket, or (None, '?') if missing."""
    if snap is None:
        return None, "?"
    for pool in snap.pools:
        if pool.key.lower() == pool_key.lower():
            for b in pool.buckets:
                if b.window.lower() == window.lower():
                    pct = int(b.remaining * 100)
                    return pct, str(pct)
    return None, "?"


def format_status(
    fmt: str,
    active: Account,
    entry: CacheEntry | None,
    now: datetime,
) -> tuple[str, dict[str, Any]]:
    """Format status string using provided template and cache entry data."""
    email_short = _get_email_short(active.email)
    snap = entry.snapshot if entry else None

    g5_int, g5_str = _get_bucket_info(snap, "gemini", "5h")
    gw_int, gw_str = _get_bucket_info(snap, "gemini", "weekly")
    c5_int, c5_str = _get_bucket_info(snap, "3p", "5h")
    cw_int, cw_str = _get_bucket_info(snap, "3p", "weekly")

    if entry and entry.fetched_at:
        elapsed = max(0, int((now - entry.fetched_at).total_seconds()))
        age_str = age_text(elapsed)
    else:
        age_str = "?"

    placeholders: dict[str, str] = {
        "slot": str(active.slot),
        "email": active.email,
        "email_short": email_short,
        "alias": active.alias or "",
        "gemini_5h": g5_str,
        "gemini_week": gw_str,
        "3p_5h": c5_str,
        "3p_week": cw_str,
        "age": age_str,
    }

    try:
        formatted = fmt.format_map(placeholders)
    except KeyError as err:
        raise UsageError(
            f"Unknown format placeholder: {err}",
            hint=(
                "Valid placeholders: slot, email, email_short, alias, "
                "gemini_5h, gemini_week, 3p_5h, 3p_week, age"
            ),
        ) from None

    data: dict[str, Any] = {
        "slot": active.slot,
        "email": active.email,
        "email_short": email_short,
        "alias": active.alias,
        "gemini_5h": g5_int,
        "gemini_week": gw_int,
        "3p_5h": c5_int,
        "3p_week": cw_int,
        "age": age_str if age_str != "?" else None,
        "formatted": formatted,
    }
    return formatted, data


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the status command."""
    accounts = ctx.store.load()
    if not accounts:
        return 0

    live = ctx.vault.read(live_target())
    active = find_active(accounts, live)
    if active is None:
        return 0

    cache = UsageCache(ctx.store.root / "usage.json")
    entry = cache.get(active.fp)
    now = ctx.clock.now()

    fmt = getattr(args, "format", None) or DEFAULT_FORMAT
    formatted, data = format_status(fmt, active, entry, now)

    if ctx.json:
        print(jsonout.ok("status", data), file=ctx.out)
    else:
        print(formatted, file=ctx.out)

    return 0
