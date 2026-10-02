"""Command to set or clear an alias for an Antigravity account."""

from __future__ import annotations

import argparse
import dataclasses

from mswap.cli.context import AppContext
from mswap.core.errors import MswapError, UsageError
from mswap.core.models import validate_alias
from mswap.core.switcher import resolve_target
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the alias command."""
    accounts = ctx.store.load()
    if not accounts:
        raise MswapError("No accounts saved yet.", hint="Run `mswap add` first.")

    selector = getattr(args, "selector", None)
    if not selector:
        raise UsageError("Missing account selector.", hint="Run `mswap alias <selector> <name>`.")

    target = resolve_target(accounts, selector, active=None)

    clear = getattr(args, "clear", False)
    name = getattr(args, "name", None)

    if clear and name:
        raise UsageError(
            "Cannot specify both an alias name and --clear.",
            hint="Choose one or the other.",
        )
    if not clear and not name:
        raise UsageError(
            "Must specify an alias name or --clear.",
            hint="Run `mswap alias <selector> <name>` or `mswap alias <selector> --clear`.",
        )

    if clear:
        new_alias = None
    else:
        assert name is not None
        validate_alias(name, accounts, exclude_slot=target.slot)
        new_alias = name

    with ctx.lock(timeout=10.0):
        accounts = ctx.store.load()
        idx = next((i for i, a in enumerate(accounts) if a.slot == target.slot), None)
        if idx is None:
            raise UsageError(f"No account #{target.slot}.", hint="See `mswap list`.")
        accounts[idx] = dataclasses.replace(accounts[idx], alias=new_alias)
        ctx.store.save(accounts)

    if ctx.json:
        data = {
            "slot": target.slot,
            "alias": new_alias,
        }
        print(jsonout.ok("alias", data), file=ctx.out)
        return 0

    ok_mark = ctx.theme.ok(ctx.theme.glyph_ok)
    slot_str = ctx.theme.bold(str(target.slot))
    if new_alias is None:
        print(f"{ok_mark} Cleared alias for account {slot_str}.", file=ctx.out)
    else:
        print(f"{ok_mark} Set alias for account {slot_str} to '{new_alias}'.", file=ctx.out)

    return 0
