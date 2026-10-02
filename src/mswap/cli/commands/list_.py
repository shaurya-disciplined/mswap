"""Command to list all saved Antigravity accounts and their quota."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mswap.agy.api import quota_groups
from mswap.agy.install import agy_version, os_arch, user_agent
from mswap.agy.paths import agy_exe
from mswap.agy.tokens import TokenService
from mswap.cli.context import AppContext
from mswap.core.errors import MswapError, TokenDead
from mswap.core.models import Account
from mswap.core.store import find_active, live_target, slot_target
from mswap.ui import jsonout
from mswap.ui.render import print_quota


def _build_pools(groups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not groups:
        return []
    pools_map: dict[str, dict[str, Any]] = {}
    for g in groups:
        buckets_raw = g.get("buckets", [])
        if not isinstance(buckets_raw, list):
            continue
        for b in buckets_raw:
            if not isinstance(b, dict):
                continue
            bucket_id = str(b.get("bucketId", ""))
            key = bucket_id.split("-", 1)[0] if "-" in bucket_id else bucket_id
            if not key:
                key = "unknown"
            if key not in pools_map:
                if key == "gemini":
                    name = "Gemini"
                elif key == "3p":
                    name = "Claude & GPT"
                else:
                    name = str(g.get("displayName", key))
                pools_map[key] = {
                    "key": key,
                    "name": name,
                    "buckets": [],
                }
            rem_raw = b.get("remainingFraction")
            rem = float(rem_raw) if rem_raw is not None else 0.0
            reset_at = b.get("resetTime") if rem < 1.0 else None
            pools_map[key]["buckets"].append(
                {
                    "window": str(b.get("window", "")),
                    "remaining": rem,
                    "reset_at": reset_at,
                }
            )
    for p in pools_map.values():
        p["buckets"].sort(
            key=lambda x: (
                0 if x["window"] == "5h" else (1 if x["window"] == "weekly" else 2),
                x["window"],
            )
        )
    return sorted(
        pools_map.values(),
        key=lambda p: (
            0 if p["key"] == "gemini" else (1 if p["key"] == "3p" else 2),
            p["key"],
        ),
    )


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
    token_service = TokenService(ctx)

    def fetch(
        acc: Account,
    ) -> tuple[Account, list[dict[str, Any]] | None, str | None]:
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
                        return acc, acc_entry["groups"], None
        blob = live if is_active else ctx.vault.read(slot_target(acc.slot))
        if not blob:
            return acc, None, "saved login missing (run `mswap add` while signed in as it)"
        try:
            if is_active:
                token = token_service.fresh_for_live(blob)
            else:
                token = token_service.fresh_for_slot(acc)
            return acc, quota_groups(token, ctx.http, version, ua=ua), None
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
        for acc, groups, err in results:
            is_active = active is not None and acc.slot == active.slot
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
                        "fetched_at": now_iso,
                        "stale": False,
                        "error": err,
                        "pools": _build_pools(groups) if err is None else [],
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
    for acc, groups, err in results:
        is_active = active is not None and acc.slot == active.slot
        mark = ctx.theme.accent(ctx.theme.glyph_active) if is_active else " "
        tag = ctx.theme.ok(" (active)") if is_active else ""
        alias_str = f"  · alias {acc.alias}" if acc.alias else ""
        disabled_str = "  · disabled" if acc.disabled else ""
        slot_str = ctx.theme.bold(str(acc.slot))
        print(f"\n {mark} {slot_str}  {acc.email}{tag}{alias_str}{disabled_str}", file=ctx.out)
        if err:
            print(f"     {ctx.theme.err('n/a')}  {ctx.theme.dim(err)}", file=ctx.out)
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
