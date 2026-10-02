"""Command to switch the active Antigravity account."""

from __future__ import annotations

import argparse

from mswap.agy.paths import agy_exe
from mswap.cli.context import AppContext
from mswap.core.errors import NothingToDo, UnsafeOperation, UsageError
from mswap.core.switcher import recover, switch
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the switch command."""
    recovery_msg = recover(ctx)
    if recovery_msg:
        print(ctx.theme.dim(recovery_msg), file=ctx.err)

    force = getattr(args, "force", False) if isinstance(args, argparse.Namespace) else False
    wait = getattr(args, "wait", False) if isinstance(args, argparse.Namespace) else False
    wait_timeout = (
        float(getattr(args, "wait_timeout", 0.0)) if isinstance(args, argparse.Namespace) else 0.0
    )
    resume = getattr(args, "resume", False) if isinstance(args, argparse.Namespace) else False
    selector = (
        getattr(args, "selector", None)
        if isinstance(args, argparse.Namespace)
        else (args[0] if args else None)
    )

    if resume and ctx.json:
        raise UsageError(
            "Cannot use --resume with --json.",
            hint="Run `mswap switch --resume` without --json to resume interactively.",
        )

    is_inside = ctx.inside_agy() if callable(getattr(ctx, "inside_agy", None)) else False
    if is_inside:
        if not force:
            raise UnsafeOperation(
                "You're running mswap inside agy. "
                "Switching changes the login this agy session uses.",
                hint="Run with --force to switch anyway.",
            )
        print(
            ctx.theme.warn(
                "! You're running mswap inside agy. "
                "Switching changes the login this agy session uses."
            ),
            file=ctx.err,
        )

    if wait:
        procs_fn = getattr(ctx, "procs", None)
        running = procs_fn() if callable(procs_fn) else []
        if running:
            if not ctx.quiet:
                print("Waiting for agy to exit… (Ctrl+C to cancel)", file=ctx.err)
            start_time = ctx.clock.now()
            while True:
                current_procs = procs_fn() if callable(procs_fn) else []
                if not current_procs:
                    break
                elapsed = (ctx.clock.now() - start_time).total_seconds()
                if wait_timeout > 0 and elapsed >= wait_timeout:
                    raise NothingToDo(
                        "agy is still running.",
                        hint="Close it or drop --wait-timeout.",
                    )
                ctx.sleep(2.0)
                elapsed = (ctx.clock.now() - start_time).total_seconds()
                if wait_timeout > 0 and elapsed >= wait_timeout:
                    remaining_procs = procs_fn() if callable(procs_fn) else []
                    if remaining_procs:
                        raise NothingToDo(
                            "agy is still running.",
                            hint="Close it or drop --wait-timeout.",
                        )
                    break

    result = switch(ctx, selector, force=force)

    if ctx.json:
        data = {
            "status": result.status,
            "from_slot": result.from_account.slot if result.from_account else None,
            "to_slot": result.to_account.slot,
            "agy_running": result.agy_running,
        }
        print(jsonout.ok("switch", data), file=ctx.out)
        return 0

    if result.status == "already_active":
        print(
            f"Already on account {result.to_account.slot}: {result.to_account.email}",
            file=ctx.out,
        )
        if resume:
            if is_inside:
                raise UsageError(
                    "Can't resume from inside agy.",
                    hint="Run `mswap switch --resume` in a normal terminal.",
                )
            return ctx.runner([str(agy_exe()), "-c"])
        return 0

    ok_mark = ctx.theme.ok(ctx.theme.glyph_ok)
    slot_str = ctx.theme.bold(str(result.to_account.slot))
    print(f"{ok_mark} Switched agy to account {slot_str}: {result.to_account.email}", file=ctx.out)

    if result.agy_running:
        procs_fn = getattr(ctx, "procs", None)
        procs_list = procs_fn() if callable(procs_fn) else []
        n = len(procs_list)
        warn_msg = (
            f"! agy is running ({n} session(s)). Those sessions may keep using the old "
            "account until restarted."
        )
        print(ctx.theme.warn(warn_msg), file=ctx.out)
        dim_hint = (
            "  Finish the current turn, quit agy, then run `agy -c` to continue the same "
            "conversation on the new account."
        )
        print(ctx.theme.dim(dim_hint), file=ctx.out)
    else:
        restart_hint = ctx.theme.dim(
            "  New agy sessions use it. Restart any agy that's already running."
        )
        print(restart_hint, file=ctx.out)

    if resume:
        if is_inside:
            raise UsageError(
                "Can't resume from inside agy.",
                hint="Run `mswap switch --resume` in a normal terminal.",
            )
        return ctx.runner([str(agy_exe()), "-c"])

    return 0
