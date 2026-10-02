"""Unit tests for watch CLI command."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch

from mswap.cli.commands.watch import run
from mswap.cli.context import AppContext
from mswap.ui.theme import Theme
from mswap.util.clock import FrozenClock
from mswap.util.http import FakeHttp
from mswap.vault.memory import MemoryVault


def test_watch_command_dispatches_to_run_live() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ctx = AppContext(
        vault=MemoryVault(),
        http=FakeHttp(),
        clock=FrozenClock(now),
        store=type("MockStore", (), {})(),  # type: ignore[arg-type]
        env={},
        out=StringIO(),
        err=StringIO(),
        theme=Theme(color=False),
        json=False,
        quiet=False,
    )
    args = argparse.Namespace(force=True)

    with patch("mswap.cli.commands.watch.run_live") as mock_run_live:
        mock_run_live.return_value = 0
        code = run(ctx, args)
        assert code == 0
        mock_run_live.assert_called_once_with(ctx, force=True)
