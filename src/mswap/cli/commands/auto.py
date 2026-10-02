"""Foreground autopilot loop and one-shot runner command.

Owns the `mswap auto` CLI command, handling --once, --dry-run, --json, and the polling loop.
Must never log unredacted credentials or perform direct vault writes outside switcher.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any, Literal

from mswap.cli.context import AppContext
from mswap.core.autopilot import tick
from mswap.core.errors import UsageError
from mswap.core.identity import find_by_fp
from mswap.core.policy import Decision, _normalize_dt
from mswap.core.policy import Settings as PolicySettings
from mswap.core.settings import load_settings
from mswap.core.store import live_target


def _find_earliest_reset(
    accounts: list[Any],
    entries: dict[int, Any],
    focus: tuple[str, ...],
    now: datetime,
) -> tuple[int, str, str, str] | None:
    """Find the earliest reset time across accounts' focus pools.

    Returns tuple of (slot, pool_name, window, time_str) or None.
    """
    candidates_with_resets: list[tuple[datetime, int, str, str, str]] = []

    for acc in accounts:
        entry = entries.get(acc.slot)
        if not entry or not entry.snapshot:
            continue
        for pool in entry.snapshot.pools:
            if focus and pool.key not in focus:
                continue
            for b in pool.buckets:
                if b.reset_at is not None:
                    norm_reset = _normalize_dt(b.reset_at, now)
                    time_str = norm_reset.astimezone().strftime("%H:%M")
                    candidates_with_resets.append(
                        (norm_reset, acc.slot, pool.name, b.window, time_str)
                    )

    if not candidates_with_resets:
        return None

    candidates_with_resets.sort(key=lambda item: (item[0], item[1]))
    best = candidates_with_resets[0]
    return (best[1], best[2], best[3], best[4])


def _render_human_decision(
    ctx: AppContext,
    decision: Decision,
    now: datetime,
    accounts: list[Any],
    entries: dict[int, Any],
) -> None:
    """Render human-readable output line per §A18."""
    time_str = now.strftime("%H:%M:%S")

    if decision.kind == "switch":
        target_acc = next((a for a in accounts if a.slot == decision.target_slot), None)
        target_info = target_acc.email if target_acc else f"account {decision.target_slot}"
        ok_sym = ctx.theme.ok("✓")
        print(
            f"{time_str} {ok_sym} switched to {decision.target_slot} ({target_info})",
            file=ctx.out,
            flush=True,
        )
    elif decision.kind == "hold":
        dim_sym = ctx.theme.dim("·")
        print(f"{time_str} {dim_sym} hold: {decision.reason}", file=ctx.out, flush=True)
    elif decision.kind == "blocked":
        warn_sym = ctx.theme.warn("!")
        print(f"{time_str} {warn_sym} blocked: {decision.reason}", file=ctx.out, flush=True)
        reset_info = _find_earliest_reset(accounts, entries, decision.focus, now)
        if reset_info is not None:
            slot, pool_name, window, time_hm = reset_info
            dim_next = ctx.theme.dim(
                f"  next reset: {time_hm} (account {slot}, {pool_name} {window})"
            )
            print(dim_next, file=ctx.out, flush=True)


def _render_json_event(
    ctx: AppContext,
    decision: Decision,
    now: datetime,
    active_slot: int | None,
    dry_run: bool,
) -> None:
    """Render one JSON event line per §A17."""
    event_obj = {
        "schema": 1,
        "event": decision.kind,
        "at": now.isoformat(),
        "reason": decision.reason,
        "from_slot": active_slot,
        "to_slot": decision.target_slot,
        "focus": list(decision.focus),
        "active_pressure": decision.active_pressure,
        "target_pressure": decision.target_pressure,
        "dry_run": dry_run,
    }
    print(json.dumps(event_obj, ensure_ascii=False), file=ctx.out, flush=True)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the mswap auto command."""
    cfg = load_settings()

    threshold_arg = getattr(args, "threshold", None)
    threshold = int(threshold_arg) if threshold_arg is not None else cfg.autopilot.threshold

    strategy_arg = getattr(args, "strategy", None)
    strategy: Literal["best", "consume-first"] = (
        strategy_arg if strategy_arg in ("best", "consume-first") else cfg.autopilot.strategy
    )

    focus_arg = getattr(args, "focus", None)
    focus: Literal["auto", "gemini", "3p", "both"] = (
        focus_arg if focus_arg in ("auto", "gemini", "3p", "both") else cfg.autopilot.focus
    )
    interval = getattr(args, "interval", 60) or 60
    if interval <= 0:
        raise UsageError("--interval must be greater than 0 seconds.")

    dry_run = getattr(args, "dry_run", False)
    once = getattr(args, "once", False)
    force = getattr(args, "force", False)

    policy_settings = PolicySettings(
        threshold=threshold,
        margin=cfg.autopilot.margin,
        cooldown_s=cfg.autopilot.cooldown,
        strategy=strategy,
        focus=focus,
    )

    def do_tick() -> tuple[Decision, int]:
        now = ctx.clock.now() if hasattr(ctx, "clock") else datetime.now(UTC)
        decision = tick(ctx, policy_settings, dry_run=dry_run, force=force)

        accounts = ctx.store.load()
        from mswap.core.usage import refresh_usage

        entries = refresh_usage(ctx, accounts, force=False)
        live = ctx.vault.read(live_target())
        active_acc = find_by_fp(accounts, live)
        active_slot = active_acc.slot if active_acc else None

        if ctx.json:
            _render_json_event(ctx, decision, now, active_slot, dry_run)
        else:
            _render_human_decision(ctx, decision, now, accounts, entries)

        # Code per §A10 / SPEC: switch 0, hold 2, blocked 3
        if decision.kind == "switch":
            return decision, 0
        if decision.kind == "hold":
            return decision, 2
        return decision, 3

    if once:
        try:
            _, exit_code = do_tick()
            return exit_code
        except KeyboardInterrupt:
            if not ctx.json:
                print("autopilot stopped", file=ctx.out)
            return 0
        except Exception as e:
            if ctx.json:
                err_obj = {
                    "schema": 1,
                    "event": "error",
                    "at": (
                        ctx.clock.now() if hasattr(ctx, "clock") else datetime.now(UTC)
                    ).isoformat(),
                    "message": str(e),
                }
                print(json.dumps(err_obj, ensure_ascii=False), file=ctx.out, flush=True)
            raise

    try:
        while True:
            do_tick()
            ctx.sleep(interval)
    except KeyboardInterrupt:
        if not ctx.json:
            print("autopilot stopped", file=ctx.out)
        return 0
