"""Unit tests for agy client discovery and binary scanning."""

from __future__ import annotations

import mmap
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mswap.agy.client_discovery import load_client


def test_client_discovery_scan_and_cache(tmp_path: Path) -> None:
    # 1. Build a fake exe binary containing client ID and two secrets
    fake_client_id = "1071006060591-" + "a" * 32 + ".apps.googleusercontent.com"
    fake_secret_1 = "GOCSPX-FAKE" + "1" * 24
    fake_secret_2 = "GOCSPX-FAKE" + "2" * 24

    binary_data = (
        b"BINARY_START\x00"
        + fake_client_id.encode("utf-8")
        + b"\x00PADDING\x00"
        + fake_secret_1.encode("utf-8")
        + b"\x00MORE_PADDING\x00"
        + fake_secret_2.encode("utf-8")
        + b"\x00BINARY_END"
    )

    exe_file = tmp_path / "agy.exe"
    exe_file.write_bytes(binary_data)
    config_file = tmp_path / "config.json"

    # 2. First call: scans the binary and caches the result
    cfg = load_client(exe_path=exe_file, config_path=config_file)

    assert cfg["client_id"] == fake_client_id
    assert cfg["secrets"] == sorted([fake_secret_1, fake_secret_2])
    assert config_file.exists()

    # 3. Second call: hits cache without rescanning (mmap is not called)
    orig_mmap = mmap.mmap
    scan_count = 0

    def counting_mmap(*args: Any, **kwargs: Any) -> Any:
        nonlocal scan_count
        scan_count += 1
        return orig_mmap(*args, **kwargs)

    with patch("mmap.mmap", side_effect=counting_mmap):
        cfg2 = load_client(exe_path=exe_file, config_path=config_file)
        assert cfg2["client_id"] == fake_client_id
        assert scan_count == 0

    # 4. Modifying file mtime forces a rescan
    new_mtime = exe_file.stat().st_mtime + 50.0
    os.utime(exe_file, (new_mtime, new_mtime))

    with patch("mmap.mmap", side_effect=counting_mmap):
        cfg3 = load_client(exe_path=exe_file, config_path=config_file)
        assert cfg3["client_id"] == fake_client_id
        assert scan_count == 1
