"""Command to switch the active Antigravity account."""

from __future__ import annotations

import argparse

from mswap.cli.context import AppContext
from mswap.core.errors import MswapError, NothingToDo, UsageError
from mswap.core.models import Account
from mswap.core.store import (
    LIVE_USER,
    backup_last,
    backup_original,
    find_active,
    live_target,
    slot_target,
)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the switch command."""
    accounts = ctx.store.load()
    if not accounts:
        raise MswapError("No accounts saved yet.", hint="Run `mswap add` first.")

    live = ctx.vault.read(live_target())
    active: Account | None = find_active(accounts, live)

    key = (
        getattr(args, "selector", None)
        if isinstance(args, argparse.Namespace)
        else (args[0] if args else None)
    )

    if key is not None:
        target = next(
            (a for a in accounts if str(a.slot) == key or a.email.lower() == key.lower()),
            None,
        )
        if not target:
            raise UsageError(f"No account matching '{key}'.", hint="See `mswap list`.")
    else:
        if len(accounts) < 2:
            raise NothingToDo("Only one account saved.", hint="Add another with `mswap add --new`.")
        slots = [a.slot for a in accounts]
        i = slots.index(active.slot) if active else -1
        target = accounts[(i + 1) % len(accounts)]

    if active and target.slot == active.slot:
        print(f"Already on account {target.slot}: {target.email}", file=ctx.out)
        return 0

    blob = ctx.vault.read(slot_target(target.slot))
    if not blob:
        raise MswapError(
            f"Saved login for account {target.slot} is missing.",
            hint="Re-add it with `mswap add`.",
        )

    if live:
        if ctx.vault.read(backup_original()) is None:
            ctx.vault.write(backup_original(), live, LIVE_USER)
        ctx.vault.write(backup_last(), live, LIVE_USER)
        if active:
            ctx.vault.write(slot_target(active.slot), live, active.email)

    ctx.vault.write(live_target(), blob, LIVE_USER)
    ok_mark = ctx.theme.ok("✓")
    slot_str = ctx.theme.bold(str(target.slot))
    print(f"{ok_mark} Switched agy to account {slot_str}: {target.email}", file=ctx.out)
    restart_hint = ctx.theme.dim(
        "  New agy sessions use it. Restart any agy that's already running."
    )
    print(restart_hint, file=ctx.out)

    return 0
