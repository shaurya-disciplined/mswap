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

    # status
    p_status = subparsers.add_parser(
        "status",
        parents=[parent],
        help="cache-only one-liner for shell prompts",
    )
    p_status.add_argument(
        "--format",
        default=None,
        help="format string for prompt output",
    )

    # watch
    p_watch = subparsers.add_parser(
        "watch",
        parents=[parent],
        help="live dashboard with keyboard controls",
    )
    p_watch.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="allow switching accounts even if inside agy",
    )

    # auto
    p_auto = subparsers.add_parser(
        "auto",
        parents=[parent],
        help="automatic account switcher based on quota policy",
    )
    p_auto.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="run a single autopilot tick and exit",
    )
    p_auto.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="simulate decisions without switching credentials",
    )
    p_auto.add_argument(
        "--interval",
        type=int,
        default=60,
        metavar="SEC",
        help="seconds between checks (default: 60)",
    )
    p_auto.add_argument(
        "--threshold",
        type=int,
        default=None,
        metavar="N",
        help="quota usage threshold percentage to trigger switch",
    )
    p_auto.add_argument(
        "--strategy",
        choices=["best", "consume-first"],
        default=None,
        metavar="S",
        help="switch strategy: best or consume-first",
    )
    p_auto.add_argument(
        "--focus",
        choices=["auto", "gemini", "3p", "both"],
        default=None,
        metavar="F",
        help="quota focus: auto, gemini, 3p, or both",
    )
    p_auto.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="allow switching accounts even if inside agy",
    )
    p_auto.add_argument(
        "--from-hook",
        action="store_true",
        default=False,
        help="run from an agy Stop hook (8s budget, never raises, always exit 0)",
    )

    # hook
    p_hook = subparsers.add_parser(
        "hook",
        parents=[parent],
        help="manage agy Stop-hook for autopilot",
    )
    p_hook.add_argument(
        "hook_action",
        nargs="?",
        choices=["install", "remove", "status"],
        default=None,
        metavar="ACTION",
        help="install, remove, or show status of the mswap-autopilot hook",
    )

    # schedule
    p_schedule = subparsers.add_parser(
        "schedule",
        parents=[parent],
        help="manage background autopilot via Windows Task Scheduler",
    )
    schedule_sub = p_schedule.add_subparsers(dest="schedule_action")
    p_sched_install = schedule_sub.add_parser(
        "install",
        parents=[parent],
        help="register the autopilot scheduled task",
    )
    p_sched_install.add_argument(
        "--every",
        type=int,
        default=5,
        metavar="MIN",
        help="run autopilot every MIN minutes (1-60, default: 5)",
    )
    schedule_sub.add_parser(
        "remove",
        parents=[parent],
        help="unregister the autopilot scheduled task",
    )
    schedule_sub.add_parser(
        "status",
        parents=[parent],
        help="show the scheduled task status and recent events",
    )

    # shim
    p_shim = subparsers.add_parser(
        "shim",
        parents=[parent],
        help="install a Smart App Control-safe mswap.cmd launcher (Windows)",
    )
    shim_sub = p_shim.add_subparsers(dest="shim_action")
    p_shim_install = shim_sub.add_parser(
        "install",
        parents=[parent],
        help="write mswap.cmd, which runs `python -m mswap`",
    )
    p_shim_install.add_argument(
        "--dir",
        default=None,
        metavar="DIR",
        help=r"directory to write mswap.cmd into (default: %%USERPROFILE%%\.local\bin)",
    )
    p_shim_install.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="replace an existing mswap.cmd that is not an mswap shim (backed up to .bak)",
    )

    # log
    p_log = subparsers.add_parser(
        "log",
        parents=[parent],
        help="show audit trail of account switches and autopilot decisions",
    )
    p_log.add_argument(
        "-n",
        type=int,
        default=20,
        metavar="N",
        help="number of recent events to show (default: 20)",
    )

    # config
    p_config = subparsers.add_parser(
        "config",
        parents=[parent],
        help="read, write, or list user settings in settings.toml",
    )
    p_config.add_argument(
        "config_action",
        nargs="?",
        choices=["get", "set", "unset", "path", "list"],
        default=None,
        metavar="ACTION",
        help="action: get, set, unset, path, or list (default: list)",
    )
    p_config.add_argument(
        "key",
        nargs="?",
        default=None,
        help="configuration key in dotted format (e.g. autopilot.threshold)",
    )
    p_config.add_argument(
        "value",
        nargs="?",
        default=None,
        help="value to set for the configuration key",
    )

    # export
    p_export = subparsers.add_parser(
        "export",
        parents=[parent],
        help="export accounts to an encrypted bundle",
    )
    p_export.add_argument(
        "file",
        metavar="FILE",
        help="path to write the encrypted bundle to",
    )
    p_export.add_argument(
        "--accounts",
        default=None,
        metavar="SEL,...",
        help="comma-separated list of account selectors to export",
    )

    # import
    p_import = subparsers.add_parser(
        "import",
        parents=[parent],
        help="import accounts from an encrypted bundle",
    )
    p_import.add_argument(
        "file",
        metavar="FILE",
        help="path to read the encrypted bundle from",
    )
    p_import.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="overwrite existing accounts if email matches",
    )

    if os.environ.get("MSWAP_DEMO") == "1":
        subparsers.add_parser(
            "__demo-seed",
            parents=[parent],
            help="seed fake demo accounts and usage cache",
        )

    return parser
