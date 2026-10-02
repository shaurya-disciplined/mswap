"""Hook management CLI command for agy Stop-hook integration.

Owns `mswap hook install|remove|status`, which manages the mswap-autopilot
hook set in agy's hooks.json. Must never install hooks during tests.
"""

from __future__ import annotations

import argparse
from typing import Any

from mswap.agy.hooks import HOOK_SET, hooks_path, install, remove, status
from mswap.cli.context import AppContext
from mswap.core.errors import UsageError
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the mswap hook command."""
    action = getattr(args, "hook_action", None)
    if action is None:
        raise UsageError(
            "Missing hook action.",
            hint="Run `mswap hook install|remove|status`.",
        )

    if action == "install":
        return _install(ctx)
    if action == "remove":
        return _remove(ctx)
    if action == "status":
        return _status(ctx)
    raise UsageError(
        f"Invalid hook action '{action}'.",
        hint="Run `mswap hook install|remove|status`.",
    )


def _install(ctx: AppContext) -> int:
    """Install the mswap-autopilot hook set."""
    changed = install()
    path = hooks_path()

    if ctx.json:
        data: dict[str, Any] = {
            "action": "install",
            "changed": changed,
            "hooks_path": str(path),
            "set_name": HOOK_SET,
        }
        print(jsonout.ok("hook", data), file=ctx.out)
    else:
        if changed:
            ok = ctx.theme.ok("✓")
            print(f"{ok} Installed hook set '{HOOK_SET}'.", file=ctx.out)
        else:
            print(f"Hook set '{HOOK_SET}' is already installed.", file=ctx.out)
        print(f"  hooks.json: {path}", file=ctx.out)
        print(
            ctx.theme.dim("  Your other hooks are untouched."),
            file=ctx.out,
        )
    return 0


def _remove(ctx: AppContext) -> int:
    """Remove the mswap-autopilot hook set."""
    changed = remove()

    if ctx.json:
        data: dict[str, Any] = {"action": "remove", "changed": changed}
        print(jsonout.ok("hook", data), file=ctx.out)
    else:
        if changed:
            ok = ctx.theme.ok("✓")
            print(f"{ok} Removed hook set '{HOOK_SET}'.", file=ctx.out)
        else:
            print(ctx.theme.dim("Not installed."), file=ctx.out)
    return 0


def _status(ctx: AppContext) -> int:
    """Report mswap-autopilot hook status."""
    info = status()

    if ctx.json:
        print(jsonout.ok("hook", info), file=ctx.out)
    else:
        if info["installed"]:
            ok = ctx.theme.ok("✓")
            print(f"{ok} mswap-autopilot hook is installed.", file=ctx.out)
            print(f"  command: {info['command']}", file=ctx.out)
        else:
            print(
                ctx.theme.dim("mswap-autopilot hook is not installed."),
                file=ctx.out,
            )
        print(f"  hooks.json: {info['hooks_path']}", file=ctx.out)
        if info["backup_exists"]:
            print(
                ctx.theme.dim("  backup: hooks.json.mswap-bak exists"),
                file=ctx.out,
            )
    return 0
