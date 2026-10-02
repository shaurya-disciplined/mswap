"""macOS Keychain vault backend using the macOS security CLI."""

from __future__ import annotations

import base64
import os
import re
import subprocess
from collections.abc import Callable
from typing import Any

from mswap.core.errors import VaultError
from mswap.vault.base import MAX_BLOB

_VALID_TARGET_CHARS = re.compile(r"^[A-Za-z0-9._:@+-]+$")
_ERR_ITEM_NOT_FOUND = 44
_PREFIX_BASE64 = b"go-keyring-base64:"
_PREFIX_ENCODED = b"go-keyring-encoded:"


class MacKeychainVault:
    """macOS Keychain implementation of Vault Protocol using `security`."""

    def __init__(
        self,
        keychain: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ) -> None:
        kc = keychain if keychain is not None else os.environ.get("MSWAP_MAC_KEYCHAIN")
        if kc is not None and any(ch in kc for ch in ('"', "\n", "\r", ";")):
            raise VaultError(f"Invalid characters in keychain path: {kc}")
        self.keychain = kc
        self._runner = runner

    def split(self, target: str) -> tuple[str, str]:
        """Split target into (service, account) at the first colon."""
        if ":" not in target:
            raise VaultError(
                f"Invalid target '{target}': target must contain ':' to separate "
                "service and account."
            )
        svc, acct = target.split(":", 1)
        if not svc or not acct:
            raise VaultError(
                f"Invalid target '{target}': service and account components cannot be empty."
            )
        if not _VALID_TARGET_CHARS.fullmatch(svc) or not _VALID_TARGET_CHARS.fullmatch(acct):
            raise VaultError(
                f"Invalid target '{target}': only alphanumeric characters and ._:@+- are allowed."
            )
        return svc, acct

    def _kc_args(self) -> list[str]:
        return [self.keychain] if self.keychain else []

    def _read_raw(self, target: str) -> bytes | None:
        svc, acct = self.split(target)
        cmd = ["security", "find-generic-password", "-s", svc, "-a", acct, "-w", *self._kc_args()]
        res = self._runner(cmd, capture_output=True, text=False)
        if res.returncode == _ERR_ITEM_NOT_FOUND:
            return None
        if res.returncode != 0:
            err = (
                res.stderr.decode("utf-8", errors="replace").strip()
                if res.stderr
                else f"exit code {res.returncode}"
            )
            raise VaultError(f"macOS Keychain error reading '{target}': {err}")
        raw: bytes = res.stdout if isinstance(res.stdout, bytes) else bytes(res.stdout or b"")
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        return raw

    def read(self, target: str) -> bytes | None:
        """Read and decode credential blob for target, or None if not found."""
        raw = self._read_raw(target)
        if raw is None:
            return None
        if raw.startswith(_PREFIX_BASE64):
            encoded = raw[len(_PREFIX_BASE64) :]
            try:
                return base64.b64decode(encoded)
            except Exception as e:
                raise VaultError(
                    f"Failed to decode base64 keychain payload for '{target}': {e}"
                ) from e
        if raw.startswith(_PREFIX_ENCODED):
            encoded = raw[len(_PREFIX_ENCODED) :]
            try:
                return bytes.fromhex(encoded.decode("ascii"))
            except Exception as e:
                raise VaultError(
                    f"Failed to decode hex keychain payload for '{target}': {e}"
                ) from e
        return raw

    def write(
        self,
        target: str,
        blob: bytes,
        user: str,  # noqa: ARG002 - Vault protocol parameter; user metadata is kept in accounts.json
    ) -> None:
        """Write credential blob for target using security -i over stdin."""
        if len(blob) == 0:
            raise VaultError("Credential blob cannot be empty.")
        if len(blob) > MAX_BLOB:
            raise VaultError(f"Credential is too large ({len(blob)} bytes, max {MAX_BLOB}).")
        svc, acct = self.split(target)

        live_tgt = os.environ.get("MSWAP_LIVE_TARGET", "gemini:antigravity")
        is_live = target == live_tgt

        if is_live:
            existing_raw = self._read_raw(target)
            if existing_raw is not None and existing_raw.startswith(_PREFIX_BASE64):
                encoded = _PREFIX_BASE64.decode("ascii") + base64.b64encode(blob).decode("ascii")
            elif existing_raw is not None and existing_raw.startswith(_PREFIX_ENCODED):
                encoded = _PREFIX_ENCODED.decode("ascii") + blob.hex()
            elif existing_raw is not None:
                try:
                    encoded = blob.decode("utf-8")
                except UnicodeDecodeError:
                    encoded = _PREFIX_BASE64.decode("ascii") + base64.b64encode(blob).decode(
                        "ascii"
                    )
            else:
                try:
                    encoded = blob.decode("utf-8")
                except UnicodeDecodeError:
                    encoded = _PREFIX_BASE64.decode("ascii") + base64.b64encode(blob).decode(
                        "ascii"
                    )
        else:
            encoded = _PREFIX_BASE64.decode("ascii") + base64.b64encode(blob).decode("ascii")

        kc_arg = f' "{self.keychain}"' if self.keychain else ""
        stdin_cmd = (
            f'add-generic-password -U -s {svc} -a {acct} -l "{svc}:{acct}" -w {encoded}{kc_arg}\n'
        )

        res = self._runner(
            ["security", "-i"],
            input=stdin_cmd.encode("utf-8"),
            capture_output=True,
            text=False,
        )
        if res.returncode != 0:
            err = (
                res.stderr.decode("utf-8", errors="replace").strip()
                if res.stderr
                else f"exit code {res.returncode}"
            )
            raise VaultError(f"macOS Keychain error writing '{target}': {err}")

    def delete(self, target: str) -> bool:
        """Delete credential for target. Return True if deleted, False if not found."""
        svc, acct = self.split(target)
        cmd = ["security", "delete-generic-password", "-s", svc, "-a", acct, *self._kc_args()]
        res = self._runner(cmd, capture_output=True, text=False)
        if res.returncode == 0:
            return True
        if res.returncode == _ERR_ITEM_NOT_FOUND:
            return False
        err = (
            res.stderr.decode("utf-8", errors="replace").strip()
            if res.stderr
            else f"exit code {res.returncode}"
        )
        raise VaultError(f"macOS Keychain error deleting '{target}': {err}")

    def list(self, prefix: str) -> list[str]:
        """List all credential targets matching prefix, sorted."""
        if ":" in prefix:
            _svc, remainder = self.split(prefix + "x")
            _ = remainder[:-1]
        elif prefix == "":
            pass
        else:
            if not _VALID_TARGET_CHARS.fullmatch(prefix):
                raise VaultError(
                    f"Invalid prefix '{prefix}': only alphanumeric characters "
                    "and ._:@+- are allowed."
                )

        cmd = ["security", "dump-keychain", *self._kc_args()]
        res = self._runner(cmd, capture_output=True, text=False)
        if res.returncode != 0:
            err = (
                res.stderr.decode("utf-8", errors="replace").strip()
                if res.stderr
                else f"exit code {res.returncode}"
            )
            raise VaultError(f"macOS Keychain error dumping keychain: {err}")

        output = (
            res.stdout.decode("utf-8", errors="replace")
            if isinstance(res.stdout, bytes)
            else str(res.stdout or "")
        )
        return _parse_dump_keychain(output, prefix)


def _parse_dump_keychain(output: str, prefix: str) -> list[str]:
    blocks = re.split(r"(?m)(?:^|\n)(?=keychain:\s*|class:\s*)", output)
    results: set[str] = set()
    for block in blocks:
        if not block.strip():
            continue
        svce_match = re.search(r'"svce"<blob>=(?:"([^"]*)"|0x([0-9a-fA-F]+))', block)
        acct_match = re.search(r'"acct"<blob>=(?:"([^"]*)"|0x([0-9a-fA-F]+))', block)
        if not svce_match or not acct_match:
            continue

        if svce_match.group(1) is not None:
            svce = svce_match.group(1)
        else:
            svce = bytes.fromhex(svce_match.group(2)).decode("utf-8", errors="replace")

        if acct_match.group(1) is not None:
            acct = acct_match.group(1)
        else:
            acct = bytes.fromhex(acct_match.group(2)).decode("utf-8", errors="replace")

        target = f"{svce}:{acct}"
        if target.startswith(prefix):
            results.add(target)

    return sorted(results)
