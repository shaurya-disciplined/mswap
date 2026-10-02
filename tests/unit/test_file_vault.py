"""Unit tests for FileVault backend."""

from __future__ import annotations

import base64
import hashlib
import json
import stat
import sys
from pathlib import Path

import pytest

from mswap.core.errors import UsageError, VaultError
from mswap.vault import (
    get_vault,
    reset_file_vault_warned,
    reset_memory_vault,
)
from mswap.vault.file import FileVault


def test_file_vault_refuses_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(UsageError, match="File vault is not supported on Windows"):
        FileVault(root=tmp_path)


def test_file_vault_hash_naming_and_index(tmp_path: Path) -> None:
    vault = FileVault(root=tmp_path, _allow_windows=True)
    target = "mswap:slot1"
    payload = b"secret-account-token"

    vault.write(target, payload, "alice@example.com")

    expected_hash = hashlib.sha256(target.encode("utf-8")).hexdigest()[:32]
    target_file = tmp_path / expected_hash
    assert target_file.is_file()

    # Content is base64 encoded
    content = target_file.read_bytes()
    assert base64.b64decode(content) == payload

    # Index mapping exists and maps hash -> target
    index_file = tmp_path / "index.json"
    assert index_file.is_file()
    index_data = json.loads(index_file.read_text(encoding="utf-8"))
    assert index_data.get(expected_hash) == target


def test_file_vault_round_trip(tmp_path: Path) -> None:
    vault = FileVault(root=tmp_path, _allow_windows=True)
    target = "mswap:slot2"
    blob = b"sample-round-trip-blob"
    vault.write(target, blob, "user@example.com")

    assert vault.read(target) == blob
    assert vault.read("mswap:nonexistent") is None


def test_file_vault_binary_safe(tmp_path: Path) -> None:
    vault = FileVault(root=tmp_path, _allow_windows=True)
    target = "mswap:binary"
    all_bytes = bytes(range(256))
    vault.write(target, all_bytes, "binary@example.com")

    assert vault.read(target) == all_bytes


def test_file_vault_overwrite(tmp_path: Path) -> None:
    vault = FileVault(root=tmp_path, _allow_windows=True)
    target = "mswap:overwrite"
    vault.write(target, b"first-payload", "user1@example.com")
    assert vault.read(target) == b"first-payload"

    vault.write(target, b"second-payload", "user2@example.com")
    assert vault.read(target) == b"second-payload"


def test_file_vault_delete(tmp_path: Path) -> None:
    vault = FileVault(root=tmp_path, _allow_windows=True)
    target = "mswap:to_delete"

    # Missing delete returns False
    assert vault.delete(target) is False

    vault.write(target, b"payload", "user@example.com")
    h = hashlib.sha256(target.encode("utf-8")).hexdigest()[:32]
    assert (tmp_path / h).is_file()

    # Existing delete returns True
    assert vault.delete(target) is True
    assert not (tmp_path / h).exists()
    assert vault.read(target) is None

    # Deleting again returns False
    assert vault.delete(target) is False


def test_file_vault_list_and_prefix(tmp_path: Path) -> None:
    vault = FileVault(root=tmp_path, _allow_windows=True)
    t1 = "mswap:slot1"
    t2 = "mswap:slot2"
    t3 = "mswap:backup-last"
    t4 = "other:slot1"

    vault.write(t2, b"b", "user@example.com")
    vault.write(t1, b"a", "user@example.com")
    vault.write(t3, b"c", "user@example.com")
    vault.write(t4, b"d", "user@example.com")

    assert vault.list("mswap:") == [t3, t1, t2]
    assert vault.list("mswap:slot") == [t1, t2]
    assert vault.list("other:") == [t4]
    assert vault.list("nonexistent:") == []


def test_file_vault_blob_size_checks(tmp_path: Path) -> None:
    vault = FileVault(root=tmp_path, _allow_windows=True)
    with pytest.raises(VaultError, match="cannot be empty"):
        vault.write("mswap:empty", b"", "user")

    with pytest.raises(VaultError, match="too large"):
        vault.write("mswap:oversized", b"x" * 2561, "user")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only apply on POSIX")
def test_file_vault_posix_permissions(tmp_path: Path) -> None:
    vault_dir = tmp_path / "secure_vault"
    vault = FileVault(root=vault_dir)
    target = "mswap:slot1"
    vault.write(target, b"secret-payload", "user@example.com")

    # Directory must be 0700
    dir_stat = vault_dir.stat()
    assert stat.S_IMODE(dir_stat.st_mode) == 0o700

    # Secret file must be 0600
    h = hashlib.sha256(target.encode("utf-8")).hexdigest()[:32]
    file_stat = (vault_dir / h).stat()
    assert stat.S_IMODE(file_stat.st_mode) == 0o600

    # Index file must be 0600
    index_stat = (vault_dir / "index.json").stat()
    assert stat.S_IMODE(index_stat.st_mode) == 0o600


def test_get_vault_file_warning_and_windows_guard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("MSWAP_VAULT", "file")
    reset_file_vault_warned()
    reset_memory_vault()

    # On Windows: raises UsageError
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(UsageError, match="File vault is not supported on Windows"):
        get_vault()

    # On Linux: emits warning once
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("MSWAP_HOME", str(tmp_path))
    reset_file_vault_warned()

    _ = get_vault()
    captured = capsys.readouterr()
    assert "! Using the file vault: logins are stored unencrypted in" in captured.err

    # Second call should not warn again
    _ = get_vault()
    captured2 = capsys.readouterr()
    assert captured2.err == ""
