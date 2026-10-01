"""Command-line interface for mswap."""

from __future__ import annotations

import contextlib
import sys

from mswap import __version__
from mswap.cli.commands import add, list_, switch
from mswap.cli.context import AppContext, get_context
from mswap.core.errors import MswapError
from mswap.ui.theme import bold, red


def main(argv: list[str] | None = None, ctx: AppContext | None = None) -> int:
    """CLI entrypoint for mswap."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8")

    args_list = sys.argv[1:] if argv is None else argv

    if args_list in (["--version"], ["-V"]):
        print(f"mswap {__version__} · not affiliated with Google")
        return 0

    cmd, cmd_args = (args_list[0], args_list[1:]) if args_list else ("help", [])
    commands = {
        "add": add.run,
        "list": list_.run,
        "ls": list_.run,
        "switch": switch.run,
    }

    if cmd not in commands:
        help_text = f"""{bold("mswap")}: switch Google accounts in agy (Antigravity CLI)

  mswap add            save the account agy is signed in to now
  mswap add --new      save it, then sign agy out here so you can sign in to another
  mswap list           all accounts with 5h / weekly quota left
  mswap switch         rotate to the next account
  mswap switch N       switch to account N (or an email)
"""
        print(help_text)
        return 0 if cmd in ("help", "-h", "--help") else 1

    app_ctx = ctx or get_context()
    try:
        return commands[cmd](app_ctx, cmd_args)
    except MswapError as e:
        print(red("✗ ") + str(e), file=sys.stderr)
        return e.code
