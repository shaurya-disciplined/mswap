"""CLI command for ``mswap schedule install|remove|status [--every MIN]``.

Registers a per-user background scheduled task that runs ``mswap auto --once``
silently every N minutes.

Supports Windows (Task Scheduler), macOS (launchd), and Linux (systemd user timers).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from mswap.cli.context import AppContext
from mswap.core.errors import UsageError


def _get_scheduler() -> Any:
    """Return platform-appropriate scheduler module."""
    if sys.platform == "win32":
        from mswap.util import schedule_win

        return schedule_win
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        from mswap.util import schedule_posix

        return schedule_posix
    raise UsageError(
        f"Scheduling is not supported on platform '{sys.platform}'.",
        hint="Supported platforms: Windows (Task Scheduler), macOS (launchd), Linux (systemd).",
    )


def _check_platform() -> None:
    """Validate that the current platform supports scheduling."""
    _get_scheduler()


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the ``mswap schedule`` command."""
    action: str | None = getattr(args, "schedule_action", None)

    if not action:
        raise UsageError(
            "Missing schedule action.",
            hint="Run `mswap schedule install`, `mswap schedule remove`, "
            "or `mswap schedule status`.",
        )

    scheduler = _get_scheduler()

    if action == "install":
        every: int = getattr(args, "every", 5) or 5
        msg = scheduler.install(every=every)
        print(msg, file=ctx.out)
        return 0

    if action == "remove":
        msg = scheduler.remove()
        print(msg, file=ctx.out)
        return 0

    if action == "status":
        return _run_status(ctx, scheduler)

    raise UsageError(f"Unknown schedule action: {action}")


def _run_status(ctx: AppContext, scheduler: Any = None) -> int:
    """Display scheduled task status and recent autopilot events."""
    sched = scheduler if scheduler is not None else _get_scheduler()
    task = sched.query()

    if not task["installed"]:
        print("Not installed.", file=ctx.out)
        print(
            ctx.theme.dim(
                "  \u2192 Run `mswap schedule install --every 5` to set up background autopilot."
            ),
            file=ctx.out,
        )
        return 0

    print(f'Scheduled task "{sched.TASK_NAME}":', file=ctx.out)
    if task["status"] is not None:
        print(f"  Status:        {task['status']}", file=ctx.out)
    if task["next_run_time"] is not None:
        print(f"  Next run:      {task['next_run_time']}", file=ctx.out)
    if task["last_run_time"] is not None:
        print(f"  Last run:      {task['last_run_time']}", file=ctx.out)
    if task["last_result"] is not None:
        print(f"  Last result:   {task['last_result']}", file=ctx.out)

    # Show last 3 autopilot events from events.log
    _print_recent_events(ctx)
    return 0


def _print_recent_events(ctx: AppContext) -> int:
    """Print the last 3 autopilot events from the event log."""
    events_path = ctx.events.path
    if not events_path.is_file():
        return 0

    import json

    lines: list[str] = []
    try:
        with events_path.open(encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return 0

    # Filter to autopilot-relevant events and take the last 3
    relevant: list[dict[str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = obj.get("event", "")
        if event in (
            "switch",
            "hold",
            "blocked",
            "writeback_suspected",
            "error",
        ):
            relevant.append(obj)

    if not relevant:
        return 0

    recent = relevant[-3:]
    print("", file=ctx.out)
    print("  Recent autopilot events:", file=ctx.out)
    for ev in recent:
        at = ev.get("at", "?")
        event = ev.get("event", "?")
        reason = ev.get("reason", "")
        summary = reason[:60] + "\u2026" if len(reason) > 60 else reason
        print(f"    {at} {event:<18} {summary}", file=ctx.out)

    return 0
