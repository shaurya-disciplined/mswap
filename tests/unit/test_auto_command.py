"""Unit tests for mswap auto CLI command, flags, exit codes, and output formats."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from mswap.agy.tokens import fingerprint
from mswap.cli.commands.auto import run as run_auto
from mswap.cli.context import AppContext
from mswap.cli.parser import build_parser
from mswap.core.models import Account, Bucket, Pool, QuotaSnapshot
from mswap.core.store import live_target, slot_target
from mswap.core.usage_cache import UsageCache
from tests.conftest import make_blob


def _make_snapshot(rem: float, now: datetime, reset_hours: int = 3) -> QuotaSnapshot:
    bucket = Bucket(window="5h", remaining=rem, reset_at=now + timedelta(hours=reset_hours))
    pool = Pool(key="gemini", name="Gemini", buckets=(bucket,))
    return QuotaSnapshot(fetched_at=now, pools=(pool,))


def _setup_accounts(ctx: AppContext) -> list[Account]:
    now = ctx.clock.now()
    accounts: list[Account] = []
    for i in (1, 2):
        blob = make_blob(i)
        fp = fingerprint(blob)
        acc = Account(
            slot=i,
            email=f"user{i}@example.com",
            fp=fp,
            added_at=now,
            updated_at=now,
        )
        accounts.append(acc)
        ctx.vault.write(slot_target(i), blob, f"user{i}@example.com")

    ctx.store.save(accounts)
    ctx.vault.write(live_target(), make_blob(1), "antigravity")
    return accounts


def test_parser_auto_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "auto",
            "--once",
            "--dry-run",
            "--threshold",
            "85",
            "--strategy",
            "consume-first",
            "--focus",
            "gemini",
            "--interval",
            "30",
            "--force",
            "--json",
        ]
    )
    assert args.command == "auto"
    assert args.once is True
    assert args.dry_run is True
    assert args.threshold == 85
    assert args.strategy == "consume-first"
    assert args.focus == "gemini"
    assert args.interval == 30
    assert args.force is True
    assert args.json is True


def test_auto_once_exit_code_switch(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx)
    now = ctx.clock.now()
    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    args = argparse.Namespace(
        once=True,
        dry_run=False,
        threshold=90,
        strategy=None,
        focus=None,
        interval=60,
        force=False,
        json=False,
    )
    code = run_auto(ctx, args)
    assert code == 0

    out = ctx.out.getvalue()
    assert "switched to 2 (user2@example.com)" in out


def test_auto_once_exit_code_hold(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx)
    now = ctx.clock.now()
    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.80, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    args = argparse.Namespace(
        once=True,
        dry_run=False,
        threshold=90,
        strategy=None,
        focus=None,
        interval=60,
        force=False,
        json=False,
    )
    code = run_auto(ctx, args)
    assert code == 2

    out = ctx.out.getvalue()
    assert "hold: 20% used, below the 90% threshold" in out


def test_auto_once_exit_code_blocked(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx)
    now = ctx.clock.now()
    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now, reset_hours=2), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.05, now, reset_hours=4), now)

    args = argparse.Namespace(
        once=True,
        dry_run=False,
        threshold=90,
        strategy=None,
        focus=None,
        interval=60,
        force=False,
        json=False,
    )
    code = run_auto(ctx, args)
    assert code == 3

    out = ctx.out.getvalue()
    assert "blocked: every other account is at or above the threshold" in out
    assert "next reset:" in out
    assert "account 1, Gemini 5h" in out


def test_auto_json_event_stream(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx)
    now = ctx.clock.now()
    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    ctx.json = True
    args = argparse.Namespace(
        once=True,
        dry_run=True,
        threshold=90,
        strategy="best",
        focus="auto",
        interval=60,
        force=False,
        json=True,
    )
    code = run_auto(ctx, args)
    assert code == 0

    out = ctx.out.getvalue().strip()
    data = json.loads(out)
    assert data["schema"] == 1
    assert data["event"] == "switch"
    assert data["from_slot"] == 1
    assert data["to_slot"] == 2
    assert data["dry_run"] is True
    assert "at" in data
    assert "reason" in data


def test_auto_loop_runs_and_stops_on_ctrl_c(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx)
    now = ctx.clock.now()
    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.80, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    tick_count = 0

    def fake_sleep(sec: float) -> None:
        nonlocal tick_count
        tick_count += 1
        if tick_count >= 3:
            raise KeyboardInterrupt()

    ctx.sleep = fake_sleep

    args = argparse.Namespace(
        once=False,
        dry_run=False,
        threshold=90,
        strategy=None,
        focus=None,
        interval=60,
        force=False,
        json=False,
    )
    code = run_auto(ctx, args)
    assert code == 0
    assert tick_count == 3
    out = ctx.out.getvalue()
    assert "autopilot stopped" in out
