"""Live dashboard rendering loop and interactive key handling."""

from __future__ import annotations

import contextlib
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, TextIO

from mswap.core.errors import UsageError
from mswap.core.models import Account
from mswap.core.poll_policy import NEAR_LIMIT, ttl
from mswap.core.store import find_active, live_target
from mswap.core.switcher import switch
from mswap.core.usage import refresh_usage
from mswap.ui.render import AccountRow, render_list
from mswap.ui.theme import Theme
from mswap.ui.timefmt import age_text


class LiveContext(Protocol):
    """Protocol for dependencies required by live dashboard."""

    vault: Any
    store: Any
    clock: Any
    theme: Theme
    out: TextIO
    sleep: Callable[[float], None]
    inside_agy: Callable[[], bool]


@dataclass(frozen=True)
class LiveAction:
    """Action resulting from key processing in live dashboard."""

    kind: Literal["none", "quit", "refresh", "switch_next", "switch_slot"]
    slot: int | None = None


@dataclass
class LiveState:
    """Mutable state of live dashboard actions and messages."""

    action_message: str | None = None
    action_is_error: bool = False
    action_time: datetime | None = None
    quit_requested: bool = False


def handle_key(key: str | None, state: LiveState) -> tuple[LiveAction, LiveState]:
    """Pure key-handling state machine: (key, state) -> (action, state)."""
    if key is None:
        return LiveAction(kind="none"), state

    if key in ("q", "Q", "\x1b"):
        state.quit_requested = True
        return LiveAction(kind="quit"), state

    if key in ("r", "R"):
        return LiveAction(kind="refresh"), state

    if key in ("s", "S"):
        return LiveAction(kind="switch_next"), state

    if key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
        return LiveAction(kind="switch_slot", slot=int(key)), state

    return LiveAction(kind="none"), state


def format_footer(
    theme: Theme,
    updated_age_sec: int,
    action_message: str | None = None,
    action_is_error: bool = False,
    action_age_sec: float | None = None,
) -> str:
    """Format footer line and optional transient action message."""
    age_str = f"{age_text(max(0, updated_age_sec))} ago"
    help_text = theme.dim(f"q quit · r refresh · s next · 1-9 switch   updated {age_str}")
    if action_message and action_age_sec is not None and action_age_sec <= 5.0:
        colored = theme.err(action_message) if action_is_error else theme.ok(action_message)
        return f"{help_text}\n  {colored}"
    return help_text


def compose_frame(lines: list[str], footer: str) -> str:
    """Compose full ANSI frame with home cursor, list lines, footer, and clear-to-end."""
    content = "\n".join(lines)
    return f"\x1b[H{content}\n\n{footer}\n\x1b[J"


def default_read_key() -> str | None:
    """Read a single keypress without blocking, or return None."""
    if sys.platform == "win32":
        try:
            import msvcrt

            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    if msvcrt.kbhit():
                        msvcrt.getwch()
                    return None
                return ch
        except Exception:
            return None
        return None
    try:
        import select

        if hasattr(sys.stdin, "fileno"):
            rlist, _, _ = select.select([sys.stdin], [], [], 0)
            if rlist:
                return sys.stdin.read(1)
    except Exception:
        return None
    return None


@contextlib.contextmanager
def _posix_terminal_mode() -> Any:
    """Context manager configuring POSIX terminal to cbreak mode."""
    if sys.platform != "win32" and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    else:
        yield


def _build_account_rows(
    accounts: list[Account],
    active: Account | None,
    results: dict[int, Any],
    now: datetime,
) -> list[AccountRow]:
    """Construct AccountRow items for rendering."""
    rows: list[AccountRow] = []
    for acc in accounts:
        is_active = active is not None and acc.slot == active.slot
        entry = results.get(acc.slot)
        snap = entry.snapshot if entry else None
        near_limit = False
        if snap:
            near_limit = any(b.remaining < NEAR_LIMIT for p in snap.pools for b in p.buckets)

        if entry is not None:
            ttl_val = ttl(snap, active=is_active, near_limit=near_limit)
            elapsed = (now - entry.fetched_at).total_seconds()
            in_backoff = entry.backoff_until is not None and now < entry.backoff_until
            is_stale_flag = elapsed > ttl_val or in_backoff
        else:
            is_stale_flag = True
        rows.append(AccountRow(account=acc, active=is_active, entry=entry, stale=is_stale_flag))
    return rows


