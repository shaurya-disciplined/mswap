"""Unit tests for live dashboard UI, state machine, and terminal management."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import pytest

from mswap.agy.tokens import fingerprint
from mswap.cli.context import AppContext
from mswap.core.errors import UsageError
from mswap.core.models import Account, Bucket, Pool, QuotaSnapshot
from mswap.core.store import live_target, slot_target
from mswap.core.usage_cache import UsageCache
from mswap.ui.live import (
    LiveState,
    compose_frame,
    format_footer,
    handle_key,
    run_live,
)
from mswap.ui.theme import Theme


def _seed_cache(ctx: AppContext, fp: str, now: datetime) -> None:
    cache = UsageCache(ctx.store.root / "usage.json")
    snap = QuotaSnapshot(
        fetched_at=now,
        pools=[
            Pool(
                key="gemini",
                name="Gemini",
                buckets=[Bucket(window="5h", remaining=0.99, reset_at=now)],
            ),
        ],
    )
    cache.put_snapshot(fp, snap, now)


class MockTTYStream(StringIO):
    """StringIO stream simulating an interactive TTY."""

    def isatty(self) -> bool:
        return True


def test_handle_key_pure_state_machine() -> None:
    state = LiveState()

    # None
    action, state = handle_key(None, state)
    assert action.kind == "none"
    assert not state.quit_requested

    # Unrecognized key
    action, state = handle_key("x", state)
    assert action.kind == "none"

    # Refresh
    action, state = handle_key("r", state)
    assert action.kind == "refresh"
    action, state = handle_key("R", state)
    assert action.kind == "refresh"

    # Switch next
    action, state = handle_key("s", state)
    assert action.kind == "switch_next"
    action, state = handle_key("S", state)
    assert action.kind == "switch_next"

    # Switch slot 1-9
    for i in range(1, 10):
        action, state = handle_key(str(i), state)
        assert action.kind == "switch_slot"
        assert action.slot == i

    # Quit
    for q_key in ("q", "Q", "\x1b"):
        s = LiveState()
        action, s = handle_key(q_key, s)
        assert action.kind == "quit"
        assert s.quit_requested is True


def test_compose_frame_pure() -> None:
    lines = ["Line 1", "Line 2"]
    footer = "footer text"
    frame = compose_frame(lines, footer)
    assert frame.startswith("\x1b[H")
    assert "Line 1\nLine 2\n\nfooter text\n\x1b[J" in frame


def test_format_footer() -> None:
    theme = Theme(color=False)
    footer = format_footer(theme, updated_age_sec=45)
    assert "q quit · r refresh · s next · 1-9 switch" in footer
    assert "updated 45s ago" in footer

    # Action message within 5s
    footer_action = format_footer(
        theme,
        updated_age_sec=10,
        action_message="Switched successfully",
        action_is_error=False,
        action_age_sec=2.5,
    )
    assert "Switched successfully" in footer_action

    # Action message older than 5s is hidden
    footer_expired = format_footer(
        theme,
        updated_age_sec=10,
        action_message="Old message",
        action_is_error=False,
        action_age_sec=5.5,
    )
    assert "Old message" not in footer_expired


def test_run_live_not_tty_raises_usage_error(ctx: AppContext) -> None:
    ctx.out = StringIO()  # isatty is False
    with pytest.raises(UsageError) as exc_info:
        run_live(ctx)
    assert "watch needs an interactive terminal" in str(exc_info.value.message)


def test_run_live_terminal_restore_sequences_on_exception(ctx: AppContext) -> None:
    out = MockTTYStream()
    ctx.out = out

    # Override store.load to raise an unhandled exception
    def faulty_load() -> list[Account]:
        raise RuntimeError("Disk read error")

    ctx.store.load = faulty_load  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        run_live(ctx)

    output = out.getvalue()
    # Check alternate screen enter
    assert "\x1b[?1049h\x1b[?25l" in output
    # Check alternate screen restore
    assert "\x1b[?25h\x1b[?1049l" in output


def test_run_live_immediate_quit_and_restore(ctx: AppContext) -> None:
    out = MockTTYStream()
    ctx.out = out

    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    blob = b'{"token":{"refresh_token":"fake-rt-1"}}'
    fp = fingerprint(blob)
    ctx.store.save(
        [Account(slot=1, email="alice@example.com", fp=fp, added_at=now, updated_at=now)]
    )
    ctx.vault.write(live_target(), blob, "antigravity")
    _seed_cache(ctx, fp, now)

    keys = ["q"]
    key_idx = 0

    def key_reader() -> str | None:
        nonlocal key_idx
        if key_idx < len(keys):
            ch = keys[key_idx]
            key_idx += 1
            return ch
        return None

    code = run_live(ctx, max_ticks=5, key_reader=key_reader)
    assert code == 0
    val = out.getvalue()
    assert "\x1b[?1049h\x1b[?25l" in val
    assert "\x1b[?25h\x1b[?1049l" in val


def test_run_live_inside_agy_refuses_switch_without_force(ctx: AppContext) -> None:
    out = MockTTYStream()
    ctx.out = out

    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    blob1 = b'{"token":{"refresh_token":"fake-rt-1"}}'
    blob2 = b'{"token":{"refresh_token":"fake-rt-2"}}'
    fp1 = fingerprint(blob1)
    fp2 = fingerprint(blob2)

    ctx.store.save(
        [
            Account(slot=1, email="alice@example.com", fp=fp1, added_at=now, updated_at=now),
            Account(slot=2, email="bob@example.com", fp=fp2, added_at=now, updated_at=now),
        ]
    )

    ctx.vault.write(slot_target(1), blob1, "alice@example.com")
    ctx.vault.write(slot_target(2), blob2, "bob@example.com")
    ctx.vault.write(live_target(), blob1, "antigravity")
    _seed_cache(ctx, fp1, now)
    _seed_cache(ctx, fp2, now)

    ctx.inside_agy = lambda: True  # Inside agy!

    # Sequence: switch next ('s'), then quit ('q')
    keys = ["s", "q"]
    key_idx = 0

    def key_reader() -> str | None:
        nonlocal key_idx
        if key_idx < len(keys):
            ch = keys[key_idx]
            key_idx += 1
            return ch
        return None

    code = run_live(ctx, force=False, max_ticks=5, key_reader=key_reader)
    assert code == 0
    val = out.getvalue()
    assert "Refusing to switch inside agy without --force" in val
