"""Argument parser construction and validation for mswap CLI.

Owns grammar definition, global options, and subcommand parsers.
Must never execute business logic or perform direct file or vault I/O.
"""

from __future__ import annotations

import argparse
import os
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
    p_add.add_argument(
        "--alias",
        default=None,
        help="human-friendly name for this account",
    )
    p_add.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="force add/sign-out even if inside agy",
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
    p_switch.add_argument(
        "--wait",
        action="store_true",
        default=False,
        help="wait for running agy sessions to exit before switching",
    )
    p_switch.add_argument(
        "--wait-timeout",
        type=float,
        default=0.0,
        help="timeout in seconds when waiting for agy to exit (0 = forever)",
    )
    p_switch.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="continue the conversation in agy after switching",
    )

    # remove
    p_remove = subparsers.add_parser(
        "remove",
        parents=[parent],
        help="remove a saved account",
    )
    p_remove.add_argument("selector", help="slot number, email, or alias")
    p_remove.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="skip confirmation prompt",
    )

    # alias
    p_alias = subparsers.add_parser(
        "alias",
        parents=[parent],
        help="set or clear an account alias",
    )
    p_alias.add_argument("selector", help="slot number, email, or alias")
    p_alias.add_argument(
        "name",
        nargs="?",
        default=None,
        help="alias name",
    )
    p_alias.add_argument(
        "--clear",
        action="store_true",
        default=False,
        help="clear account alias",
    )

    # disable
    p_disable = subparsers.add_parser(
        "disable",
        parents=[parent],
        help="disable account from rotation and autopilot",
    )
    p_disable.add_argument("selector", help="slot number, email, or alias")

    # enable
    p_enable = subparsers.add_parser(
        "enable",
        parents=[parent],
        help="enable a previously disabled account",
    )
    p_enable.add_argument("selector", help="slot number, email, or alias")

    # current
    subparsers.add_parser(
        "current",
        parents=[parent],
        help="show active account",
    )

    # doctor
    p_doctor = subparsers.add_parser(
        "doctor",
        parents=[parent],
        help="diagnose installation, accounts, storage, and network health",
    )
    p_doctor.add_argument(
        "--repair",
        action="store_true",
        default=False,
        help="repair recoverable issues such as interrupted switches and orphan logins",
    )
    p_doctor.add_argument(
        "--online",
        action="store_true",
        default=False,
        help="run network checks for token refresh and quota API",
    )

    if os.environ.get("MSWAP_DEMO") == "1":
        subparsers.add_parser(
            "__demo-seed",
            parents=[parent],
            help="seed fake demo accounts and usage cache",
        )

    return parser
