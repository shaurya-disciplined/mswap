"""Error taxonomy, exit codes, and hints for mswap.

Owns error classifications and exit codes for all failure states.
Must never log, print, or leak credential secrets in exception messages.
"""

from __future__ import annotations


class MswapError(Exception):
    """Base exception for all mswap errors."""

    code: int = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    @property
    def kind(self) -> str:
        """Return the exception class name."""
        return self.__class__.__name__


class NothingToDo(MswapError):
    """Raised when an operation has nothing to switch or update."""

    code: int = 2


class NoViableTarget(MswapError):
    """Raised when no accounts are viable for switching."""

    code: int = 3


class NotSignedIn(MswapError):
    """Raised when agy is not currently signed in."""

    code: int = 4


class TokenDead(MswapError):
    """Raised when an OAuth refresh token is dead or revoked."""

    code: int = 4


class AgyNotFound(MswapError):
    """Raised when the agy executable cannot be located."""

    code: int = 5


class UnsafeOperation(MswapError):
    """Raised when an operation would overwrite an unsaved login."""

    code: int = 6


class LockTimeout(MswapError):
    """Raised when acquiring the cross-process lock times out."""

    code: int = 7


class UsageError(MswapError):
    """Raised when invalid command-line usage or arguments are given."""

    code: int = 64


class TokenExpired(MswapError):
    """Raised when an API call returns HTTP 401 Unauthorized."""

    code: int = 1


class ApiError(MswapError):
    """Raised when an API call fails with an HTTP error."""

    code: int = 1

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        endpoint: str = "",
        hint: str | None = None,
        kind: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.status = status
        self.endpoint = endpoint
        self._kind = kind

    @property
    def kind(self) -> str:
        """Return specific error kind or exception class name."""
        return self._kind if self._kind is not None else self.__class__.__name__


class NetworkError(MswapError):
    """Raised when an HTTP or network operation fails."""

    code: int = 1


class VaultError(MswapError):
    """Raised when an operation on a credential vault fails."""

    code: int = 1


class CorruptState(MswapError):
    """Raised when on-disk state or credentials are corrupted."""

    code: int = 1


INTERNAL_ERROR_CODE: int = 70
