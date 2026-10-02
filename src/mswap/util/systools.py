"""Run OS-supplied tools (schtasks, tasklist, icacls, ps, security, launchctl, ...) safely.

A bare program name is resolved by the operating system, and on Windows that search starts in
the *current directory*: a ``schtasks.exe`` dropped into a cloned repository would run the next
time mswap is started from inside it. On macOS the ``security`` tool also receives the saved
login on stdin. So these tools are looked up in the fixed system directories first and only
fall back to ``PATH`` when they are not there.

Only imports the standard library (util layer rule).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_POSIX_SYSTEM_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def system_tool(name: str) -> str:
    """Return the absolute path of the OS tool `name`, or `name` itself when not found."""
    if sys.platform == "win32":
        root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
        candidate = Path(root) / "System32" / f"{name}.exe"
        return str(candidate) if candidate.is_file() else name
    for directory in _POSIX_SYSTEM_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name) or name


def run_system(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """`subprocess.run` for `cmd`, with the program (`cmd[0]`) resolved by `system_tool`."""
    return subprocess.run(  # noqa: S603 - program resolved from system directories, no shell
        [system_tool(cmd[0]), *cmd[1:]], **kwargs
    )
