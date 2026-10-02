"""File-based vault backend and composite vault for Linux/headless environments."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from mswap.core.errors import UsageError, VaultError
from mswap.vault.base import MAX_BLOB, Vault
from mswap.vault.linux import SecretToolVault

_VALID_TARGET_CHARS = re.compile(r"^[A-Za-z0-9._:@+-]+$")


def _default_file_vault_root() -> Path:
    if "MSWAP_HOME" in os.environ:
        return Path(os.environ["MSWAP_HOME"]) / "vault"
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        base = Path(local_app) if local_app else Path.home() / "AppData" / "Local"
        return base / "mswap" / "vault"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mswap" / "vault"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "mswap" / "vault"


def _default_live_target() -> str:
    return os.environ.get("MSWAP_LIVE_TARGET", "gemini:antigravity")


class FileVault:
    """Opt-in file-based credential storage for headless/WSL systems.

    Stores one file per target, named sha256(target)[:32] with base64-encoded
    content, and a mapping file index.json {hash: target}.
    Directory mode is 0700, files 0600.
    """

    def __init__(self, root: Path | None = None, *, _allow_windows: bool = False) -> None:
        if sys.platform == "win32" and not _allow_windows:
            raise UsageError(
                "File vault is not supported on Windows.",
                hint="Windows uses Windows Credential Manager.",
            )
        self.root = root if root is not None else _default_file_vault_root()

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

    def _ensure_dir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            with contextlib.suppress(OSError):
                self.root.chmod(0o700)

    def _write_secure_file(self, target_path: Path, data: bytes) -> None:
        self._ensure_dir()
        temp_path = self.root / f".{target_path.name}.{uuid4().hex[:8]}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        mode = 0o600
        fd = os.open(temp_path, flags, mode)
        try:
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(fd)
        except Exception:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)
            raise
        temp_path.replace(target_path)
        if sys.platform != "win32":
            with contextlib.suppress(OSError):
                target_path.chmod(0o600)

    def _hash(self, target: str) -> str:
        return hashlib.sha256(target.encode("utf-8")).hexdigest()[:32]

    def _read_index(self) -> dict[str, str]:
        index_file = self.root / "index.json"
        if not index_file.is_file():
            return {}
        try:
            content = index_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            return {}
        return {}

    def _write_index(self, index: dict[str, str]) -> None:
        data = json.dumps(index, indent=2).encode("utf-8")
        self._write_secure_file(self.root / "index.json", data)

    def read(self, target: str) -> bytes | None:
        """Read and decode credential blob for target, or None if not found."""
        self.split(target)
        h = self._hash(target)
        p = self.root / h
        if not p.is_file():
            return None
        try:
            content = p.read_bytes().strip()
            return base64.b64decode(content)
        except Exception as e:
            raise VaultError(f"Failed to read file vault item '{target}': {e}") from e

    def write(
        self,
        target: str,
        blob: bytes,
        user: str,  # noqa: ARG002 - Vault protocol parameter; user metadata is kept in accounts.json
    ) -> None:
        """Write credential blob for target to an owner-only (0600) file; base64, not encrypted."""
        if len(blob) == 0:
            raise VaultError("Credential blob cannot be empty.")
        if len(blob) > MAX_BLOB:
            raise VaultError(f"Credential is too large ({len(blob)} bytes, max {MAX_BLOB}).")

        self.split(target)
        h = self._hash(target)
        encoded = base64.b64encode(blob)
        self._write_secure_file(self.root / h, encoded)

        index = self._read_index()
        index[h] = target
        self._write_index(index)

    def delete(self, target: str) -> bool:
        """Delete credential for target. Return True if deleted, False if not found."""
        self.split(target)
        h = self._hash(target)
        p = self.root / h
        if not p.is_file():
            return False
        p.unlink(missing_ok=True)
        index = self._read_index()
        if h in index:
            del index[h]
            self._write_index(index)
        return True

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

        index = self._read_index()
        results: list[str] = []
        for h, target in index.items():
            if target.startswith(prefix) and (self.root / h).is_file():
                results.append(target)
        return sorted(results)


class CompositeVault:
    """Routes live target to Secret Service and mswap accounts to FileVault."""

    def __init__(
        self,
        live: Vault | None = None,
        own: Vault | None = None,
        live_target_fn: Callable[[], str] | None = None,
    ) -> None:
        self.live = live if live is not None else SecretToolVault()
        self.own = own if own is not None else FileVault()
        self._live_target_fn = (
            live_target_fn if live_target_fn is not None else _default_live_target
        )

    def _is_live(self, target: str) -> bool:
        return target == self._live_target_fn()

    def read(self, target: str) -> bytes | None:
        if self._is_live(target):
            try:
                return self.live.read(target)
            except VaultError as e:
                if "No Secret Service available" in str(e):
                    raise UsageError(
                        "agy's login lives in the Secret Service, which isn't available here."
                    ) from e
                raise
        return self.own.read(target)

    def write(self, target: str, blob: bytes, user: str) -> None:
        if self._is_live(target):
            try:
                self.live.write(target, blob, user)
            except VaultError as e:
                if "No Secret Service available" in str(e):
                    raise UsageError(
                        "agy's login lives in the Secret Service, which isn't available here."
                    ) from e
                raise
        else:
            self.own.write(target, blob, user)

    def delete(self, target: str) -> bool:
        if self._is_live(target):
            try:
                return self.live.delete(target)
            except VaultError as e:
                if "No Secret Service available" in str(e):
                    raise UsageError(
                        "agy's login lives in the Secret Service, which isn't available here."
                    ) from e
                raise
        return self.own.delete(target)

    def list(self, prefix: str) -> list[str]:
        results = set(self.own.list(prefix))
        live_tgt = self._live_target_fn()
        if live_tgt.startswith(prefix):
            with contextlib.suppress(Exception):
                if self.live.read(live_tgt) is not None:
                    results.add(live_tgt)
        return sorted(results)
