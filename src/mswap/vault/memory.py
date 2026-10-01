"""In-memory vault implementation for testing."""

from __future__ import annotations

from mswap.core.errors import VaultError
from mswap.vault.base import MAX_BLOB

TOUCHED: set[str] = set()


class MemoryVault:
    """In-memory fake vault recording calls and entries."""

    def __init__(self, *, fail_on_write: int | None = None) -> None:
        self.entries: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_on_write = fail_on_write
        self._write_count = 0

    def read(self, target: str) -> bytes | None:
        TOUCHED.add(target)
        self.calls.append(("read", target))
        entry = self.entries.get(target)
        return entry[0] if entry is not None else None

    def write(self, target: str, blob: bytes, user: str) -> None:
        TOUCHED.add(target)
        self.calls.append(("write", target))
        self._write_count += 1
        if self.fail_on_write is not None and self._write_count == self.fail_on_write:
            raise VaultError("simulated failure")
        if len(blob) > MAX_BLOB:
            raise VaultError(f"blob length {len(blob)} exceeds maximum {MAX_BLOB}")
        self.entries[target] = (blob, user)

    def delete(self, target: str) -> bool:
        TOUCHED.add(target)
        self.calls.append(("delete", target))
        if target in self.entries:
            del self.entries[target]
            return True
        return False

    def list(self, prefix: str) -> list[str]:
        TOUCHED.add(prefix)
        self.calls.append(("list", prefix))
        return sorted(target for target in self.entries if target.startswith(prefix))
