"""Credential vault storage backends."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mswap.core.errors import VaultError
from mswap.vault.base import Vault
from mswap.vault.memory import MemoryVault

_MEMORY_VAULT_SINGLETON: MemoryVault | None = None


def _demo_vault_path() -> Path:
    if "MSWAP_HOME" in os.environ:
        return Path(os.environ["MSWAP_HOME"]) / "demo_vault.json"
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        base = Path(local_app) if local_app else Path.home() / "AppData" / "Local"
        return base / "mswap" / "demo_vault.json"
    return Path.home() / ".mswap" / "demo_vault.json"


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
    if backend in ("native", "windows"):
        if sys.platform == "win32":
            from mswap.vault.windows import WindowsVault

            return WindowsVault()
        raise VaultError(
            "No supported credential store on this OS yet.",
            hint="macOS and Linux support arrives in v0.6.",
        )
    raise VaultError(f"Unknown vault backend: {backend}")


def reset_memory_vault() -> None:
    """Reset the memory vault singleton (used in test isolation)."""
    global _MEMORY_VAULT_SINGLETON
    _MEMORY_VAULT_SINGLETON = None
