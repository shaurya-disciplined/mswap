"""Filesystem paths for Antigravity CLI."""

from __future__ import annotations

import os
from pathlib import Path


def agy_exe() -> Path:
    """Return path to agy executable."""
    env_exe = os.environ.get("MSWAP_AGY_EXE")
    if env_exe:
        return Path(env_exe)
    return Path(os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe"))
