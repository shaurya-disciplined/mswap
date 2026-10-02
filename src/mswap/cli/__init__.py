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

from mswap import __version__
from mswap.cli.context import AppContext, get_context
from mswap.cli.parser import build_parser
from mswap.core.errors import INTERNAL_ERROR_CODE, MswapError, UsageError
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
  mswap status [--format FMT]       cache-only one-liner for shell prompts
  mswap watch                       live dashboard
  mswap auto [--once] [--dry-run]   foreground autopilot loop
  mswap hook install|remove|status  manage agy Stop-hook for autopilot
  mswap schedule install|remove|status  background autopilot via Task Scheduler
  mswap shim install [--force]      write a Smart App Control-safe mswap.cmd
  mswap log [-n 20]                 show audit trail of switches and autopilot
  mswap config [get|set|unset|path|list] manage settings.toml
  mswap export FILE [--accounts S]  export accounts to an encrypted bundle
  mswap import FILE [--force]       import accounts from an encrypted bundle
  mswap completions SHELL           print a completion script (powershell|bash|zsh|fish)
  mswap doctor [--repair] [--online] diagnose environment and accounts
  mswap debug record [--out DIR]    save secret-free API response shapes for a bug report
"""


def _dispatch_command(cmd_name: str, app_ctx: AppContext, parsed: argparse.Namespace) -> int:
    """Lazily load and execute command runner."""
    match cmd_name:
        case "add":
            from mswap.cli.commands import add

            return int(add.run(app_ctx, parsed))
        case "list" | "ls":
            from mswap.cli.commands import list_

            return int(list_.run(app_ctx, parsed))
        case "switch":
            from mswap.cli.commands import switch

            return int(switch.run(app_ctx, parsed))
        case "remove":
            from mswap.cli.commands import remove

            return int(remove.run(app_ctx, parsed))
        case "alias":
            from mswap.cli.commands import alias

            return int(alias.run(app_ctx, parsed))
        case "disable" | "enable":
            from mswap.cli.commands import toggle

            return int(toggle.run(app_ctx, parsed))
        case "current":
            from mswap.cli.commands import current

            return int(current.run(app_ctx, parsed))
        case "status":
            from mswap.cli.commands import status

            return int(status.run(app_ctx, parsed))
        case "watch":
            from mswap.cli.commands import watch

            return int(watch.run(app_ctx, parsed))
        case "auto":
            from mswap.cli.commands import auto

            return int(auto.run(app_ctx, parsed))
        case "hook":
            from mswap.cli.commands import hook

            return int(hook.run(app_ctx, parsed))
        case "schedule":
            from mswap.cli.commands import schedule

            return int(schedule.run(app_ctx, parsed))
        case "shim":
            from mswap.cli.commands import shim

            return int(shim.run(app_ctx, parsed))
        case "log":
            from mswap.cli.commands import log

            return int(log.run(app_ctx, parsed))
        case "export":
            from mswap.cli.commands import export_

            return int(export_.run(app_ctx, parsed))
        case "import":
            from mswap.cli.commands import import_

            return int(import_.run(app_ctx, parsed))
        case "doctor":
            from mswap.cli.commands import doctor

            return int(doctor.run(app_ctx, parsed))
        case "debug":
            from mswap.cli.commands import debug

            return int(debug.run(app_ctx, parsed))
        case "config":
            from mswap.cli.commands import config

            return int(config.run(app_ctx, parsed))
        case "completions":
            from mswap.cli.commands import completions

            return int(completions.run(app_ctx, parsed))
        case "__demo-seed":
            if os.environ.get("MSWAP_DEMO") == "1":
                from mswap.cli.commands import demo_seed

                return int(demo_seed.run(app_ctx, parsed))
            raise UsageError(f"Unknown command: {cmd_name}")
        case _:
            raise UsageError(f"Unknown command: {cmd_name}")


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

    parsed: argparse.Namespace | None = None
    cmd_name = next((arg for arg in args_list if not arg.startswith("-")), "")

    try:
        if args_list[:1] == ["__complete"]:
            # Hidden helper for the completion scripts: kept out of the parser so it never
            # shows in usage text, error messages or the generated scripts.
            from mswap.cli.commands import complete

            return int(complete.run(ctx or get_context(None), args_list[1:]))

        parser = build_parser()
        parsed = parser.parse_args(args_list)

        if getattr(parsed, "version", False):
            try:
                import json

                from mswap.agy.install import agy_version
                from mswap.agy.paths import agy_exe, data_dir

                cache = {}
                client_path = data_dir() / "client.json"
                if client_path.exists():
                    try:
                        with open(client_path, encoding="utf-8") as f:
                            cache = json.load(f)
                    except Exception:
                        pass

                av = agy_version(agy_exe(), cache)
                agy_part = f" (agy {av})" if av and av != "0.0.0" else " (agy not found)"
            except Exception:
                agy_part = " (agy not found)"

            print(f"mswap {__version__}{agy_part} · not affiliated with Google")
            return 0

        app_ctx = ctx or get_context(parsed)
        if getattr(parsed, "json", False):
            app_ctx.json = True
        if getattr(parsed, "quiet", False):
            app_ctx.quiet = True

        if not parsed.command:
            accounts = app_ctx.store.load()
            if len(accounts) >= 1 or getattr(parsed, "json", False):
                from mswap.cli.commands import list_

                return int(list_.run(app_ctx, parsed))
            print(HELP_TEXT)
            return 0

        cmd_name = str(parsed.command)
        return _dispatch_command(cmd_name, app_ctx, parsed)

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
