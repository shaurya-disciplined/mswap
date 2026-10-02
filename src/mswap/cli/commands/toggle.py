"""Command to enable or disable an Antigravity account."""

from __future__ import annotations

import argparse
import dataclasses

from mswap.cli.context import AppContext
from mswap.core.errors import MswapError, UsageError
from mswap.core.switcher import resolve_target
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute enable or disable command."""
    command = getattr(args, "command", "disable")
    disable_flag = command == "disable"

    accounts = ctx.store.load()
    if not accounts:
        raise MswapError("No accounts saved yet.", hint="Run `mswap add` first.")

    selector = getattr(args, "selector", None)
    if not selector:
        raise UsageError(
            "Missing account selector.",
            hint=f"Run `mswap {command} <selector>`.",
        )

    target = resolve_target(accounts, selector, active=None)

    with ctx.lock(timeout=10.0):
        accounts = ctx.store.load()
        idx = next((i for i, a in enumerate(accounts) if a.slot == target.slot), None)
        if idx is None:
            raise UsageError(f"No account #{target.slot}.", hint="See `mswap list`.")
        accounts[idx] = dataclasses.replace(accounts[idx], disabled=disable_flag)
        ctx.store.save(accounts)

    if ctx.json:
        data = {
            "slot": target.slot,
            "disabled": disable_flag,
        }
        print(jsonout.ok(command, data), file=ctx.out)
        return 0

    ok_mark = ctx.theme.ok(ctx.theme.glyph_ok)
    slot_str = ctx.theme.bold(str(target.slot))
    if disable_flag:
        print(
            f"{ok_mark} Account {slot_str} disabled. Rotation and autopilot will skip it.",
            file=ctx.out,
        )
    else:
        print(f"{ok_mark} Account {slot_str} enabled.", file=ctx.out)

    return 0
