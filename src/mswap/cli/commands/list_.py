"""Command to list all saved Antigravity accounts and their quota."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mswap.agy.api import AgyApi
from mswap.agy.install import agy_version, os_arch, user_agent
from mswap.agy.paths import agy_exe
from mswap.agy.tokens import TokenService
from mswap.cli.context import AppContext
from mswap.core.errors import MswapError, TokenDead
from mswap.core.models import Account, QuotaSnapshot
from mswap.core.store import find_active, live_target, slot_target
from mswap.ui import jsonout
from mswap.ui.render import print_quota


def run(ctx: AppContext, _args: argparse.Namespace) -> int:
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

    cache_file = ctx.store.root / "client.json"
    if not cache_file.exists():
        legacy_cfg = ctx.store.root / "config.json"
        if legacy_cfg.exists():
            cache_file = legacy_cfg
    cache: dict[str, Any] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    version = agy_version(agy_exe(), cache)
    ua = user_agent(version, os_arch())
    api = AgyApi(ctx.http, ua, clock=ctx.clock)
    token_service = TokenService(ctx)

    def fetch(
        acc: Account,
    ) -> tuple[Account, QuotaSnapshot | None, str | None]:
        is_active = active is not None and acc.slot == active.slot
        if acc.quarantined is not None:
            return (
                acc,
                None,
                "saved login expired or revoked  → sign in as it in agy, then `mswap add`",
            )
        if os.environ.get("MSWAP_DEMO") == "1":
            usage_file = ctx.store.root / "usage.json"
            if usage_file.exists():
                with contextlib.suppress(Exception):
                    usage_json = json.loads(usage_file.read_text(encoding="utf-8"))
                    acc_entry = usage_json.get("accounts", {}).get(acc.fp)
                    if acc_entry and "groups" in acc_entry:
                        snap = AgyApi._parse_summary(
                            {"groups": acc_entry["groups"]},
                            ctx.clock.now(),
                        )
                        return acc, snap, None
        blob = live if is_active else ctx.vault.read(slot_target(acc.slot))
        if not blob:
            return acc, None, "saved login missing (run `mswap add` while signed in as it)"
        try:
            target = blob if is_active else acc
            snapshot = token_service.call_with_retry(target, api.quota_summary)
            return acc, snapshot, None
        except TokenDead:
            return (
                acc,
                None,
                "saved login expired or revoked  → sign in as it in agy, then `mswap add`",
            )
        except MswapError as e:
            return acc, None, str(e)

    with ThreadPoolExecutor(max_workers=min(8, len(accounts))) as pool:
        results = list(pool.map(fetch, accounts))

    if ctx.json:
        now_iso = ctx.clock.now().isoformat()
        accounts_data: list[dict[str, Any]] = []
        for acc, snapshot, err in results:
            is_active = active is not None and acc.slot == active.slot
            pools_json = (
                [p.to_json() for p in snapshot.pools]
                if (snapshot is not None and err is None)
                else []
            )
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
                        "fetched_at": (
                            snapshot.fetched_at.isoformat() if snapshot is not None else now_iso
                        ),
                        "stale": False,
                        "error": err,
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

    header = ctx.theme.bold("mswap") + ctx.theme.dim(" · agy accounts")
    print(header, file=ctx.out)
    for acc, snapshot, err in results:
        is_active = active is not None and acc.slot == active.slot
        mark = ctx.theme.accent(ctx.theme.glyph_active) if is_active else " "
        tag = ctx.theme.ok(" (active)") if is_active else ""
        alias_str = f"  · alias {acc.alias}" if acc.alias else ""
        disabled_str = "  · disabled" if acc.disabled else ""
        slot_str = ctx.theme.bold(str(acc.slot))
        print(f"\n {mark} {slot_str}  {acc.email}{tag}{alias_str}{disabled_str}", file=ctx.out)
        if err:
            print(f"     {ctx.theme.err('n/a')}  {ctx.theme.dim(err)}", file=ctx.out)
        elif snapshot is not None:
            if snapshot.source == "models":
                print(ctx.theme.dim("  (per-model view: summary unavailable)"), file=ctx.out)
            for line in print_quota(snapshot, ctx.clock.now()):
                print(line, file=ctx.out)
    if live and not active:
        msg = (
            "\n  agy is signed in to an account mswap doesn't know yet. Run `mswap add` to save it."
        )
        print(ctx.theme.warn(msg), file=ctx.out)
    print("", file=ctx.out)

    return 0
