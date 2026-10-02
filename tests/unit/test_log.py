"""Unit tests for mswap log command, audit trail formatting, and secret scanning."""

from __future__ import annotations

import argparse
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mswap.cli.commands.log import (
    format_line,
    format_local_time,
    format_summary,
    read_recent_events,
    run,
)
from mswap.cli.context import AppContext
from mswap.core.errors import UsageError
from mswap.core.events import Events
from mswap.ui.theme import Theme
from mswap.util.clock import FrozenClock
from mswap.util.redact import _TOKEN_PATTERNS
from mswap.vault.memory import MemoryVault


class DummyStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self) -> list[Any]:
        return []

    def save(self, _accounts: Any) -> None:
        pass


def _make_context(
    tmp_path: Path,
    *,
    json_out: bool = False,
    ascii_only: bool = False,
) -> AppContext:
    events_path = tmp_path / "events.log"
    out = io.StringIO()
    err = io.StringIO()
    theme = Theme(color=False, ascii=ascii_only)
    return AppContext(
        vault=MemoryVault(),
        http=None,  # type: ignore[arg-type]
        clock=FrozenClock(datetime(2026, 10, 2, 12, 0, tzinfo=UTC)),
        store=DummyStore(tmp_path),  # type: ignore[arg-type]
        env={},
        out=out,
        err=err,
        theme=theme,
        json=json_out,
        quiet=False,
        events=Events(events_path),
    )


# ---------------------------------------------------------------------------
# 1. format_local_time tests
# ---------------------------------------------------------------------------


def test_format_local_time_valid_iso() -> None:
    iso = "2026-10-02T10:00:00+00:00"
    formatted = format_local_time(iso)
    # Must format as YYYY-MM-DD HH:MM:SS in local timezone
    dt = datetime.fromisoformat(iso).astimezone()
    expected = dt.strftime("%Y-%m-%d %H:%M:%S")
    assert formatted == expected


def test_format_local_time_naive_iso() -> None:
    iso = "2026-10-02T10:00:00"
    formatted = format_local_time(iso)
    assert formatted == "2026-10-02 10:00:00"


def test_format_local_time_invalid_or_empty() -> None:
    assert format_local_time("") == ""
    assert format_local_time("not-a-date") == "not-a-date"


# ---------------------------------------------------------------------------
# 2. format_summary tests for each event kind
# ---------------------------------------------------------------------------


def test_format_summary_switch_autopilot() -> None:
    ev = {
        "event": "switch",
        "from_slot": 1,
        "to_slot": 2,
        "source": "autopilot",
        "focus": ["gemini"],
    }
    assert format_summary(ev) == "1 → 2 (autopilot)"


def test_format_summary_switch_manual() -> None:
    ev = {
        "event": "switch",
        "from_slot": 1,
        "to_slot": 2,
        "source": "manual",
    }
    assert format_summary(ev) == "1 → 2 (manual)"


def test_format_summary_switch_ascii() -> None:
    ev = {
        "event": "switch",
        "from_slot": 1,
        "to_slot": 2,
        "source": "autopilot",
    }
    assert format_summary(ev, ascii_only=True) == "1 -> 2 (autopilot)"


def test_format_summary_switch_none_slots() -> None:
    ev = {"event": "switch", "from_slot": None, "to_slot": 2}
    assert format_summary(ev) == "? → 2 (manual)"


def test_format_summary_hold() -> None:
    ev = {"event": "hold", "reason": "cooldown (240s left)"}
    assert format_summary(ev) == "cooldown (240s left)"


def test_format_summary_quarantine() -> None:
    ev = {"event": "quarantine", "slot": 3, "email": "carol@example.com"}
    assert format_summary(ev) == "account 3"


def test_format_summary_writeback_suspected() -> None:
    ev = {"event": "writeback_suspected", "from_slot": 1, "to_slot": 2}
    assert format_summary(ev) == "after switch 1 → 2"
    assert format_summary(ev, ascii_only=True) == "after switch 1 -> 2"


def test_format_summary_blocked() -> None:
    ev = {
        "event": "blocked",
        "reason": "every other account is at or above the threshold",
    }
    assert format_summary(ev) == "every other account is at or above the threshold"


def test_format_summary_hook_switch() -> None:
    ev = {
        "event": "hook_switch",
        "hook_action": "notify",
        "to_slot": 2,
        "reason": "account 2 has more quota",
    }
    assert format_summary(ev) == "target 2 (notify): account 2 has more quota"


