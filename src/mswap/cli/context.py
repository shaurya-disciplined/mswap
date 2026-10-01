"""Application context and dependency injection."""

from __future__ import annotations

from dataclasses import dataclass

from mswap.util.clock import Clock, SystemClock
from mswap.util.http import Http, UrllibHttp
from mswap.vault import get_vault
from mswap.vault.base import Vault


@dataclass
class AppContext:
    """Execution context carrying shared services and configuration."""

    vault: Vault
    http: Http
    clock: Clock


_ACTIVE_CONTEXT: AppContext | None = None


def default_context() -> AppContext:
    """Create the production application context."""
    return AppContext(
        vault=get_vault(),
        http=UrllibHttp(),
        clock=SystemClock(),
    )


def get_context() -> AppContext:
    """Return the active or default application context."""
    if _ACTIVE_CONTEXT is not None:
        return _ACTIVE_CONTEXT
    return default_context()


def set_context(ctx: AppContext | None) -> None:
    """Set or clear the active application context."""
    global _ACTIVE_CONTEXT
    _ACTIVE_CONTEXT = ctx
