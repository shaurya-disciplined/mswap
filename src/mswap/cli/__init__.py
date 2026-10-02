"""Command-line interface and top-level execution handler for mswap.

Owns argument dispatch, exit codes, and top-level error formatting.
Must never allow unredacted credentials to reach stdout or stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import traceback
from typing import Any

from mswap import __version__
from mswap.cli.commands import add, alias, current, list_, remove, switch, toggle
from mswap.cli.context import AppContext, get_context
from mswap.cli.parser import build_parser
from mswap.core.errors import INTERNAL_ERROR_CODE, MswapError
from mswap.ui import jsonout
from mswap.ui.theme import bold, theme_from
from mswap.util.redact import redact

HELP_TEXT = f"""{bold("mswap")}: switch Google accounts in agy (Antigravity CLI)

  mswap add [--new] [--alias NAME]  save the account agy is signed in to now
  mswap list [--refresh]            all accounts with 5h / weekly quota left
  mswap switch [SELECTOR]           rotate to the next account or switch to SELECTOR
  mswap remove SELECTOR [--yes]     remove a saved account
  mswap alias SELECTOR [NAME]       set or clear (--clear) an account alias
  mswap disable|enable SELECTOR     disable or enable an account
  mswap current                     show active account
"""


def main(argv: list[str] | None = None, ctx: AppContext | None = None) -> int:
    """CLI entrypoint for mswap with full error handling and redaction."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8")

    args_list = sys.argv[1:] if argv is None else argv

    if args_list == ["help"] or args_list in (["-h"], ["--help"]):
        print(HELP_TEXT)
        return 0

    commands: dict[str, Any] = {
        "add": add.run,
        "list": list_.run,
        "ls": list_.run,
        "switch": switch.run,
        "remove": remove.run,
        "alias": alias.run,
        "disable": toggle.run,
        "enable": toggle.run,
        "current": current.run,
    }

    parsed: argparse.Namespace | None = None
    cmd_name = next((arg for arg in args_list if not arg.startswith("-")), "")

    try:
        parser = build_parser()
        parsed = parser.parse_args(args_list)

        if getattr(parsed, "version", False):
            print(f"mswap {__version__} · not affiliated with Google")
            return 0

        app_ctx = ctx or get_context(parsed)

        if not parsed.command:
            accounts = app_ctx.store.load()
            if len(accounts) >= 1 or getattr(parsed, "json", False):
                return int(list_.run(app_ctx, parsed))
            print(HELP_TEXT)
            return 0

        cmd_name = str(parsed.command)
        return int(commands[cmd_name](app_ctx, parsed))

    except KeyboardInterrupt:
        return 130

    except MswapError as e:
        is_json = getattr(parsed, "json", False) if parsed is not None else ("--json" in args_list)
        no_color = (
            getattr(parsed, "no_color", False)
            if parsed is not None
            else ("--no-color" in args_list)
        )
        ascii_flag = (
            getattr(parsed, "ascii", False) if parsed is not None else ("--ascii" in args_list)
        )

        if is_json:
            print(jsonout.err(cmd_name, e), file=sys.stdout)
        else:
            theme = theme_from(
                no_color_flag=no_color,
                ascii_flag=ascii_flag,
                isatty=sys.stderr.isatty(),
            )
            print(theme.err(f"{theme.glyph_err} {redact(e.message)}"), file=sys.stderr)
            if e.hint:
                print(f"  → {theme.dim(redact(e.hint))}", file=sys.stderr)
        return e.code

    except Exception:
        err_theme = theme_from(
            no_color_flag="--no-color" in args_list,
            ascii_flag="--ascii" in args_list,
            isatty=sys.stderr.isatty(),
        )
        print(
            f"{err_theme.glyph_err} Internal error (this is a bug). Please report it: "
            "https://github.com/shaurya-disciplined/mswap/issues",
            file=sys.stderr,
        )
        if os.environ.get("MSWAP_DEBUG") == "1":
            tb = traceback.format_exc()
            print(redact(tb), file=sys.stderr)
        return INTERNAL_ERROR_CODE
