"""Filesystem paths for Antigravity CLI and mswap data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def agy_exe() -> Path:
    """Return path to agy executable."""
    env_exe = os.environ.get("MSWAP_AGY_EXE")
    if env_exe:
        return Path(env_exe)
    return Path(os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe"))


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
