"""CLI command for ``mswap schedule install|remove|status [--every MIN]``.

Registers a per-user Windows scheduled task that runs ``mswap auto --once``
silently every N minutes with no console window flashing.

Non-Windows platforms get a clear error with a hint about future support.
"""

from __future__ import annotations

import argparse
import sys

from mswap.cli.context import AppContext
from mswap.core.errors import UsageError


def _check_platform() -> None:
    """Raise ``UsageError`` on non-Windows platforms."""
    if sys.platform != "win32":
        raise UsageError(
            "Scheduling is Windows-only for now.",
            hint="On macOS/Linux, run `mswap auto` in a terminal "
            "(launchd/systemd support arrives in v0.6).",
        )


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the ``mswap schedule`` command."""
    action: str | None = getattr(args, "schedule_action", None)

    if not action:
        raise UsageError(
            "Missing schedule action.",
            hint="Run `mswap schedule install`, `mswap schedule remove`, "
            "or `mswap schedule status`.",
        )

    _check_platform()

    from mswap.util import schedule_win

    if action == "install":
        every: int = getattr(args, "every", 5) or 5
        msg = schedule_win.install(every=every)
        print(msg, file=ctx.out)
        return 0

    if action == "remove":
        msg = schedule_win.remove()
        print(msg, file=ctx.out)
        return 0

    if action == "status":
        return _run_status(ctx)

    raise UsageError(f"Unknown schedule action: {action}")


def _run_status(ctx: AppContext) -> int:
    """Display scheduled task status and recent autopilot events."""
    from mswap.util import schedule_win

    task = schedule_win.query()

    if not task["installed"]:
        print("Not installed.", file=ctx.out)
        print(
            ctx.theme.dim(
                "  \u2192 Run `mswap schedule install --every 5` to set up background autopilot."
            ),
            file=ctx.out,
        )
        return 0

    print(f'Scheduled task "{schedule_win.TASK_NAME}":', file=ctx.out)
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
