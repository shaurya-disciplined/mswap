"""Pytest configuration and test harness safety net."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mswap.util.clock import FrozenClock
from mswap.util.http import FakeHttp
from mswap.vault.memory import MemoryVault


def make_blob(n: int, *, expiry: str = "2026-10-02T13:00:00.1234567+05:30") -> bytes:
    """Return an agy credential blob shape with fake test tokens."""
    payload = {
        "token": {
            "access_token": f"ya29.FAKE-access-{n}",
            "token_type": "Bearer",
            "refresh_token": f"1//FAKE-refresh-{n}",
            "expiry": expiry,
        },
        "auth_method": "consumer",
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


@pytest.fixture(scope="session", autouse=True)
def _session_safety_guard() -> Generator[None, None, None]:
    """Fail the session if real Windows credential target gemini:antigravity was referenced."""
    yield
    from mswap.vault.memory import TOUCHED

    if "gemini:antigravity" in TOUCHED:
        pytest.fail("Safety net triggered: gemini:antigravity was referenced in MemoryVault!")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate environment variables for every test."""
    monkeypatch.setenv("MSWAP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "mswaptest:live")
    monkeypatch.setenv("MSWAP_VAULT_PREFIX", "mswaptest:")
    monkeypatch.setenv("MSWAP_VAULT", "memory")
    monkeypatch.setenv("MSWAP_NO_NETWORK", "1")
    monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path / "agy-state"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MSWAP_ASCII", raising=False)
    monkeypatch.delenv("MSWAP_DEBUG", raising=False)

    try:
        import mswap.vault.windows as win_vault  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        if hasattr(win_vault, "WindowsVault"):
            cls = win_vault.WindowsVault
            for method_name in ("read", "write", "delete"):
                if hasattr(cls, method_name):
                    orig = getattr(cls, method_name)

                    def make_guarded(orig_fn: Any) -> Any:
                        def guarded(self: Any, target: str, *args: Any, **kwargs: Any) -> Any:
                            if target == "gemini:antigravity":
                                raise AssertionError(
                                    "Safety net triggered: gemini:antigravity "
                                    "called on WindowsVault!"
                                )
                            return orig_fn(self, target, *args, **kwargs)

                        return guarded

                    monkeypatch.setattr(cls, method_name, make_guarded(orig))


@pytest.fixture
def vault() -> MemoryVault:
    """Provide a fresh MemoryVault instance."""
    return MemoryVault()


@pytest.fixture
def http() -> FakeHttp:
    """Provide a fresh FakeHttp instance."""
    return FakeHttp()


@pytest.fixture
def clock() -> FrozenClock:
    """Provide a FrozenClock fixed at 2026-10-02T12:00:00Z."""
    return FrozenClock(datetime(2026, 10, 2, 12, 0, tzinfo=UTC))
