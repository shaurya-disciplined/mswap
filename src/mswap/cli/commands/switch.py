"""Command to switch the active Antigravity account."""

from __future__ import annotations

import argparse

from mswap.cli.context import AppContext
from mswap.core.errors import MswapError, NothingToDo, UsageError
from mswap.core.store import (
    LIVE_USER,
    backup_last,
    backup_original,
    find_active,
    live_target,
    load_accounts,
    slot_target,
)
from mswap.ui.theme import bold, dim, green


def run(ctx: AppContext, args: list[str] | argparse.Namespace) -> int:
    """Execute the switch command."""
    accounts = load_accounts()
    if not accounts:
        raise MswapError("No accounts saved yet.", hint="Run `mswap add` first.")

    live = ctx.vault.read(live_target())
    active = find_active(accounts, live)

    key = args[0] if isinstance(args, list) and args else getattr(args, "selector", None)

    if key is not None:
        target = next(
            (a for a in accounts if str(a["slot"]) == key or a["email"].lower() == key.lower()),
            None,
        )
        if not target:
            raise UsageError(f"No account matching '{key}'.", hint="See `mswap list`.")
    else:
        if len(accounts) < 2:
            raise NothingToDo("Only one account saved.", hint="Add another with `mswap add --new`.")
        slots = [a["slot"] for a in accounts]
        i = slots.index(active["slot"]) if active else -1
        target = accounts[(i + 1) % len(accounts)]

    if active and target["slot"] == active["slot"]:
        print(f"Already on account {target['slot']}: {target['email']}")
        return 0

    blob = ctx.vault.read(slot_target(int(target["slot"])))
    if not blob:
        raise MswapError(
            f"Saved login for account {target['slot']} is missing.",
            hint="Re-add it with `mswap add`.",
        )

    if live:
        if ctx.vault.read(backup_original()) is None:
            ctx.vault.write(backup_original(), live, LIVE_USER)
        ctx.vault.write(backup_last(), live, LIVE_USER)
        if active:
            ctx.vault.write(slot_target(int(active["slot"])), live, str(active["email"]))

    ctx.vault.write(live_target(), blob, LIVE_USER)
    print(f"{green('✓')} Switched agy to account {bold(str(target['slot']))}: {target['email']}")
    print(dim("  New agy sessions use it. Restart any agy that's already running."))

    return 0
