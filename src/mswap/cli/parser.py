"""Argument parser construction and validation for mswap CLI.

Owns grammar definition, global options, and subcommand parsers.
Must never execute business logic or perform direct file or vault I/O.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import NoReturn

from mswap.core.errors import UsageError


class MswapArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that converts parse errors into UsageError exceptions."""

    def error(self, message: str) -> NoReturn:
        """Override default error method to raise UsageError with exit code 64."""
        raise UsageError(message, hint="Run `mswap --help`.")

    def parse_args(  # type: ignore[override]
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        """Parse arguments and ensure global flags have default boolean values."""
        ns = super().parse_args(args, namespace)
        for flag in ("json", "no_color", "ascii", "quiet", "verbose", "version"):
            if not hasattr(ns, flag):
                setattr(ns, flag, False)
        return ns


def build_parser() -> argparse.ArgumentParser:
    """Build and configure the mswap CLI argument parser."""
    parent = MswapArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="output machine-readable JSON",
    )
    parent.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="disable terminal color output",
    )
    parent.add_argument(
        "--ascii",
        action="store_true",
        default=argparse.SUPPRESS,
        help="use ASCII characters for symbols and bars",
    )
    parent.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="suppress progress and status messages",
    )
    parent.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="enable verbose diagnostic logging",
    )
    parent.add_argument(
        "-V",
        "--version",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show version information and exit",
    )

    parser = MswapArgumentParser(prog="mswap", parents=[parent])
    subparsers = parser.add_subparsers(dest="command")

    # add
    p_add = subparsers.add_parser("add", parents=[parent], help="save current agy account")
    p_add.add_argument(
        "--new",
        action="store_true",
        default=False,
        help="save current account and sign out on this machine",
    )

    # list / ls
    p_list = subparsers.add_parser(
        "list",
        aliases=["ls"],
        parents=[parent],
        help="list all saved accounts and quota",
    )
    p_list.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help="force refresh of quota information from network",
    )

    # switch
    p_switch = subparsers.add_parser(
        "switch",
        parents=[parent],
        help="switch to another account or rotate",
    )
    p_switch.add_argument(
        "selector",
        nargs="?",
        default=None,
        help="slot number or email to switch to",
    )
    p_switch.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="force switch even if unsaved live login is present",
    )

    return parser
