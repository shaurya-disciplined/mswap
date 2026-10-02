"""Command to remove a saved Antigravity account."""

from __future__ import annotations

import argparse
import sys

from mswap.cli.context import AppContext
from mswap.core.errors import MswapError, UsageError
from mswap.core.identity import find_by_fp
from mswap.core.store import live_target, slot_target
from mswap.core.switcher import recover, resolve_target
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the remove command."""
    recovery_msg = recover(ctx)
    if recovery_msg:
        print(ctx.theme.dim(recovery_msg), file=ctx.err)

    accounts = ctx.store.load()
    if not accounts:
        raise MswapError("No accounts saved yet.", hint="Run `mswap add` first.")

    selector = getattr(args, "selector", None)
    if not selector:
        raise UsageError("Missing account selector.", hint="Run `mswap remove <selector>`.")

    target = resolve_target(accounts, selector, active=None)
    yes = getattr(args, "yes", False)

    if not yes:
        is_tty = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
        if is_tty:
            prompt = (
                f"Remove account {target.slot} ({target.email})? "
                "Its saved login will be deleted from this PC. [y/N] "
            )
            ctx.out.write(prompt)
            ctx.out.flush()
            answer = sys.stdin.readline().strip().lower()
            if answer not in ("y", "yes"):
                return 0
        else:
            raise UsageError(
                "Refusing to remove without confirmation.",
                hint="Add --yes.",
            )

    live = ctx.vault.read(live_target())
    active = find_by_fp(accounts, live)
    was_active = active is not None and active.slot == target.slot

    with ctx.lock(timeout=10.0):
        accounts = ctx.store.load()
        remaining = [a for a in accounts if a.slot != target.slot]
        ctx.vault.delete(slot_target(target.slot))
        ctx.store.save(remaining)

    if ctx.json:
        data = {
            "removed_slot": target.slot,
            "email": target.email,
        }
        print(jsonout.ok("remove", data), file=ctx.out)
        return 0

    ok_mark = ctx.theme.ok(ctx.theme.glyph_ok)
    slot_str = ctx.theme.bold(str(target.slot))
    print(f"{ok_mark} Removed account {slot_str}: {target.email}", file=ctx.out)
    if was_active:
        print(
            ctx.theme.dim("  agy is still signed in to it; mswap just won't switch to it anymore."),
            file=ctx.out,
        )

    return 0
