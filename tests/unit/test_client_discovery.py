"""Unit tests for agy client discovery and binary scanning."""

from __future__ import annotations

import mmap
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mswap.agy.client_discovery import OAuthClient, discover, load_client, rediscover
from mswap.util.http import FakeHttp, json_response


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


def test_discover_pair_validation_and_cache_hit(tmp_path: Path) -> None:
    fake_client_id = "1071006060591-" + "b" * 32 + ".apps.googleusercontent.com"
    fake_secret_bad = "GOCSPX-" + "B" * 28
    fake_secret_good = "GOCSPX-" + "G" * 28

    binary_data = (
        b"BINARY_START\x00"
        + fake_client_id.encode("utf-8")
        + b"\x00PADDING\x00"
        + fake_secret_bad.encode("utf-8")
        + b"\x00PADDING2\x00"
        + fake_secret_good.encode("utf-8")
        + b"\x00BINARY_END"
    )

    exe_file = tmp_path / "agy.exe"
    exe_file.write_bytes(binary_data)
    cache_file = tmp_path / "client.json"

    http = FakeHttp()
    # FakeHttp responds with 401 invalid_client for fake_secret_bad and 200 for fake_secret_good
    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_client", "error_description": "Bad client secret"},
            status=401,
        ),
        json_response({"access_token": "ya29.FAKE", "expires_in": 3599}, status=200),
    )

    client = discover(exe_file, cache_file, http, sample_refresh_token="1//sample-token")
    assert isinstance(client, OAuthClient)
    assert client.client_id == fake_client_id
    assert client.client_secret == fake_secret_good
    assert cache_file.exists()
    assert len(http.requests) == 2  # 1 bad attempt + 1 successful
    # Second call hits cache: makes NO HTTP calls
    cached_client = discover(exe_file, cache_file, http, sample_refresh_token="1//sample-token")
    assert cached_client == client
    assert len(http.requests) == 2  # No new HTTP requests made on cache hit!


def test_rediscover_clears_secret_and_forces_rescan(tmp_path: Path) -> None:
    fake_client_id = "1071006060591-" + "c" * 32 + ".apps.googleusercontent.com"
    fake_secret = "GOCSPX-" + "S" * 28

    binary_data = (
        b"START\x00"
        + fake_client_id.encode("utf-8")
        + b"\x00PAD\x00"
        + fake_secret.encode("utf-8")
        + b"\x00END"
    )

    exe_file = tmp_path / "agy.exe"
    exe_file.write_bytes(binary_data)
    cache_file = tmp_path / "client.json"

    http = FakeHttp()
    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response({"access_token": "ya29.FAKE", "expires_in": 3599}, status=200),
        json_response({"access_token": "ya29.FAKE", "expires_in": 3599}, status=200),
    )

    client1 = discover(exe_file, cache_file, http, sample_refresh_token="1//sample")
    assert client1.client_secret == fake_secret

    # Rediscover forces rescan and hits the endpoint again
    client2 = rediscover(
        exe=exe_file,
        cache_file=cache_file,
        http=http,
        sample_refresh_token="1//sample",
    )
    assert client2.client_secret == fake_secret
    assert len(http.requests) == 2
