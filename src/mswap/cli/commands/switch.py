"""Command to switch the active Antigravity account."""

from __future__ import annotations

import argparse

from mswap.cli.context import AppContext
from mswap.core.switcher import recover, switch
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the switch command."""
    recovery_msg = recover(ctx)
    if recovery_msg:
        print(ctx.theme.dim(recovery_msg), file=ctx.err)

    force = getattr(args, "force", False) if isinstance(args, argparse.Namespace) else False
    selector = (
        getattr(args, "selector", None)
        if isinstance(args, argparse.Namespace)
        else (args[0] if args else None)
    )

    result = switch(ctx, selector, force=force)

    if ctx.json:
        data = {
            "status": result.status,
            "from_slot": result.from_account.slot if result.from_account else None,
            "to_slot": result.to_account.slot,
            "agy_running": result.agy_running,
        }
        print(jsonout.ok("switch", data), file=ctx.out)
        return 0

    if result.status == "already_active":
        print(
            f"Already on account {result.to_account.slot}: {result.to_account.email}",
            file=ctx.out,
        )
        return 0

    ok_mark = ctx.theme.ok(ctx.theme.glyph_ok)
    slot_str = ctx.theme.bold(str(result.to_account.slot))
    print(f"{ok_mark} Switched agy to account {slot_str}: {result.to_account.email}", file=ctx.out)
    restart_hint = ctx.theme.dim(
        "  New agy sessions use it. Restart any agy that's already running."
    )
    print(restart_hint, file=ctx.out)

    return 0
