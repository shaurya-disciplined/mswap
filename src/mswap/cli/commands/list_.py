"""Command to list all saved Antigravity accounts and their quota."""

from __future__ import annotations

import argparse
import shutil
from typing import Any

from mswap.cli.context import AppContext
from mswap.cli.update_notice import maybe_print_update_notice
from mswap.core.models import Account
from mswap.core.pace import pace
from mswap.core.poll_policy import ttl
from mswap.core.store import find_active, live_target
from mswap.core.usage import refresh_usage
from mswap.ui import jsonout
from mswap.ui.render import AccountRow, render_list


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
    # Reload accounts from store in case refresh_usage updated plan
    accounts = ctx.store.load()
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

            pools_json: list[dict[str, Any]] = []
            if snap is not None:
                for p in snap.pools:
                    buckets_json: list[dict[str, Any]] = []
                    for b in p.buckets:
                        b_dict = b.to_json()
                        p_info = pace(b, now)
                        b_dict["pace"] = p_info.to_json() if p_info is not None else None
                        buckets_json.append(b_dict)
                    pools_json.append(
                        {
                            "key": p.key,
                            "name": p.name,
                            "buckets": buckets_json,
                        }
                    )
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

    rows: list[AccountRow] = []
    for acc in accounts:
        is_active = active is not None and acc.slot == active.slot
        entry = results.get(acc.slot)
        snap = entry.snapshot if entry else None
        if entry is not None:
            ttl_val = ttl(snap, active=is_active)
            elapsed = (now - entry.fetched_at).total_seconds()
            in_backoff = entry.backoff_until is not None and now < entry.backoff_until
            is_stale_flag = elapsed > ttl_val or in_backoff
        else:
            is_stale_flag = True
        rows.append(AccountRow(account=acc, active=is_active, entry=entry, stale=is_stale_flag))

    width = shutil.get_terminal_size((80, 24)).columns
    for line in render_list(rows, now=now, theme=ctx.theme, width=width, tz=None):
        print(line, file=ctx.out)

    if live and not active:
        msg = (
            "\n  agy is signed in to an account mswap doesn't know yet. Run `mswap add` to save it."
        )
        print(ctx.theme.warn(msg), file=ctx.out)
    print("", file=ctx.out)
    maybe_print_update_notice(ctx)

    return 0
