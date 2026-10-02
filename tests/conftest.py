"""Pytest configuration and test harness safety net."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from mswap.cli.context import AppContext, set_context
from mswap.core.events import Events
from mswap.core.journal import Journal
from mswap.core.locking import FileLock
from mswap.core.store import AccountStore
from mswap.ui.theme import Theme
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
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[None, None, None]:
    """Isolate environment variables for every test."""
    monkeypatch.setenv("MSWAP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(tmp_path / "legacy"))
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "mswaptest:live")
    monkeypatch.setenv("MSWAP_VAULT_PREFIX", "mswaptest:")
    monkeypatch.setenv("MSWAP_VAULT", "memory")
    monkeypatch.setenv("MSWAP_NO_NETWORK", "1")
    monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path / "agy-state"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MSWAP_ASCII", raising=False)
    monkeypatch.delenv("MSWAP_DEBUG", raising=False)

    from mswap.vault import reset_memory_vault

    reset_memory_vault()

    try:
        import mswap.vault.windows as win_vault
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

    yield
    from mswap.cli.context import set_context

    set_context(None)
    reset_memory_vault()


@pytest.fixture
def vault(_isolate: None) -> MemoryVault:
    """Provide the process-wide MemoryVault instance."""
    from mswap.vault import get_vault

    v = get_vault()
    assert isinstance(v, MemoryVault)
    return v


@pytest.fixture
def http() -> FakeHttp:
    """Provide a fresh FakeHttp instance."""
    return FakeHttp()


@pytest.fixture
def clock() -> FrozenClock:
    """Provide a FrozenClock fixed at 2026-10-02T12:00:00Z."""
    return FrozenClock(datetime(2026, 10, 2, 12, 0, tzinfo=UTC))


@pytest.fixture
def ctx(vault: MemoryVault, http: FakeHttp, clock: FrozenClock, tmp_path: Path) -> AppContext:
    """Provide an isolated AppContext with in-memory streams and no-colour Theme."""
    home = tmp_path / "home"
    store = AccountStore(home)
    journal = Journal(home / "journal.json")
    events = Events(home / "events.log")
    return AppContext(
        vault=vault,
        http=http,
        clock=clock,
        store=store,
        env=dict(os.environ),
        out=StringIO(),
        err=StringIO(),
        theme=Theme(color=False),
        json=False,
        quiet=False,
        lock=lambda timeout=10.0, poll=0.05: FileLock(
            home / "mswap.lock", timeout=timeout, poll=poll
        ),
        journal=journal,
        events=events,
        procs=lambda: [],
        inside_agy=lambda: False,
        sleep=lambda s: clock.advance(s),
        runner=lambda cmd: 0,
    )


@pytest.fixture(autouse=True)
def _inject_test_context(
    vault: MemoryVault, http: FakeHttp, clock: FrozenClock, tmp_path: Path
) -> Generator[None, None, None]:
    """Inject test context into cli context."""
    home = tmp_path / "home"
    store = AccountStore(home)
    journal = Journal(home / "journal.json")
    events = Events(home / "events.log")
    app_ctx = AppContext(
        vault=vault,
        http=http,
        clock=clock,
        store=store,
        env=dict(os.environ),
        out=sys.stdout,
        err=sys.stderr,
        theme=Theme(color=False),
        json=False,
        quiet=False,
        lock=lambda timeout=10.0, poll=0.05: FileLock(
            home / "mswap.lock", timeout=timeout, poll=poll
        ),
        journal=journal,
        events=events,
        procs=lambda: [],
        inside_agy=lambda: False,
        sleep=lambda s: clock.advance(s),
        runner=lambda cmd: 0,
    )
    set_context(app_ctx)
    yield
    set_context(None)