def test_format_summary_sign_out() -> None:
    ev = {"event": "sign_out", "from_fp": "abcdef1234567890"}
    assert format_summary(ev) == "live sign-out (abcdef12...)"

    ev_no_fp = {"event": "sign_out"}
    assert format_summary(ev_no_fp) == "live sign-out"


def test_format_summary_error() -> None:
    ev = {"event": "error", "reason": "LockTimeout: another mswap process running"}
    assert format_summary(ev) == "LockTimeout: another mswap process running"


def test_format_summary_unknown_fallback() -> None:
    ev = {"event": "custom_event", "reason": "something happened"}
    assert format_summary(ev) == "something happened"


def test_format_line_layout() -> None:
    ev = {
        "at": "2026-10-02T14:30:00+00:00",
        "event": "switch",
        "from_slot": 1,
        "to_slot": 2,
        "source": "manual",
    }
    line = format_line(ev)
    local_time = format_local_time(ev["at"])
    expected = f"{local_time} {'switch':<18} 1 → 2 (manual)"
    assert line == expected


# ---------------------------------------------------------------------------
# 3. read_recent_events: rotation across events.log.1 + events.log in order
# ---------------------------------------------------------------------------


def test_read_recent_events_order_across_rotation(tmp_path: Path) -> None:
    log_file = tmp_path / "events.log"
    rot_file = tmp_path / "events.log.1"

    # Write 3 older events to events.log.1
    older = [
        {
            "at": "2026-10-02T10:00:00Z",
            "event": "switch",
            "from_slot": 1,
            "to_slot": 2,
            "source": "manual",
        },
        {"at": "2026-10-02T10:01:00Z", "event": "hold", "reason": "cooldown"},
        {"at": "2026-10-02T10:02:00Z", "event": "quarantine", "slot": 3},
    ]
    rot_file.write_text("\n".join(json.dumps(e) for e in older) + "\n", encoding="utf-8")

    # Write 2 newer events to events.log
    newer = [
        {
            "at": "2026-10-02T10:03:00Z",
            "event": "writeback_suspected",
            "from_slot": 1,
            "to_slot": 2,
        },
        {
            "at": "2026-10-02T10:04:00Z",
            "event": "switch",
            "from_slot": 2,
            "to_slot": 3,
            "source": "autopilot",
        },
    ]
    log_file.write_text("\n".join(json.dumps(e) for e in newer) + "\n", encoding="utf-8")

    # Read all 5 events
    events = read_recent_events(log_file, count=20)
    assert len(events) == 5
    # Order must be chronological: older first, newest last
    assert [e["event"] for e in events] == [
        "switch",
        "hold",
        "quarantine",
        "writeback_suspected",
        "switch",
    ]
    assert events[0]["from_slot"] == 1
    assert events[-1]["to_slot"] == 3


def test_read_recent_events_count_limit(tmp_path: Path) -> None:
    log_file = tmp_path / "events.log"
    all_events = [
        {"at": f"2026-10-02T10:{i:02d}:00Z", "event": "hold", "reason": f"step {i}"}
        for i in range(10)
    ]
    log_file.write_text("\n".join(json.dumps(e) for e in all_events) + "\n", encoding="utf-8")

    # Ask for last 3
    recent = read_recent_events(log_file, count=3)
    assert len(recent) == 3
    assert recent[0]["reason"] == "step 7"
    assert recent[1]["reason"] == "step 8"
    assert recent[2]["reason"] == "step 9"


def test_read_recent_events_count_zero(tmp_path: Path) -> None:
    log_file = tmp_path / "events.log"
    log_file.write_text('{"event": "switch"}\n', encoding="utf-8")
    assert read_recent_events(log_file, count=0) == []


def test_read_recent_events_missing_file(tmp_path: Path) -> None:
    log_file = tmp_path / "does_not_exist.log"
    assert read_recent_events(log_file, count=10) == []


def test_read_recent_events_handles_corrupt_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "events.log"
    content = "not json\n\n" + json.dumps({"event": "switch", "to_slot": 1}) + "\n{bad json\n"
    log_file.write_text(content, encoding="utf-8")
    events = read_recent_events(log_file, count=10)
    assert len(events) == 1
    assert events[0]["event"] == "switch"


# ---------------------------------------------------------------------------
# 4. CLI command run() tests
# ---------------------------------------------------------------------------


