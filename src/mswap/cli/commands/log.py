"""Audit trail viewer CLI command for mswap.

Owns `mswap log [-n 20] [--json]`, reading events.log and events.log.1
in chronological order (newest last) and displaying structured audit events.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

from mswap.agy.paths import data_dir
from mswap.cli.context import AppContext
from mswap.core.errors import UsageError
from mswap.core.events import read_recent_events
from mswap.ui import jsonout


def format_local_time(at_str: str) -> str:
    """Format an ISO timestamp into local time string YYYY-MM-DD HH:MM:SS."""
    if not at_str:
        return ""
    try:
        dt = datetime.fromisoformat(at_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return at_str


def format_summary(ev: dict[str, Any], *, ascii_only: bool = False) -> str:
    """Format the human summary for an event based on its kind."""
    event = str(ev.get("event", ""))
    arrow = "->" if ascii_only else "→"

    if event == "switch":
        from_slot = ev.get("from_slot")
        to_slot = ev.get("to_slot")
        from_str = str(from_slot) if from_slot is not None else "?"
        to_str = str(to_slot) if to_slot is not None else "?"
        source = ev.get("source")
        is_autopilot = (
            source == "autopilot" or "focus" in ev or "dry_run" in ev or "active_pressure" in ev
        )
        mode = "autopilot" if is_autopilot else "manual"
        return f"{from_str} {arrow} {to_str} ({mode})"

    if event in ("hold", "blocked"):
        return str(ev.get("reason", ""))

    if event == "quarantine":
        slot = ev.get("slot", "?")
        return f"account {slot}"

    if event == "writeback_suspected":
        from_slot = ev.get("from_slot", "?")
        to_slot = ev.get("to_slot", "?")
        return f"after switch {from_slot} {arrow} {to_slot}"

    if event.startswith("hook_"):
        reason = str(ev.get("reason", ""))
        action = ev.get("hook_action")
        target = ev.get("to_slot")
        if target is not None and action:
            if reason:
                return f"target {target} ({action}): {reason}"
            return f"target {target} ({action})"
        return reason

    if event == "sign_out":
        from_fp = ev.get("from_fp")
        if from_fp and isinstance(from_fp, str):
            return f"live sign-out ({from_fp[:8]}...)"
        return "live sign-out"

    return str(ev.get("reason", "") or ev.get("message", "") or "")


def format_line(ev: dict[str, Any], *, ascii_only: bool = False) -> str:
    """Format one audit log line: '{local time} {event:<18} {summary}'."""
    at_str = str(ev.get("at", ""))
    local_time = format_local_time(at_str)
    event_kind = str(ev.get("event", ""))
    summary = format_summary(ev, ascii_only=ascii_only)
    return f"{local_time} {event_kind:<18} {summary}"


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the mswap log command."""
    count = getattr(args, "n", 20)
    if count is None:
        count = 20
    if count < 0:
        raise UsageError("-n must be non-negative.", hint="Pass -n 20.")

    if hasattr(ctx, "events") and hasattr(ctx.events, "path"):
        log_path = ctx.events.path
    else:
        log_path = data_dir() / "events.log"

    events = read_recent_events(log_path, count=count)

    if ctx.json:
        print(jsonout.ok("log", {"events": events}), file=ctx.out)
        return 0

    if not events:
        print("No events logged yet.", file=ctx.out)
        return 0

    ascii_only = getattr(ctx.theme, "ascii_only", False)
    for ev in events:
        line = format_line(ev, ascii_only=ascii_only)
        print(line, file=ctx.out)

    return 0
