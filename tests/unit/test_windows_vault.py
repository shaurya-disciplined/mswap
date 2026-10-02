"""Unit tests for WindowsVault and vault selection."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from mswap.core.errors import VaultError
from mswap.vault import get_vault, reset_memory_vault
from mswap.vault.memory import MemoryVault
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
    with pytest.raises(VaultError, match="Credential is too large"):
        vault.write("mswaptest:oversized", b"x" * 2561, "user")


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_vault_empty_blob() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    vault = WindowsVault()
    with pytest.raises(VaultError, match="cannot be empty"):
        vault.write("mswaptest:empty", b"", "user")


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_vault_forbidden_guard() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    vault = WindowsVault()
    assert "gemini:antigravity" in vault.forbidden

    # Test that any target in forbidden raises AssertionError before Win32 calls
    vault.forbidden.add("mswaptest:forbidden_key")
    with pytest.raises(AssertionError, match="forbidden"):
        vault.read("mswaptest:forbidden_key")
    with pytest.raises(AssertionError, match="forbidden"):
        vault.write("mswaptest:forbidden_key", b"test", "user")
    with pytest.raises(AssertionError, match="forbidden"):
        vault.delete("mswaptest:forbidden_key")
    with pytest.raises(AssertionError, match="forbidden"):
        vault._read_user("mswaptest:forbidden_key")


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_vault_write_zeroes_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    vault = WindowsVault()
    memset_calls: list[int] = []
    orig_memset = vault._ctypes.memset

    def tracking_memset(buf: Any, val: int, size: int) -> Any:
        memset_calls.append(size)
        return orig_memset(buf, val, size)

    monkeypatch.setattr(vault._ctypes, "memset", tracking_memset)

    target = "mswaptest:zero_buf_test"
    try:
        vault.write(target, b"sensitive-data-12345", "user@example.com")
        assert len(memset_calls) == 1
        assert memset_calls[0] == len(b"sensitive-data-12345")
    finally:
        vault.delete(target)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_vault_error_formatting(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    vault = WindowsVault()

    # Simulate CredWriteW returning False with an error code
    monkeypatch.setattr(vault._adv, "CredWriteW", MagicMock(return_value=False))
    monkeypatch.setattr(vault._ctypes, "get_last_error", MagicMock(return_value=5))  # Access denied

    with pytest.raises(VaultError) as exc_info:
        vault.write("mswaptest:fail", b"blob", "user@example.com")

    err_msg = str(exc_info.value)
    assert "Windows Credential Manager error 5" in err_msg
    # Crucially ensure blob and user are NEVER present in error message
    assert "blob" not in err_msg
    assert "user@example.com" not in err_msg


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


def test_get_vault_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_memory_vault()
    monkeypatch.setenv("MSWAP_VAULT", "memory")
    v1 = get_vault()
    v2 = get_vault()
    assert isinstance(v1, MemoryVault)
    assert v1 is v2
    reset_memory_vault()


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_get_vault_native_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    monkeypatch.setenv("MSWAP_VAULT", "native")
    v = get_vault()
    assert isinstance(v, WindowsVault)


def test_get_vault_native_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "freebsd")
    monkeypatch.setenv("MSWAP_VAULT", "native")
    with pytest.raises(VaultError) as exc_info:
        get_vault()
    assert "No supported credential store on this OS yet." in str(exc_info.value)
    assert exc_info.value.hint == "Only macOS, Windows, and Linux are supported."


def test_get_vault_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_VAULT", "redis")
    with pytest.raises(VaultError, match="Unknown vault backend: redis"):
        get_vault()
