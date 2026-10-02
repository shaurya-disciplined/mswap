"""Application context and dependency injection."""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TextIO

from mswap.core.events import Events
from mswap.core.journal import Journal
from mswap.core.locking import FileLock
from mswap.core.store import AccountStore, data_dir
from mswap.ui.theme import Theme, theme_from
from mswap.util.clock import Clock, SystemClock
from mswap.util.http import Http, UrllibHttp
from mswap.vault import get_vault
from mswap.vault.base import Vault


def _default_lock(timeout: float = 10.0, poll: float = 0.05) -> FileLock:
    return FileLock(data_dir() / "mswap.lock", timeout=timeout, poll=poll)


def _default_journal() -> Journal:
    return Journal(data_dir() / "journal.json")


def _default_events() -> Events:
    return Events(data_dir() / "events.log")


@dataclass
class AppContext:
    """Execution context carrying shared services and configuration."""

    vault: Vault
    http: Http
    clock: Clock
    store: AccountStore
    env: Mapping[str, str]
    out: TextIO
    err: TextIO
    theme: Theme
    json: bool
    quiet: bool
    lock: Callable[..., FileLock] = _default_lock
    journal: Journal = field(default_factory=_default_journal)
    events: Events = field(default_factory=_default_events)


_ACTIVE_CONTEXT: AppContext | None = None


def default_context(args: argparse.Namespace | None = None) -> AppContext:
    """Create the default application context based on CLI arguments and environment."""
    env = os.environ
    no_color = getattr(args, "no_color", False) if args is not None else False
    ascii_flag = getattr(args, "ascii", False) if args is not None else False
    is_json = getattr(args, "json", False) if args is not None else False
    quiet = getattr(args, "quiet", False) if args is not None else False
    theme = theme_from(
        env,
        no_color_flag=no_color,
        ascii_flag=ascii_flag,
        isatty=sys.stdout.isatty(),
    )
    home = data_dir()
    return AppContext(
        vault=get_vault(),
        http=UrllibHttp(),
        clock=SystemClock(),
        store=AccountStore(home),
        env=env,
        out=sys.stdout,
        err=sys.stderr,
        theme=theme,
        json=is_json,
        quiet=quiet,
        lock=lambda timeout=10.0, poll=0.05: FileLock(
            home / "mswap.lock", timeout=timeout, poll=poll
        ),
        journal=Journal(home / "journal.json"),
        events=Events(home / "events.log"),
    )


def get_context(args: argparse.Namespace | None = None) -> AppContext:
    """Return the active or default application context."""
    if _ACTIVE_CONTEXT is not None:
        from io import StringIO

        out = _ACTIVE_CONTEXT.out if isinstance(_ACTIVE_CONTEXT.out, StringIO) else sys.stdout
        err = _ACTIVE_CONTEXT.err if isinstance(_ACTIVE_CONTEXT.err, StringIO) else sys.stderr

        no_color = getattr(args, "no_color", False) if args is not None else False
        ascii_flag = getattr(args, "ascii", False) if args is not None else False
        is_json = getattr(args, "json", False) if args is not None else False
        quiet = getattr(args, "quiet", False) if args is not None else False
        theme = theme_from(
            _ACTIVE_CONTEXT.env,
            no_color_flag=no_color,
            ascii_flag=ascii_flag,
            isatty=out.isatty() if hasattr(out, "isatty") else False,
        )
        return dataclasses.replace(
            _ACTIVE_CONTEXT,
            out=out,
            err=err,
            theme=theme,
            json=is_json,
            quiet=quiet,
        )
    return default_context(args)


def set_context(ctx: AppContext | None) -> None:
    """Set or clear the active application context."""
    global _ACTIVE_CONTEXT
    _ACTIVE_CONTEXT = ctx
