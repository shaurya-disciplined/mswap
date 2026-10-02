"""Foreground autopilot loop and one-shot runner command.

Owns the `mswap auto` CLI command, handling --once, --dry-run, --json, and the polling loop.
Must never log unredacted credentials or perform direct vault writes outside switcher.
"""

from __future__ import annotations

import argparse
import contextlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from mswap.cli.context import AppContext
from mswap.core.autopilot import tick
from mswap.core.errors import UsageError
from mswap.core.identity import find_by_fp
from mswap.core.policy import Decision, _normalize_dt
from mswap.core.policy import Settings as PolicySettings
from mswap.core.poll_policy import NEAR_LIMIT, is_stale
from mswap.core.settings import load_settings
from mswap.core.store import find_active, live_target
from mswap.core.usage_cache import UsageCache
from mswap.util.clock import SystemClock


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
    from_hook = getattr(args, "from_hook", False)

    if from_hook:
        return _run_from_hook(ctx, args)

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
            with contextlib.suppress(Exception):
                ctx.events.emit("error", reason=str(e))
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
            try:
                do_tick()
            except Exception as e:
                with contextlib.suppress(Exception):
                    ctx.events.emit("error", reason=str(e))
                raise
            ctx.sleep(interval)
    except KeyboardInterrupt:
        if not ctx.json:
            print("autopilot stopped", file=ctx.out)
        return 0


def _run_from_hook(
    ctx: AppContext,
    _args: argparse.Namespace,
    *,
    timer: Callable[[], float] | None = None,
) -> int:
    """Run autopilot from an agy Stop hook with an 8-second time budget.

    Never raises.  Always exits 0.  Only prints if a switch happened
    or if the hook_action is "notify" and the policy says switch.
    """
    import time as _time

    if timer is not None:
        get_elapsed = timer
    elif hasattr(ctx, "clock") and not isinstance(ctx.clock, SystemClock):
        start_dt = ctx.clock.now()

        def _get_elapsed_clock() -> float:
            return (ctx.clock.now() - start_dt).total_seconds()

        get_elapsed = _get_elapsed_clock
    else:
        start_mono = _time.monotonic()

        def _get_elapsed_mono() -> float:
            return _time.monotonic() - start_mono

        get_elapsed = _get_elapsed_mono

    try:
        if 8.0 - get_elapsed() <= 0:
            return 0

        cfg = load_settings()
        hook_action = cfg.autopilot.hook_action

        accounts = ctx.store.load()
        if not accounts:
            return 0

        # Check if any account needs a network fetch
        cache_path = ctx.store.root / "usage.json"
        cache = UsageCache(cache_path)
        now_dt = ctx.clock.now()
        live_blob = ctx.vault.read(live_target())
        active_acc = find_active(accounts, live_blob)

        fetch_needed = False
        for acc in accounts:
            if acc.quarantined is not None:
                continue
            cached = cache.get(acc.fp)
            if cached is None:
                fetch_needed = True
                break
            if cached.backoff_until is not None and now_dt < cached.backoff_until:
                continue
            is_active = active_acc is not None and acc.slot == active_acc.slot
            near_limit = False
            if cached.snapshot:
                near_limit = any(
                    b.remaining < NEAR_LIMIT for p in cached.snapshot.pools for b in p.buckets
                )
            if is_stale(cached, now_dt, active=is_active, near_limit=near_limit):
                fetch_needed = True
                break

        remaining = 8.0 - get_elapsed()
        if remaining <= 0:
            return 0

        # A skip if data refresh would exceed it: use cache only when any fetch
        # would be needed and the remaining budget < 5 s
        cache_only = fetch_needed and remaining < 5.0

        policy_settings = PolicySettings(
            threshold=cfg.autopilot.threshold,
            margin=cfg.autopilot.margin,
            cooldown_s=cfg.autopilot.cooldown,
            strategy=cfg.autopilot.strategy,
            focus=cfg.autopilot.focus,
        )

        force = hook_action == "switch"
        dry_run = hook_action == "notify"

        decision = tick(
            ctx,
            policy_settings,
            dry_run=dry_run,
            force=force,
            cache_only=cache_only,
        )

        if decision.kind == "switch":
            if hook_action == "notify":
                target = decision.target_slot
                msg = (
                    f"mswap: account {target} is better now. "
                    "Run `mswap switch --resume` after this turn."
                )
                print(msg, file=ctx.out, flush=True)
            else:
                print(
                    f"mswap: switched to account {decision.target_slot}.",
                    file=ctx.out,
                    flush=True,
                )

        ctx.events.emit(
            f"hook_{decision.kind}",
            reason=decision.reason,
            from_slot=active_acc.slot if active_acc else None,
            to_slot=decision.target_slot,
            hook_action=hook_action,
        )

    except Exception as e:
        with contextlib.suppress(Exception):
            ctx.events.emit("error", reason=f"hook: {e}")

    return 0
