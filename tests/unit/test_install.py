"""Unit tests for agy install detection, versioning, and User-Agent."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from mswap.agy.install import agy_version, os_arch, user_agent


def test_os_arch_mappings() -> None:
    cases = [
        ("win32", "AMD64", "windows/amd64"),
        ("win32", "x86_64", "windows/amd64"),
        ("win32", "ARM64", "windows/arm64"),
        ("darwin", "arm64", "darwin/arm64"),
        ("darwin", "x86_64", "darwin/amd64"),
        ("linux", "x86_64", "linux/amd64"),
        ("linux", "aarch64", "linux/arm64"),
    ]
    for sys_plat, machine, expected in cases:
        with patch("sys.platform", sys_plat), patch("platform.machine", return_value=machine):
            assert os_arch() == expected


def test_user_agent_format() -> None:
    ua = user_agent("1.2.12", "windows/amd64")
    assert ua == "antigravity/1.2.12 windows/amd64"


def test_agy_version_cached_hit(tmp_path: Path) -> None:
    fake_exe = tmp_path / "agy.exe"
    fake_exe.write_text("binary content")
    sig = f"{fake_exe.stat().st_size}:{int(fake_exe.stat().st_mtime)}"
    cache = {"exe_sig": sig, "agy_version": "1.2.12"}

    with patch("subprocess.run") as mock_run:
        version = agy_version(fake_exe, cache)
        assert version == "1.2.12"
        mock_run.assert_not_called()


def test_agy_version_calls_binary_on_cache_miss(tmp_path: Path) -> None:
    fake_exe = tmp_path / "agy.exe"
    fake_exe.write_text("binary content")
    cache: dict[str, str] = {}

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = "1.2.13\n"

    with patch("subprocess.run", return_value=completed) as mock_run:
        version = agy_version(fake_exe, cache)
        assert version == "1.2.13"
        mock_run.assert_called_once()


def test_agy_version_fallback_on_subprocess_error(tmp_path: Path) -> None:
    fake_exe = tmp_path / "agy.exe"
    fake_exe.write_text("binary content")
    cache = {"version": "1.2.10"}

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="agy", timeout=15)):
        version = agy_version(fake_exe, cache)
        assert version == "1.2.10"

    # With no cache and error, fall back to "0.0.0"
    with patch("subprocess.run", side_effect=OSError("Exec format error")):
        version_no_cache = agy_version(fake_exe, {})
        assert version_no_cache == "0.0.0"


def test_agy_version_missing_exe_uses_cache_or_default(tmp_path: Path) -> None:
    missing_exe = tmp_path / "nonexistent_agy.exe"
    assert agy_version(missing_exe, {"agy_version": "1.2.12"}) == "1.2.12"
    assert agy_version(missing_exe, {}) == "0.0.0"
