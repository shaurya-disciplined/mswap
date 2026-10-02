"""Installation detection, binary versioning, and User-Agent construction for agy."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _exe_sig(exe: Path) -> str:
    try:
        resolved = exe.resolve()
        st = resolved.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def agy_version(exe: Path, cache: dict[str, Any]) -> str:
    """Return agy version string, cached by binary signature."""
    cached_version = str(cache.get("agy_version") or cache.get("version") or "")
    if exe.exists():
        sig = _exe_sig(exe)
        if sig and cache.get("exe_sig") == sig and cached_version:
            return cached_version

        try:
            out = subprocess.run(  # noqa: S603 - executing trusted local agy executable
                [str(exe), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass

    return cached_version if cached_version else "0.0.0"


def os_arch() -> str:
    """Return platform and architecture identifier for User-Agent header."""
    p = sys.platform
    if p == "win32":
        os_part = "windows"
    elif p == "darwin":
        os_part = "darwin"
    elif p.startswith("linux"):
        os_part = "linux"
    else:
        os_part = p

    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "x64"):
        arch_part = "amd64"
    elif m in ("arm64", "aarch64"):
        arch_part = "arm64"
    else:
        arch_part = m

    return f"{os_part}/{arch_part}"


def user_agent(version: str, os_arch: str) -> str:
    """Return the required User-Agent string for agy quota and companion APIs."""
    return f"antigravity/{version} {os_arch}"
