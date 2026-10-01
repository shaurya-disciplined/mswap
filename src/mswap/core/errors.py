"""Error hierarchy for mswap."""

from __future__ import annotations


class MswapError(Exception):
    """Base exception for all mswap errors."""

    code: int = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class VaultError(MswapError):
    """Raised when an operation on a credential vault fails."""


class NetworkError(MswapError):
    """Raised when an HTTP or network operation fails."""
