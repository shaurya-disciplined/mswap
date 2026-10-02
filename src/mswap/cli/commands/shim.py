"""CLI command for ``mswap shim install [--dir DIR] [--force]``.

Writes a ``mswap.cmd`` launcher that runs ``python -m mswap`` with the interpreter that is
running this command, so Windows Smart App Control never sees an unsigned ``.exe``.
Never touches a file that is not an mswap shim unless --force, and then backs it up first.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from mswap.cli.context import AppContext
from mswap.core.errors import UsageError
from mswap.ui import jsonout
from mswap.util.shellquote import RUN_MODULE, quote_cmd_batch

SHIM_NAME = "mswap.cmd"
SHIM_MARKER = "-m mswap"


def shim_text(python: str) -> str:
    """Return the shim file content (CRLF line ending, as cmd.exe expects)."""
    return f"@{quote_cmd_batch(python)} {RUN_MODULE} %*\r\n"


def default_shim_dir(env: Any) -> Path:
    """Return <USERPROFILE>/.local/bin (falling back to the home directory)."""
    home = env.get("USERPROFILE") or str(Path.home())
    return Path(home) / ".local" / "bin"


def is_mswap_shim(path: Path) -> bool:
    """Return True when the file looks like a launcher written by ``shim install``."""
    try:
        return SHIM_MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p.strip().strip('"')))


def dir_on_path(directory: Path, path_env: str) -> bool:
    """Return True when ``directory`` is one of the entries of ``path_env``."""
    want = _norm(str(directory))
    return any(entry.strip() and _norm(entry) == want for entry in path_env.split(os.pathsep))


def install_shim(directory: Path, python: str, *, force: bool) -> tuple[Path, Path | None]:
    """Write the shim into ``directory``; return (shim path, backup path or None)."""
    target = directory / SHIM_NAME
    backup: Path | None = None
    if target.exists() and not is_mswap_shim(target):
        if not force:
            raise UsageError(
                f"{target} already exists and is not an mswap shim.",
                hint="Re-run with --force to back it up to mswap.cmd.bak and replace it.",
            )
        backup = directory / (SHIM_NAME + ".bak")
        target.replace(backup)
    directory.mkdir(parents=True, exist_ok=True)
    target.write_bytes(shim_text(python).encode("utf-8"))
    return target, backup


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the ``mswap shim`` command."""
    action: str | None = getattr(args, "shim_action", None)
    if action != "install":
        raise UsageError(
            "Missing shim action.",
            hint="Run `mswap shim install`.",
        )
    if sys.platform != "win32" and not ctx.env.get("MSWAP_TEST_FORCE_WINDOWS"):
        raise UsageError(
            "`mswap shim install` is only needed on Windows.",
            hint="On macOS and Linux, `uv tool install mswap` puts a working launcher on PATH.",
        )

    dir_arg: str | None = getattr(args, "dir", None)
    directory = Path(dir_arg) if dir_arg else default_shim_dir(ctx.env)
    force = bool(getattr(args, "force", False))
    target, backup = install_shim(directory, sys.executable, force=force)
    on_path = dir_on_path(directory, ctx.env.get("PATH", ""))

    if ctx.json:
        data: dict[str, Any] = {
            "path": str(target),
            "dir": str(directory),
            "backup": str(backup) if backup is not None else None,
            "on_path": on_path,
        }
        print(jsonout.ok("shim", data), file=ctx.out)
        return 0

    theme = ctx.theme
    if backup is not None:
        print(f"{theme.ok} Backed up the existing file to {backup}", file=ctx.out)
    print(f"{theme.ok} Wrote {target}", file=ctx.out)
    if on_path:
        print(f"{theme.ok} {directory} is on your PATH.", file=ctx.out)
    else:
        print(f"{theme.warn('!')} {directory} is not on your PATH.", file=ctx.out)
        print(
            f"  → {theme.dim(f'Add {directory} to your PATH to run `mswap` from any terminal.')}",
            file=ctx.out,
        )
    return 0
