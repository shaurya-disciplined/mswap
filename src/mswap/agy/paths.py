"""Filesystem paths for Antigravity CLI and mswap data."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

from mswap.core.errors import AgyNotFound


def agy_exe() -> Path:
    """Return path to agy executable.

    Resolution order on POSIX:
    1. MSWAP_AGY_EXE environment variable.
    2. shutil.which("agy") in PATH.
    3. First existing candidate among ~/.local/bin/agy, /usr/local/bin/agy,
       /opt/homebrew/bin/agy.
    4. Raise AgyNotFound if none exist.

    Resolution order on Windows:
    1. MSWAP_AGY_EXE environment variable.
    2. %LOCALAPPDATA%\\agy\\bin\\agy.exe
    """
    env_exe = os.environ.get("MSWAP_AGY_EXE")
    if env_exe:
        return Path(env_exe)
    if sys.platform == "win32":
        return Path(os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe"))

    which_path = shutil.which("agy")
    if which_path:
        return Path(which_path)

    candidates = [
        Path.home() / ".local" / "bin" / "agy",
        Path("/usr/local/bin/agy"),
        Path("/opt/homebrew/bin/agy"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise AgyNotFound(
        "Antigravity CLI (agy) executable not found.",
        hint="Install agy, or set MSWAP_AGY_EXE to the binary path.",
    )


def data_dir() -> Path:
    """Return mswap local data directory path (mkdir on first write only)."""
    if "MSWAP_HOME" in os.environ:
        return Path(os.environ["MSWAP_HOME"])
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "mswap"
        return Path.home() / "AppData" / "Local" / "mswap"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mswap"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "mswap"
    return Path.home() / ".local" / "share" / "mswap"


def ensure_data_dir(path: Path | None = None) -> Path:
    """Ensure data directory exists, setting 0700 permissions on POSIX on creation."""
    d = path if path is not None else data_dir()
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            d.chmod(0o700)
    return d
