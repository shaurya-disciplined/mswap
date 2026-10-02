"""Quoting for the command lines mswap hands to other programs.

mswap writes an executable path into three places that something else later parses: agy's
``hooks.json`` (a shell command string), the Windows scheduled task (``schtasks /TR``) and the
Windows ``mswap.cmd`` shim. A path with a space, a quote, ``$`` or a backtick must stay one
argument and must never become extra commands. Systemd unit files have their own rules, which
live next to the unit writer in ``schedule_posix``.

Only imports ``core.errors`` from mswap (util layer rule).
"""

from __future__ import annotations

import shlex

from mswap.core.errors import UsageError

# How every background launcher starts mswap: `-P` keeps the current directory out of sys.path.
# Without it, `python -m mswap` imports an `mswap.py` or `mswap/` found in whatever directory
# agy (hook), the scheduler or the user's shell happens to be in, e.g. a freshly cloned repo.
RUN_MODULE = "-P -m mswap"
RUN_MODULE_ARGS = ("-P", "-m", "mswap")

_WINDOWS_FORBIDDEN = ('"', "\r", "\n", "\0")


def quote_windows(path: str) -> str:
    """Return ``path`` wrapped in double quotes for a Windows command line.

    NTFS cannot hold ``"`` or control characters in a path, so a path with one is not a real
    path. Refuse it rather than guess at an escape that cmd.exe and CommandLineToArgvW read
    differently.
    """
    if any(ch in path for ch in _WINDOWS_FORBIDDEN):
        raise UsageError(
            "This Python's path contains a quote or a line break, so it can't be written "
            "into a command line safely.",
            hint="Install mswap under a path without quotes, e.g. with `uv tool install mswap`.",
        )
    return f'"{path}"'


def quote_posix(path: str) -> str:
    """Return ``path`` quoted for a POSIX ``sh`` command line (``shlex.quote``)."""
    return shlex.quote(path)


def quote_cmd_batch(path: str) -> str:
    """Return ``path`` quoted for a ``.cmd`` batch file: double quotes, ``%`` doubled.

    A batch file expands ``%NAME%`` even inside double quotes, so a literal ``%`` must be
    written as ``%%``.
    """
    return quote_windows(path).replace("%", "%%")


def quote_command_path(path: str, *, windows: bool) -> str:
    """Quote ``path`` as the program part of a shell command for the given platform."""
    return quote_windows(path) if windows else quote_posix(path)
