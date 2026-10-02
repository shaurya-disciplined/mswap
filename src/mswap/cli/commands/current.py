"""Command to show the currently active Antigravity account."""

from __future__ import annotations

import argparse

from mswap.cli.context import AppContext
from mswap.core.errors import NotSignedIn
from mswap.core.identity import find_by_fp
from mswap.core.models import account_to_json
from mswap.core.store import live_target
from mswap.ui import jsonout


def run(ctx: AppContext, _args: argparse.Namespace) -> int:
    """Execute the current command."""
    live = ctx.vault.read(live_target())
    if live is None:
        raise NotSignedIn(
            "agy isn't signed in.",
            hint="Run `agy`, sign in, then try again.",
        )

    accounts = ctx.store.load()
    active = find_by_fp(accounts, live)

    if ctx.json:
        data = {
            "active": account_to_json(active) if active is not None else None,
            "live_present": True,
        }
        print(jsonout.ok("current", data), file=ctx.out)
        return 0

    if active is None:
        print(
            "agy is signed in to an account mswap hasn't saved. Run `mswap add`.",
            file=ctx.out,
        )
        return 0

    alias_str = f"  · alias {active.alias}" if active.alias else ""
    print(f"{active.slot}  {active.email}{alias_str}", file=ctx.out)
    return 0
