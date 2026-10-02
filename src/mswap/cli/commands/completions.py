"""Shell completions CLI command.

Owns `mswap completions powershell|bash|zsh|fish`, which prints a script built from the parser.
Must never perform vault, network or accounts I/O.
"""

from __future__ import annotations

import argparse
import contextlib

from mswap.cli.completions import generate
from mswap.cli.context import AppContext


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Print the completion script for the requested shell to stdout."""
    script = generate(str(args.shell))
    # Scripts for bash, zsh and fish break on CRLF, which Windows text mode would add.
    if hasattr(ctx.out, "reconfigure"):
        with contextlib.suppress(Exception):
            ctx.out.reconfigure(newline="\n")
    ctx.out.write(script)
    return 0