def run_live(
    ctx: Any,
    *,
    force: bool = False,
    max_ticks: int | None = None,
    key_reader: Callable[[], str | None] | None = None,
) -> int:
    """Execute live dashboard render loop."""
    out = ctx.out
    if not hasattr(out, "isatty") or not out.isatty():
        raise UsageError(
            "watch needs an interactive terminal.",
            hint="Use `mswap list` instead.",
        )

    read_key_fn = key_reader or default_read_key
    state = LiveState()

    # Enter alternate screen and hide cursor
    out.write("\x1b[?1049h\x1b[?25l")
    out.flush()

    try:
        with _posix_terminal_mode():
            accounts = ctx.store.load()
            live = ctx.vault.read(live_target())
            active: Account | None = find_active(accounts, live)
            results = refresh_usage(ctx, accounts, force=False)
            last_refresh_time = ctx.clock.now()

            ticks = 0
            while not state.quit_requested:
                if max_ticks is not None and ticks >= max_ticks:
                    break

                now = ctx.clock.now()

                # Refresh data every 15s automatically
                if (now - last_refresh_time).total_seconds() >= 15.0:
                    accounts = ctx.store.load()
                    live = ctx.vault.read(live_target())
                    active = find_active(accounts, live)
                    results = refresh_usage(ctx, accounts, force=False)
                    last_refresh_time = now

                # Compose frame
                rows = _build_account_rows(accounts, active, results, now)
                width = shutil.get_terminal_size((80, 24)).columns
                lines = render_list(rows, now=now, theme=ctx.theme, width=width, tz=None)

                updated_age_sec = max(0, int((now - last_refresh_time).total_seconds()))
                action_age_sec = (
                    (now - state.action_time).total_seconds()
                    if state.action_time is not None
                    else None
                )
                footer = format_footer(
                    ctx.theme,
                    updated_age_sec,
                    action_message=state.action_message,
                    action_is_error=state.action_is_error,
                    action_age_sec=action_age_sec,
                )

                frame = compose_frame(lines, footer)
                out.write(frame)
                out.flush()

                # Check keys in small slices for 1 second tick
                slices = 10
                for _ in range(slices):
                    key = read_key_fn()
                    if key is not None:
                        action, state = handle_key(key, state)
                        if action.kind == "quit":
                            break

                        if action.kind == "refresh":
                            try:
                                accounts = ctx.store.load()
                                results = refresh_usage(ctx, accounts, force=True)
                                last_refresh_time = ctx.clock.now()
                                state.action_message = "Refreshed"
                                state.action_is_error = False
                                state.action_time = ctx.clock.now()
                            except Exception as e:
                                state.action_message = f"Refresh failed: {e}"
                                state.action_is_error = True
                                state.action_time = ctx.clock.now()
                            break

                        if action.kind in ("switch_next", "switch_slot"):
                            is_inside = (
                                ctx.inside_agy()
                                if callable(getattr(ctx, "inside_agy", None))
                                else False
                            )
                            if is_inside and not force:
                                state.action_message = (
                                    "Refusing to switch inside agy without --force"
                                )
                                state.action_is_error = True
                                state.action_time = ctx.clock.now()
                            else:
                                selector = (
                                    str(action.slot)
                                    if action.kind == "switch_slot" and action.slot is not None
                                    else None
                                )
                                try:
                                    res = switch(ctx, selector, force=force)
                                    accounts = ctx.store.load()
                                    live = ctx.vault.read(live_target())
                                    active = find_active(accounts, live)
                                    results = refresh_usage(ctx, accounts, force=False)
                                    if res.status == "already_active":
                                        state.action_message = (
                                            f"Already on account {res.to_account.slot}: "
                                            f"{res.to_account.email}"
                                        )
                                    else:
                                        state.action_message = (
                                            f"Switched to account {res.to_account.slot}: "
                                            f"{res.to_account.email}"
                                        )
                                    state.action_is_error = False
                                    state.action_time = ctx.clock.now()
                                except Exception as e:
                                    state.action_message = f"Switch failed: {e}"
                                    state.action_is_error = True
                                    state.action_time = ctx.clock.now()
                            break

                    ctx.sleep(0.1)

                ticks += 1

    except KeyboardInterrupt:
        pass
    finally:
        # Always restore cursor and alternate screen
        out.write("\x1b[?25h\x1b[?1049l")
        out.flush()

    return 0
