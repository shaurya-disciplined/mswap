"""Credential vault storage backends."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mswap.core.errors import UsageError, VaultError
from mswap.vault.base import Vault
from mswap.vault.memory import MemoryVault

_MEMORY_VAULT_SINGLETON: MemoryVault | None = None
_MACOS_WARNED: bool = False
_LINUX_WARNED: bool = False
_FILE_VAULT_WARNED: bool = False


def _demo_vault_path() -> Path:
    if "MSWAP_HOME" in os.environ:
        return Path(os.environ["MSWAP_HOME"]) / "demo_vault.json"
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        base = Path(local_app) if local_app else Path.home() / "AppData" / "Local"
        return base / "mswap" / "demo_vault.json"
    return Path.home() / ".mswap" / "demo_vault.json"


def _dim(s: str) -> str:
    if os.environ.get("NO_COLOR") or not getattr(sys.stderr, "isatty", lambda: False)():
        return s
    return f"\033[2m{s}\033[0m"


def get_vault() -> Vault:
    """Return the configured vault instance."""
    if os.environ.get("MSWAP_DEMO") == "1":
        from mswap.vault.demo import DemoVault

        return DemoVault(_demo_vault_path())

    backend = os.environ.get("MSWAP_VAULT", "native")
    if backend == "memory":
        global _MEMORY_VAULT_SINGLETON
        if _MEMORY_VAULT_SINGLETON is None:
            _MEMORY_VAULT_SINGLETON = MemoryVault()
        return _MEMORY_VAULT_SINGLETON

    if backend == "file":
        if sys.platform == "win32":
            raise UsageError(
                "File vault is not supported on Windows.",
                hint="Windows uses Windows Credential Manager.",
            )
        from mswap.vault.file import CompositeVault, FileVault, _default_file_vault_root
        from mswap.vault.linux import SecretToolVault

        root = _default_file_vault_root()
        global _FILE_VAULT_WARNED
        if not _FILE_VAULT_WARNED:
            _FILE_VAULT_WARNED = True
            print(
                f"! Using the file vault: logins are stored unencrypted in {root}. "
                "Prefer a Secret Service.",
                file=sys.stderr,
            )

        return CompositeVault(live=SecretToolVault(), own=FileVault(root=root))

    if backend in ("native", "windows", "macos", "linux"):
        if sys.platform == "win32":
            from mswap.vault.windows import WindowsVault

            return WindowsVault()
        if sys.platform == "darwin":
            from mswap.vault.macos import MacKeychainVault

            return MacKeychainVault()
        if sys.platform.startswith("linux"):
            from mswap.vault.linux import SecretToolVault

            return SecretToolVault()
        raise VaultError(
            "No supported credential store on this OS yet.",
            hint="Only macOS, Windows, and Linux are supported.",
        )
    raise VaultError(f"Unknown vault backend: {backend}")


def reset_memory_vault() -> None:
    """Reset the memory vault singleton (used in test isolation)."""
    global _MEMORY_VAULT_SINGLETON
    _MEMORY_VAULT_SINGLETON = None


def reset_file_vault_warned() -> None:
    """Reset the file vault warning flag (used in test isolation)."""
    global _FILE_VAULT_WARNED
    _FILE_VAULT_WARNED = False
