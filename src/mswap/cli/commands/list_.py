"""Command to list all saved Antigravity accounts and their quota."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mswap.agy.api import quota_groups
from mswap.agy.client_discovery import load_client
from mswap.agy.tokens import ensure_fresh
from mswap.cli.context import AppContext
from mswap.core.errors import MswapError
from mswap.core.models import Account
from mswap.core.store import find_active, live_target, slot_target
from mswap.ui.render import print_quota


def run(ctx: AppContext, _args: argparse.Namespace) -> int:
    """Execute the list command."""
    accounts = ctx.store.load()
    live = ctx.vault.read(live_target())
    active: Account | None = find_active(accounts, live)

    if not accounts:
        print("No accounts saved yet. Sign in to agy, then run `mswap add`.", file=ctx.out)
        return 0

    client_info = load_client()
    version = str(client_info.get("version", "1.2.12"))

    def fetch(
        acc: Account,
    ) -> tuple[Account, list[dict[str, Any]] | None, str | None]:
        is_active = active is not None and acc.slot == active.slot
        blob = live if is_active else ctx.vault.read(slot_target(acc.slot))
        if not blob:
            return acc, None, "saved login missing (run `mswap add` while signed in as it)"
        try:
            token, updated = ensure_fresh(blob, ctx.http, ctx.clock.now())
            if updated and not is_active:
                ctx.vault.write(slot_target(acc.slot), updated, acc.email)
            return acc, quota_groups(token, ctx.http, version), None
        except MswapError as e:
            return acc, None, str(e)

    with ThreadPoolExecutor(max_workers=min(8, len(accounts))) as pool:
        results = list(pool.map(fetch, accounts))

    header = ctx.theme.bold("mswap") + ctx.theme.dim(" · agy accounts")
    print(header, file=ctx.out)
    for acc, groups, err in results:
        is_active = active is not None and acc.slot == active.slot
        mark = ctx.theme.accent("▸") if is_active else " "
        tag = ctx.theme.ok(" (active)") if is_active else ""
        print(f"\n {mark} {ctx.theme.bold(str(acc.slot))}  {acc.email}{tag}", file=ctx.out)
        if err:
            print(f"     {ctx.theme.err('n/a')} {ctx.theme.dim(err)}", file=ctx.out)
        else:
            for line in print_quota(groups or [], ctx.clock.now()):
                print(line, file=ctx.out)
    if live and not active:
        msg = (
            "\n  agy is signed in to an account mswap doesn't know yet. Run `mswap add` to save it."
        )
        print(ctx.theme.warn(msg), file=ctx.out)
    print("", file=ctx.out)

    return 0
