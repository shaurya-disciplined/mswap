"""Credential vault storage backends."""

from __future__ import annotations

import os

from mswap.vault.base import Vault
from mswap.vault.memory import MemoryVault

_MEMORY_VAULT_SINGLETON: MemoryVault | None = None


def get_vault() -> Vault:
    """Return the configured vault instance."""
    if os.environ.get("MSWAP_VAULT") == "memory":
        global _MEMORY_VAULT_SINGLETON
        if _MEMORY_VAULT_SINGLETON is None:
            _MEMORY_VAULT_SINGLETON = MemoryVault()
        return _MEMORY_VAULT_SINGLETON
    from mswap.vault.windows import WindowsVault

    return WindowsVault()


def reset_memory_vault() -> None:
    """Reset the memory vault singleton (used in test isolation)."""
    global _MEMORY_VAULT_SINGLETON
    _MEMORY_VAULT_SINGLETON = None
