"""Configuration CLI command for managing settings.toml.

Owns `mswap config [get KEY | set KEY VALUE | unset KEY | path | list]`.
Must never store credentials or perform network access.
"""

from __future__ import annotations

import argparse
from typing import Any

from mswap.cli.context import AppContext
from mswap.core.errors import UsageError
from mswap.core.settings import (
    _format_toml_value,
    get_setting,
    list_settings,
    set_setting,
    settings_path,
    unset_setting,
)
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the mswap config command."""
    action = getattr(args, "config_action", None) or "list"
    if action == "list":
        return _list(ctx)
    if action == "get":
        return _get(ctx, getattr(args, "key", None))
    if action == "set":
        return _set(ctx, getattr(args, "key", None), getattr(args, "value", None))
    if action == "unset":
        return _unset(ctx, getattr(args, "key", None))
    if action == "path":
        return _path(ctx)
    raise UsageError(
        f"Unknown config action '{action}'.",
        hint="Run `mswap config [get|set|unset|path|list]`.",
    )


def _path(ctx: AppContext) -> int:
    """Print the path to settings.toml."""
    p = settings_path()
    if ctx.json:
        data: dict[str, Any] = {
            "path": str(p),
            "comments_preserved": False,
        }
        print(jsonout.ok("config", data), file=ctx.out)
    else:
        print(str(p), file=ctx.out)
        print(
            ctx.theme.dim(
                "Note: comments in settings.toml are not preserved "
                "when writing configuration via `mswap config`."
            ),
            file=ctx.out,
        )
    return 0


def _get(ctx: AppContext, key: str | None) -> int:
    """Get the effective value of a configuration key."""
    if not key:
        raise UsageError(
            "Missing key for 'mswap config get'.",
            hint="Run `mswap config get KEY` (e.g. `mswap config get autopilot.threshold`).",
        )
    val, is_default = get_setting(key)
    if ctx.json:
        data: dict[str, Any] = {
            "key": key,
            "value": val,
            "default": is_default,
        }
        print(jsonout.ok("config", data), file=ctx.out)
    else:
        formatted = ("true" if val else "false") if isinstance(val, bool) else str(val)
        print(formatted, file=ctx.out)
    return 0


def _set(ctx: AppContext, key: str | None, value_str: str | None) -> int:
    """Set a configuration key to a new value."""
    if not key:
        raise UsageError(
            "Missing key for 'mswap config set'.",
            hint="Run `mswap config set KEY VALUE` (e.g. `mswap config set ui.ascii true`).",
        )
    if value_str is None:
        raise UsageError(
            f"Missing value for 'mswap config set {key}'.",
            hint=f"Run `mswap config set {key} VALUE`.",
        )
    val = set_setting(key, value_str)
    if ctx.json:
        data: dict[str, Any] = {
            "action": "set",
            "key": key,
            "value": val,
        }
        print(jsonout.ok("config", data), file=ctx.out)
    else:
        formatted = _format_toml_value(val)
        print(f"{ctx.theme.glyph_ok} Set {key} = {formatted}", file=ctx.out)
    return 0


def _unset(ctx: AppContext, key: str | None) -> int:
    """Unset a configuration key back to its default."""
    if not key:
        raise UsageError(
            "Missing key for 'mswap config unset'.",
            hint="Run `mswap config unset KEY` (e.g. `mswap config unset autopilot.threshold`).",
        )
    unset_setting(key)
    if ctx.json:
        data: dict[str, Any] = {
            "action": "unset",
            "key": key,
        }
        print(jsonout.ok("config", data), file=ctx.out)
    else:
        print(f"{ctx.theme.glyph_ok} Unset {key}", file=ctx.out)
    return 0


def _list(ctx: AppContext) -> int:
    """List effective configuration with default markers."""
    items = list_settings()
    if ctx.json:
        defaults = [key for key, _, is_default in items if is_default]
        settings_dict: dict[str, dict[str, Any]] = {}
        for key, val, _ in items:
            sec, field_name = key.split(".", 1) if "." in key else ("other", key)
            if sec not in settings_dict:
                settings_dict[sec] = {}
            settings_dict[sec][field_name] = val
        data: dict[str, Any] = {
            "settings": settings_dict,
            "defaults": defaults,
            "items": [{"key": k, "value": v, "default": d} for k, v, d in items],
            **settings_dict,
        }
        print(jsonout.ok("config", data), file=ctx.out)
    else:
        for key, val, is_default in items:
            formatted_val = _format_toml_value(val)
            marker = f" {ctx.theme.dim('(default)')}" if is_default else ""
            print(f"{key} = {formatted_val}{marker}", file=ctx.out)
    return 0
