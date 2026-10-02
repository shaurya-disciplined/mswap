"""Encrypted account bundle export and import using scrypt and AES-256-GCM.

Owns serialization, cryptographic envelope packing/unpacking, and bundle validation.
Must never log, print, or leak decrypted tokens, keys, or passphrases.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from mswap.core.errors import UsageError


def check_crypto_available() -> None:
    """Verify that the optional cryptography package is available."""
    try:
        import cryptography.hazmat.primitives.ciphers.aead  # noqa: F401
    except ImportError as err:
        raise UsageError(
            "Export needs the optional crypto package.",
            hint='Install with: uv tool install "mswap[export]"',
        ) from err


def get_export_passphrase(
    env: Mapping[str, str],
    *,
    confirm: bool = True,
    getpass_fn: Callable[[str], str] | None = None,
) -> str:
    """Resolve export or import passphrase from environment or interactive prompt.

    Passphrases must be at least 12 characters long.
    """
    if "MSWAP_EXPORT_PASSPHRASE" in env:
        passphrase = env["MSWAP_EXPORT_PASSPHRASE"]
        if len(passphrase) < 12:
            raise UsageError("Passphrase must be at least 12 characters.")
        return passphrase

    import getpass

    prompt_fn = getpass_fn or getpass.getpass
    if confirm:
        p1 = prompt_fn("Enter export passphrase: ")
        p2 = prompt_fn("Confirm export passphrase: ")
        if p1 != p2:
            raise UsageError("Passphrases do not match.")
        if len(p1) < 12:
            raise UsageError("Passphrase must be at least 12 characters.")
        return p1

    return prompt_fn("Enter export passphrase: ")


def _restrict_windows_acl(path: Path) -> None:
    """Best-effort: strip inherited ACEs and grant only the current user read/write."""
    username = os.environ.get("USERNAME")
    if not username:
        return
    with contextlib.suppress(Exception):
        import shutil
        import subprocess

        icacls_bin = shutil.which("icacls") or "icacls"
        subprocess.run(  # noqa: S603 - setting Windows file ACL permissions
            [icacls_bin, str(path), "/inheritance:r", "/grant:r", f"{username}:(R,W)"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def write_secure_file(path: Path, data: str) -> None:
    """Write bundle file with owner-only permissions (Windows icacls / POSIX 0600).

    Permissions are applied to the empty file before any content is written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if sys.platform == "win32":
            os.close(fd)
            fd = -1
            _restrict_windows_acl(path)
            fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
        else:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            f.write(data)
    except Exception:
        if fd != -1:
            os.close(fd)
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        raise


def encrypt_bundle(plaintext_dict: dict[str, Any], passphrase: str) -> dict[str, Any]:
    """Encrypt a plaintext bundle dictionary using scrypt and AES-256-GCM.

    Returns the formatted bundle dictionary ready for JSON serialization.
    """
    check_crypto_available()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = os.urandom(16)
    nonce = os.urandom(12)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    nonce_b64 = base64.b64encode(nonce).decode("ascii")

    header: dict[str, Any] = {
        "format": "mswap-export",
        "version": 1,
        "kdf": {
            "name": "scrypt",
            "n": 32768,
            "r": 8,
            "p": 1,
            "salt": salt_b64,
        },
        "cipher": "AES-256-GCM",
        "nonce": nonce_b64,
    }

    # AAD binds canonical header without ciphertext to prevent parameter tampering
    aad = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")

    key = hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=32768,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )

    plaintext_bytes = json.dumps(plaintext_dict, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )

    aesgcm = AESGCM(key)
    ciphertext_bytes = aesgcm.encrypt(nonce, plaintext_bytes, aad)
    ciphertext_b64 = base64.b64encode(ciphertext_bytes).decode("ascii")

    return {
        **header,
        "ciphertext": ciphertext_b64,
    }


def decrypt_bundle(bundle_dict: dict[str, Any], passphrase: str) -> dict[str, Any]:
    """Decrypt a bundle dictionary and verify authentication tag and AAD.

    Raises UsageError on incorrect passphrase or corrupt/tampered contents.
    """
    check_crypto_available()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if (
        not isinstance(bundle_dict, dict)
        or bundle_dict.get("format") != "mswap-export"
        or bundle_dict.get("version") != 1
        or "kdf" not in bundle_dict
        or not isinstance(bundle_dict["kdf"], dict)
        or bundle_dict["kdf"].get("name") != "scrypt"
        or "salt" not in bundle_dict["kdf"]
        or bundle_dict.get("cipher") != "AES-256-GCM"
        or "nonce" not in bundle_dict
        or "ciphertext" not in bundle_dict
    ):
        raise UsageError("Wrong passphrase or damaged file.")

    try:
        n = int(bundle_dict["kdf"].get("n", 32768))
        r = int(bundle_dict["kdf"].get("r", 8))
        p = int(bundle_dict["kdf"].get("p", 1))
        salt_b64 = str(bundle_dict["kdf"]["salt"])
        nonce_b64 = str(bundle_dict["nonce"])
        ciphertext_b64 = str(bundle_dict["ciphertext"])

        salt = base64.b64decode(salt_b64, validate=True)
        nonce = base64.b64decode(nonce_b64, validate=True)
        ciphertext = base64.b64decode(ciphertext_b64, validate=True)
    except Exception as err:
        raise UsageError("Wrong passphrase or damaged file.") from err

    # Bound attacker-controlled KDF cost so a crafted file cannot stall or exhaust the machine.
    if not (2**14 <= n <= 2**16 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4):
        raise UsageError("Wrong passphrase or damaged file.")

    header: dict[str, Any] = {
        "format": bundle_dict["format"],
        "version": bundle_dict["version"],
        "kdf": {
            "name": bundle_dict["kdf"]["name"],
            "n": n,
            "r": r,
            "p": p,
            "salt": salt_b64,
        },
        "cipher": bundle_dict["cipher"],
        "nonce": nonce_b64,
    }
    aad = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")

    try:
        key = hashlib.scrypt(
            passphrase.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
    except Exception as err:
        raise UsageError("Wrong passphrase or damaged file.") from err

    aesgcm = AESGCM(key)
    try:
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, aad)
    except Exception as err:
        raise UsageError("Wrong passphrase or damaged file.") from err

    try:
        data = json.loads(plaintext_bytes.decode("utf-8"))
        if (
            not isinstance(data, dict)
            or "accounts" not in data
            or not isinstance(data["accounts"], list)
        ):
            raise UsageError("Wrong passphrase or damaged file.")
        return data
    except Exception as err:
        raise UsageError("Wrong passphrase or damaged file.") from err
