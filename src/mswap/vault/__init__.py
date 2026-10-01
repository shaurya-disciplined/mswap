"""Credential vault storage backends."""

from __future__ import annotations

import os
import sys

from mswap.core.errors import VaultError
from mswap.vault.base import Vault
from mswap.vault.memory import MemoryVault

_MEMORY_VAULT_SINGLETON: MemoryVault | None = None


def get_vault() -> Vault:
    """Return the configured vault instance."""
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
