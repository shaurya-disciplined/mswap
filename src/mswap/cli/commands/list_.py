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
from mswap.core.store import find_active, live_target, load_accounts, slot_target
from mswap.ui.render import print_quota
from mswap.ui.theme import bold, cyan, dim, green, red, yellow


def run(ctx: AppContext, _args: list[str] | argparse.Namespace) -> int:
    """Execute the list command."""
    accounts = load_accounts()
    live = ctx.vault.read(live_target())
    active = find_active(accounts, live)

    if not accounts:
        print("No accounts saved yet. Sign in to agy, then run `mswap add`.")
        return 0

    client_info = load_client()
    version = str(client_info.get("version", "1.2.12"))

    def fetch(
        acc: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]] | None, str | None]:
        is_active = active is not None and acc["slot"] == active["slot"]
        blob = live if is_active else ctx.vault.read(slot_target(int(acc["slot"])))
        if not blob:
            return acc, None, "saved login missing (run `mswap add` while signed in as it)"
        try:
            token, updated = ensure_fresh(blob, ctx.http, ctx.clock.now())
            if updated and not is_active:
                ctx.vault.write(slot_target(int(acc["slot"])), updated, str(acc["email"]))
            return acc, quota_groups(token, ctx.http, version), None
        except MswapError as e:
            return acc, None, str(e)

    with ThreadPoolExecutor(max_workers=min(8, len(accounts))) as pool:
        results = list(pool.map(fetch, accounts))

    print(bold("mswap") + dim(" · agy accounts"))
    for acc, groups, err in results:
        is_active = active is not None and acc["slot"] == active["slot"]
        mark = cyan("▸") if is_active else " "
        tag = green(" (active)") if is_active else ""
        print(f"\n {mark} {bold(str(acc['slot']))}  {acc['email']}{tag}")
        if err:
            print(f"     {red('n/a')} {dim(err)}")
        else:
            for line in print_quota(groups or [], ctx.clock.now()):
                print(line)
    if live and not active:
        msg = (
            "\n  agy is signed in to an account mswap doesn't know yet. Run `mswap add` to save it."
        )
        print(yellow(msg))
    print()

    return 0
