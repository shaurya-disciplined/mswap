"""Unit tests for CompositeVault."""

from __future__ import annotations

import pytest

from mswap.core.errors import UsageError, VaultError
from mswap.vault.file import CompositeVault
from mswap.vault.memory import MemoryVault


def test_composite_vault_routes_live_vs_own() -> None:
    live_vault = MemoryVault()
    own_vault = MemoryVault()
    comp = CompositeVault(
        live=live_vault,
        own=own_vault,
        live_target_fn=lambda: "mswaptest:live",
    )

    # Write to live target -> goes to live_vault
    comp.write("mswaptest:live", b"live-token", "live-user")
    assert live_vault.read("mswaptest:live") == b"live-token"
    assert own_vault.read("mswaptest:live") is None
    assert comp.read("mswaptest:live") == b"live-token"

    # Write to own slot target -> goes to own_vault
    comp.write("mswaptest:slot1", b"slot-token", "slot-user")
    assert own_vault.read("mswaptest:slot1") == b"slot-token"
    assert live_vault.read("mswaptest:slot1") is None
    assert comp.read("mswaptest:slot1") == b"slot-token"

    # Delete routing
    assert comp.delete("mswaptest:live") is True
    assert live_vault.read("mswaptest:live") is None

    assert comp.delete("mswaptest:slot1") is True
    assert own_vault.read("mswaptest:slot1") is None


def test_composite_vault_live_unavailable_raises_usage_error() -> None:
    class FailingLiveVault(MemoryVault):
        def read(self, target: str) -> bytes | None:
            raise VaultError("No Secret Service available.")

        def write(self, target: str, blob: bytes, user: str) -> None:
            raise VaultError("No Secret Service available.")

        def delete(self, target: str) -> bool:
            raise VaultError("No Secret Service available.")

    live_vault = FailingLiveVault()
    own_vault = MemoryVault()
    comp = CompositeVault(
        live=live_vault,
        own=own_vault,
        live_target_fn=lambda: "mswaptest:live",
    )

    # Operations on live target raise UsageError
    with pytest.raises(UsageError, match="agy's login lives in the Secret Service"):
        comp.read("mswaptest:live")

    with pytest.raises(UsageError, match="agy's login lives in the Secret Service"):
        comp.write("mswaptest:live", b"blob", "user")

    with pytest.raises(UsageError, match="agy's login lives in the Secret Service"):
        comp.delete("mswaptest:live")

    # Operations on own targets succeed despite Secret Service absence
    comp.write("mswaptest:slot1", b"blob1", "user1")
    assert comp.read("mswaptest:slot1") == b"blob1"
    assert comp.delete("mswaptest:slot1") is True


def test_composite_vault_list_combines() -> None:
    live_vault = MemoryVault()
    own_vault = MemoryVault()
    comp = CompositeVault(
        live=live_vault,
        own=own_vault,
        live_target_fn=lambda: "mswaptest:live",
    )

    live_vault.write("mswaptest:live", b"live", "user")
    own_vault.write("mswaptest:slot1", b"slot1", "user")
    own_vault.write("mswaptest:slot2", b"slot2", "user")

    assert comp.list("mswaptest:") == [
        "mswaptest:live",
        "mswaptest:slot1",
        "mswaptest:slot2",
    ]
    assert comp.list("mswaptest:slot") == ["mswaptest:slot1", "mswaptest:slot2"]
