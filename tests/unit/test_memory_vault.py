"""Unit tests for MemoryVault backend."""

from __future__ import annotations

import pytest

from mswap.core.errors import VaultError
from mswap.vault.memory import MemoryVault


def test_round_trip() -> None:
    # Arrange
    vault = MemoryVault()
    target = "mswaptest:slot1"
    blob = b"secret-token-data"
    user = "alice@example.com"

    # Act
    vault.write(target, blob, user)
    result = vault.read(target)

    # Assert
    assert result == blob
    assert vault.entries[target] == (blob, user)


def test_overwrite_replaces_blob_and_user() -> None:
    # Arrange
    vault = MemoryVault()
    target = "mswaptest:slot1"

    # Act
    vault.write(target, b"first-blob", "user1@example.com")
    vault.write(target, b"second-blob", "user2@example.com")
    result = vault.read(target)

    # Assert
    assert result == b"second-blob"
    assert vault.entries[target] == (b"second-blob", "user2@example.com")


def test_read_missing_returns_none() -> None:
    # Arrange
    vault = MemoryVault()

    # Act
    result = vault.read("mswaptest:nonexistent")

    # Assert
    assert result is None


def test_delete_existing_returns_true() -> None:
    # Arrange
    vault = MemoryVault()
    target = "mswaptest:slot1"
    vault.write(target, b"payload", "user@example.com")

    # Act
    deleted = vault.delete(target)

    # Assert
    assert deleted is True
    assert vault.read(target) is None


def test_delete_missing_returns_false() -> None:
    # Arrange
    vault = MemoryVault()

    # Act
    deleted = vault.delete("mswaptest:missing")

    # Assert
    assert deleted is False


def test_list_prefix_sorted_and_filtered() -> None:
    # Arrange
    vault = MemoryVault()
    vault.write("mswaptest:slot2", b"b", "user@example.com")
    vault.write("mswaptest:slot1", b"a", "user@example.com")
    vault.write("mswaptest:slot10", b"c", "user@example.com")
    vault.write("othertarget:slot1", b"d", "user@example.com")

    # Act
    results = vault.list("mswaptest:slot")

    # Assert
    assert results == ["mswaptest:slot1", "mswaptest:slot10", "mswaptest:slot2"]


def test_write_exceeding_max_blob_raises_vault_error() -> None:
    # Arrange
    vault = MemoryVault()
    oversized = b"x" * 2561

    # Act & Assert
    with pytest.raises(VaultError, match="exceeds maximum"):
        vault.write("mswaptest:slot1", oversized, "user@example.com")


def test_fail_on_write() -> None:
    # Arrange
    vault = MemoryVault(fail_on_write=2)
    target = "mswaptest:slot1"

    # Act & Assert
    # 1st write ok
    vault.write(target, b"first", "user@example.com")
    assert vault.read(target) == b"first"

    # 2nd write raises
    with pytest.raises(VaultError, match="simulated failure"):
        vault.write(target, b"second", "user@example.com")

    # 3rd write ok
    vault.write(target, b"third", "user@example.com")
    assert vault.read(target) == b"third"


def test_calls_recorded_in_order() -> None:
    # Arrange
    vault = MemoryVault()

    # Act
    vault.read("mswaptest:missing")
    vault.write("mswaptest:slot1", b"blob", "user@example.com")
    vault.list("mswaptest:")
    vault.delete("mswaptest:slot1")

    # Assert
    assert vault.calls == [
        ("read", "mswaptest:missing"),
        ("write", "mswaptest:slot1"),
        ("list", "mswaptest:"),
        ("delete", "mswaptest:slot1"),
    ]


def test_binary_safe() -> None:
    # Arrange
    vault = MemoryVault()
    all_bytes = bytes(range(256))
    target = "mswaptest:binary"

    # Act
    vault.write(target, all_bytes, "binary@example.com")
    read_back = vault.read(target)

    # Assert
    assert read_back == all_bytes
