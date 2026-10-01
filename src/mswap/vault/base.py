"""Vault interface and constants."""

from __future__ import annotations

from typing import Protocol

MAX_BLOB = 2560


class Vault(Protocol):
    """Protocol for credential vault backends."""

    def read(self, target: str) -> bytes | None: ...

    def write(self, target: str, blob: bytes, user: str) -> None: ...

    def delete(self, target: str) -> bool: ...

    def list(self, prefix: str) -> list[str]: ...
