"""Command to list all saved Antigravity accounts and their quota."""

from __future__ import annotations

import argparse
from typing import Any

from mswap.cli.context import AppContext
from mswap.core.models import Account
from mswap.core.poll_policy import ttl
from mswap.core.store import find_active, live_target
from mswap.core.usage import refresh_usage
from mswap.ui import jsonout
from mswap.ui.render import format_age, print_quota


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the list command."""
    accounts = ctx.store.load()
    live = ctx.vault.read(live_target())
    active: Account | None = find_active(accounts, live)

    if not accounts:
        if ctx.json:
            empty_data: dict[str, Any] = {"active_slot": None, "accounts": []}
            print(jsonout.ok("list", empty_data), file=ctx.out)
            return 0
        print("No accounts saved yet. Sign in to agy, then run `mswap add`.", file=ctx.out)
        return 0

    force = getattr(args, "refresh", False)
    results = refresh_usage(ctx, accounts, force=force)

    now = ctx.clock.now()

    if ctx.json:
        accounts_data: list[dict[str, Any]] = []
        for acc in accounts:
            entry = results.get(acc.slot)
            is_active = active is not None and acc.slot == active.slot
            snap = entry.snapshot if entry else None

            if entry is not None:
                ttl_val = ttl(snap, active=is_active)
                elapsed = (now - entry.fetched_at).total_seconds()
                is_stale_flag = elapsed > ttl_val
            else:
                is_stale_flag = True

            err_msg: str | None = None
            if entry and entry.error:
                err_msg = entry.error.get("message")

            pools_json = [p.to_json() for p in snap.pools] if snap is not None else []
            fetched_at_str = entry.fetched_at.isoformat() if entry else now.isoformat()

            accounts_data.append(
                {
                    "slot": acc.slot,
                    "email": acc.email,
                    "alias": acc.alias,
                    "active": is_active,
                    "disabled": acc.disabled,
                    "quarantined": (
                        {
                            "reason": acc.quarantined.reason,
                            "at": acc.quarantined.at.isoformat(),
                        }
                        if acc.quarantined is not None
                        else None
                    ),
                    "plan": acc.plan,
                    "usage": {
                        "fetched_at": fetched_at_str,
                        "stale": is_stale_flag,
                        "error": err_msg,
                        "pools": pools_json,
                    },
                }
            )
        data: dict[str, Any] = {
            "active_slot": active.slot if active is not None else None,
            "accounts": accounts_data,
        }
        print(jsonout.ok("list", data), file=ctx.out)
        return 0

    header = ctx.theme.bold("mswap") + ctx.theme.dim(" · agy accounts")
    print(header, file=ctx.out)
    for acc in accounts:
        is_active = active is not None and acc.slot == active.slot
        entry = results.get(acc.slot)
        snap = entry.snapshot if entry else None
        err = entry.error if entry else None
        err_msg = err.get("message") if err else None

        mark = ctx.theme.accent(ctx.theme.glyph_active) if is_active else " "
        tag = ctx.theme.ok(" (active)") if is_active else ""
        alias_str = f"  · alias {acc.alias}" if acc.alias else ""
        disabled_str = "  · disabled" if acc.disabled else ""
        slot_str = ctx.theme.bold(str(acc.slot))

        age_suffix = ""
        if snap is not None and entry is not None:
            ttl_val = ttl(snap, active=is_active)
            elapsed = (now - entry.fetched_at).total_seconds()
            in_backoff = entry.backoff_until is not None and now < entry.backoff_until
            if elapsed > ttl_val or in_backoff:
                age_suffix = ctx.theme.dim(f" · {format_age(int(elapsed))} ago")

        print(
            f"\n {mark} {slot_str}  {acc.email}{tag}{alias_str}{disabled_str}{age_suffix}",
            file=ctx.out,
        )

        if snap is not None:
            if snap.source == "models":
                print(ctx.theme.dim("  (per-model view: summary unavailable)"), file=ctx.out)
            for line in print_quota(snap, now):
                print(line, file=ctx.out)
            if err_msg:
                print(f"     {ctx.theme.dim(f'last check failed: {err_msg}')}", file=ctx.out)
        elif err_msg:
            print(f"     {ctx.theme.err('n/a')}  {ctx.theme.dim(err_msg)}", file=ctx.out)

    if live and not active:
        msg = (
            "\n  agy is signed in to an account mswap doesn't know yet. Run `mswap add` to save it."
        )
        print(ctx.theme.warn(msg), file=ctx.out)
    print("", file=ctx.out)

    return 0
