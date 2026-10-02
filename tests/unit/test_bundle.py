"""Unit tests for encrypted account bundle operations (core/bundle.py)."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mswap.core.bundle import (
    check_crypto_available,
    decrypt_bundle,
    encrypt_bundle,
    get_export_passphrase,
    write_secure_file,
)
from mswap.core.errors import UsageError


def test_check_crypto_available_success() -> None:
    # Act & Assert (cryptography is installed in dev env)
    check_crypto_available()


def test_check_crypto_available_simulated_missing() -> None:
    # Arrange
    with patch.dict(sys.modules, {"cryptography.hazmat.primitives.ciphers.aead": None}):
        # Act & Assert
        with pytest.raises(UsageError) as exc_info:
            check_crypto_available()
        assert exc_info.value.message == "Export needs the optional crypto package."
        assert exc_info.value.hint == 'Install with: uv tool install "mswap[export]"'


def test_get_export_passphrase_from_env() -> None:
    # Arrange
    env = {"MSWAP_EXPORT_PASSPHRASE": "strong-passphrase-1234"}

    # Act
    res = get_export_passphrase(env, confirm=True)

    # Assert
    assert res == "strong-passphrase-1234"


def test_get_export_passphrase_from_env_too_short() -> None:
    # Arrange
    env = {"MSWAP_EXPORT_PASSPHRASE": "short"}

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        get_export_passphrase(env, confirm=True)
    assert exc_info.value.message == "Passphrase must be at least 12 characters."


def test_get_export_passphrase_interactive_mismatch() -> None:
    # Arrange
    inputs = ["strong-passphrase-1", "strong-passphrase-2"]
    call_idx = 0

    def mock_getpass(prompt: str) -> str:
        nonlocal call_idx
        val = inputs[call_idx]
        call_idx += 1
        return val

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        get_export_passphrase({}, confirm=True, getpass_fn=mock_getpass)
    assert exc_info.value.message == "Passphrases do not match."


def test_get_export_passphrase_interactive_too_short() -> None:
    # Arrange
    inputs = ["short-pass", "short-pass"]
    call_idx = 0

    def mock_getpass(prompt: str) -> str:
        nonlocal call_idx
        val = inputs[call_idx]
        call_idx += 1
        return val

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        get_export_passphrase({}, confirm=True, getpass_fn=mock_getpass)
    assert exc_info.value.message == "Passphrase must be at least 12 characters."


def test_get_export_passphrase_interactive_success() -> None:
    # Arrange
    inputs = ["valid-passphrase-1234", "valid-passphrase-1234"]
    call_idx = 0

    def mock_getpass(prompt: str) -> str:
        nonlocal call_idx
        val = inputs[call_idx]
        call_idx += 1
        return val

    # Act
    res = get_export_passphrase({}, confirm=True, getpass_fn=mock_getpass)

    # Assert
    assert res == "valid-passphrase-1234"


def test_get_export_passphrase_interactive_no_confirm() -> None:
    # Arrange
    def mock_getpass(prompt: str) -> str:
        return "single-prompt-passphrase-123"

    # Act
    res = get_export_passphrase({}, confirm=False, getpass_fn=mock_getpass)

    # Assert
    assert res == "single-prompt-passphrase-123"


def test_bundle_encrypt_decrypt_round_trip() -> None:
    # Arrange
    plaintext = {
        "exported_at": "2026-10-02T12:00:00Z",
        "mswap_version": "0.6.0",
        "accounts": [
            {"slot": 1, "email": "user1@example.com", "blob": "dGVzdC1ibG9i-1"},
            {"slot": 2, "email": "user2@example.com", "blob": "dGVzdC1ibG9i-2"},
        ],
    }
    passphrase = "my-super-secret-passphrase-2026"

    # Act
    bundle = encrypt_bundle(plaintext, passphrase)

    # Assert bundle envelope structure
    assert bundle["format"] == "mswap-export"
    assert bundle["version"] == 1
    assert bundle["cipher"] == "AES-256-GCM"
    assert bundle["kdf"]["name"] == "scrypt"
    assert bundle["kdf"]["n"] == 32768
    assert bundle["kdf"]["r"] == 8
    assert bundle["kdf"]["p"] == 1
    assert len(base64.b64decode(bundle["kdf"]["salt"])) == 16
    assert len(base64.b64decode(bundle["nonce"])) == 12
    assert "ciphertext" in bundle

    # Decrypt
    decrypted = decrypt_bundle(bundle, passphrase)
    assert decrypted == plaintext


def test_bundle_decrypt_wrong_passphrase() -> None:
    # Arrange
    plaintext = {"exported_at": "2026-10-02T12:00:00Z", "accounts": []}
    bundle = encrypt_bundle(plaintext, "correct-passphrase-123")

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        decrypt_bundle(bundle, "wrong-passphrase-456")
    assert exc_info.value.message == "Wrong passphrase or damaged file."


def test_bundle_tampered_ciphertext() -> None:
    # Arrange
    plaintext = {"exported_at": "2026-10-02T12:00:00Z", "accounts": []}
    passphrase = "correct-passphrase-123"
    bundle = encrypt_bundle(plaintext, passphrase)

    # Tamper with ciphertext by corrupting bytes
    raw_ct = bytearray(base64.b64decode(bundle["ciphertext"]))
    raw_ct[0] ^= 0xFF
    bundle["ciphertext"] = base64.b64encode(raw_ct).decode("ascii")

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        decrypt_bundle(bundle, passphrase)
    assert exc_info.value.message == "Wrong passphrase or damaged file."


@pytest.mark.parametrize(
    "tamper_field,tamper_val",
    [
        ("version", 2),
        ("cipher", "AES-128-GCM"),
        ("format", "invalid-format"),
    ],
)
def test_bundle_tampered_header_fails(tamper_field: str, tamper_val: object) -> None:
    # Arrange
    plaintext = {"exported_at": "2026-10-02T12:00:00Z", "accounts": []}
    passphrase = "correct-passphrase-123"
    bundle = encrypt_bundle(plaintext, passphrase)

    bundle[tamper_field] = tamper_val

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        decrypt_bundle(bundle, passphrase)
    assert exc_info.value.message == "Wrong passphrase or damaged file."


def test_bundle_tampered_kdf_params() -> None:
    # Arrange
    plaintext = {"exported_at": "2026-10-02T12:00:00Z", "accounts": []}
    passphrase = "correct-passphrase-123"
    bundle = encrypt_bundle(plaintext, passphrase)

    # Change kdf.n to a different value (this changes AAD and derived key)
    bundle["kdf"]["n"] = 16384

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        decrypt_bundle(bundle, passphrase)
    assert exc_info.value.message == "Wrong passphrase or damaged file."


def test_bundle_tampered_nonce() -> None:
    # Arrange
    plaintext = {"exported_at": "2026-10-02T12:00:00Z", "accounts": []}
    passphrase = "correct-passphrase-123"
    bundle = encrypt_bundle(plaintext, passphrase)

    # Change nonce
    raw_nonce = bytearray(base64.b64decode(bundle["nonce"]))
    raw_nonce[0] ^= 0x01
    bundle["nonce"] = base64.b64encode(raw_nonce).decode("ascii")

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        decrypt_bundle(bundle, passphrase)
    assert exc_info.value.message == "Wrong passphrase or damaged file."


def test_bundle_invalid_structure() -> None:
    # Arrange
    passphrase = "correct-passphrase-123"

    # Act & Assert
    with pytest.raises(UsageError) as exc_info:
        decrypt_bundle({"not_a_bundle": True}, passphrase)
    assert exc_info.value.message == "Wrong passphrase or damaged file."


def test_write_secure_file(tmp_path: Path) -> None:
    # Arrange
    target = tmp_path / "sub" / "export.json"
    content = '{"test": true}'

    # Act
    write_secure_file(target, content)

    # Assert
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    "kdf_patch",
    [{"n": 2**20}, {"n": 3 * 2**13}, {"n": 2**13}, {"r": 64}, {"p": 64}, {"n": "x"}],
)
def test_bundle_rejects_out_of_bounds_kdf_params(kdf_patch: dict[str, object]) -> None:
    # Arrange: crafted KDF cost parameters must be refused before any scrypt work
    bundle = encrypt_bundle({"accounts": []}, "correct-passphrase-123")
    bundle["kdf"].update(kdf_patch)

    # Act & Assert
    with pytest.raises(UsageError, match=r"Wrong passphrase or damaged file."):
        decrypt_bundle(bundle, "correct-passphrase-123")


def test_bundle_fresh_salt_and_nonce_each_export() -> None:
    # Arrange & Act
    a = encrypt_bundle({"accounts": []}, "correct-passphrase-123")
    b = encrypt_bundle({"accounts": []}, "correct-passphrase-123")

    # Assert
    assert a["kdf"]["salt"] != b["kdf"]["salt"]
    assert a["nonce"] != b["nonce"]
    assert a["ciphertext"] != b["ciphertext"]
