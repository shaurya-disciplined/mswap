"""Unit tests for WindowsVault."""

from __future__ import annotations

import sys

import pytest

from mswap.core.errors import VaultError
from mswap.vault.windows import WindowsVault


def test_windows_vault_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(VaultError, match="only available on Windows"):
        WindowsVault()


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_vault_max_blob() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    vault = WindowsVault()
    with pytest.raises(VaultError, match="exceeds maximum"):
        vault.write("mswaptest:oversized", b"x" * 2561, "user")


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_vault_roundtrip() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    vault = WindowsVault()
    target = "mswaptest:temp_test_key"
    try:
        vault.write(target, b"test-blob-123", "alice@example.com")
        assert vault.read(target) == b"test-blob-123"
        assert target in vault.list("mswaptest:")
        assert vault.delete(target) is True
        assert vault.read(target) is None
    finally:
        vault.delete(target)