def test_cli_log_empty(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    args = argparse.Namespace(n=20)
    rc = run(ctx, args)
    assert rc == 0
    out = ctx.out.getvalue()  # type: ignore[union-attr]
    assert "No events logged yet." in out


def test_cli_log_empty_json(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path, json_out=True)
    args = argparse.Namespace(n=20)
    rc = run(ctx, args)
    assert rc == 0
    out = ctx.out.getvalue()  # type: ignore[union-attr]
    payload = json.loads(out)
    assert payload["schema"] == 1
    assert payload["ok"] is True
    assert payload["command"] == "log"
    assert payload["data"] == {"events": []}


def test_cli_log_negative_count_raises_usage_error(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    args = argparse.Namespace(n=-1)
    with pytest.raises(UsageError, match="-n must be non-negative"):
        run(ctx, args)


def test_cli_log_human_output(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    ctx.events.emit("switch", from_slot=1, to_slot=2, source="autopilot")
    ctx.events.emit("hold", reason="below 90% threshold")

    args = argparse.Namespace(n=20)
    rc = run(ctx, args)
    assert rc == 0

    lines = ctx.out.getvalue().strip().splitlines()  # type: ignore[union-attr]
    assert len(lines) == 2
    assert "switch" in lines[0]
    assert "1 → 2 (autopilot)" in lines[0]
    assert "hold" in lines[1]
    assert "below 90% threshold" in lines[1]


def test_cli_log_json_output(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path, json_out=True)
    ctx.events.emit("quarantine", slot=3, reason="invalid_grant")

    args = argparse.Namespace(n=5)
    rc = run(ctx, args)
    assert rc == 0

    out = ctx.out.getvalue()  # type: ignore[union-attr]
    payload = json.loads(out)
    assert payload["schema"] == 1
    assert payload["ok"] is True
    assert len(payload["data"]["events"]) == 1
    assert payload["data"]["events"][0]["event"] == "quarantine"
    assert payload["data"]["events"][0]["slot"] == 3


# ---------------------------------------------------------------------------
# 5. Secret scanning test (Sentinel requirement)
# ---------------------------------------------------------------------------


def test_events_log_no_secrets_scan(tmp_path: Path) -> None:
    """Verify that events.log contains zero unredacted secrets or tokens."""
    events_path = tmp_path / "events.log"
    events = Events(events_path)

    # Emit a variety of realistic events including sensitive fields that could leak
    events.emit(
        "switch",
        from_slot=1,
        to_slot=2,
        access_token="ya29.A0ARrdaM-SAMPLE-FAKE-TOKEN-12345",
        refresh_token="1//04SAMPLE-REFRESH-TOKEN-67890",
        client_secret="GOCSPX-SAMPLE-SECRET-ABCDE",
        jwt_token="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        email="alice@example.com",
    )
    events.emit(
        "error",
        reason='Failed to authenticate with "access_token": "ya29.LEAKED-INSIDE-ERROR"',
    )
    events.emit(
        "quarantine",
        slot=2,
        email="bob@example.com",
        reason="invalid_grant",
    )

    content = events_path.read_text(encoding="utf-8")

    # Assert that all secret patterns find 0 matches
    for pattern in _TOKEN_PATTERNS:
        matches = pattern.findall(content)
        assert len(matches) == 0, f"Found unredacted secret matching {pattern.pattern}: {matches}"

    # Verify no JSON secret key contains anything other than [REDACTED]
    import re

    unredacted_json_secrets = re.findall(
        r'"(access_token|refresh_token|id_token|client_secret)"\s*:\s*"(?!\[REDACTED\])([^"]+)"',
        content,
    )
    assert len(unredacted_json_secrets) == 0, (
        f"Found unredacted JSON secrets: {unredacted_json_secrets}"
    )

    # Verify that [REDACTED] placeholder was used
    assert "[REDACTED]" in content


def test_format_summary_hook_without_target() -> None:
    ev = {"event": "hook_hold", "reason": "staying on slot 1"}
    assert format_summary(ev) == "staying on slot 1"


def test_cli_log_count_none_defaults_to_20(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    args = argparse.Namespace(n=None)
    rc = run(ctx, args)
    assert rc == 0


def test_cli_log_fallback_to_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_HOME", str(tmp_path))
    ctx = _make_context(tmp_path)
    # Remove events attribute from ctx to trigger fallback
    del ctx.events  # type: ignore[misc]
    args = argparse.Namespace(n=20)
    rc = run(ctx, args)
    assert rc == 0
