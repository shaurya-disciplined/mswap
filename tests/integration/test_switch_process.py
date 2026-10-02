"""Integration tests for process awareness, switch warnings, --wait, and --resume."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest

from mswap.agy.paths import agy_exe
from mswap.agy.process import AgyProcess
from mswap.agy.tokens import fingerprint
from mswap.cli.commands.add import run as run_add
from mswap.cli.commands.doctor import check_agy_sessions
from mswap.cli.commands.switch import run as run_switch
from mswap.cli.context import AppContext
from mswap.core.errors import NothingToDo, UnsafeOperation, UsageError
from mswap.core.models import Account
from mswap.core.store import live_target, slot_target
from mswap.util.http import json_response
from tests.conftest import make_blob


def _seed_two_accounts(ctx: AppContext) -> None:
    now = ctx.clock.now()
    blob1 = make_blob(1)
    blob2 = make_blob(2)
    ctx.vault.write(slot_target(1), blob1, "alice@example.com")
    ctx.vault.write(slot_target(2), blob2, "bob@example.com")
    ctx.vault.write(live_target(), blob1, "antigravity")

    accounts = [
        Account(
            slot=1, email="alice@example.com", fp=fingerprint(blob1), added_at=now, updated_at=now
        ),
        Account(
            slot=2, email="bob@example.com", fp=fingerprint(blob2), added_at=now, updated_at=now
        ),
    ]
    ctx.store.save(accounts)

    # Seed client.json in store root for token refresh without requiring agy.exe on disk
    client_path = Path(ctx.store.root) / "client.json"
    client_path.parent.mkdir(parents=True, exist_ok=True)
    client_path.write_text(
        json.dumps(
            {
                "client_id": (
                    "1071006060591-faketestclient12345678901234.apps.googleusercontent.com"
                ),
                "client_secret": "GOCSPX-FAKEsecret12345678901234",
                "secrets": ["GOCSPX-FAKEsecret12345678901234"],
            }
        ),
        encoding="utf-8",
    )


def test_switch_warns_when_agy_running(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    proc1 = AgyProcess(pid=1001, started_at=datetime(2026, 10, 2, 10, 0, tzinfo=UTC))
    proc2 = AgyProcess(pid=1002, started_at=None)
    ctx.procs = lambda: [proc1, proc2]

    args = argparse.Namespace(selector="2", force=False, wait=False, resume=False)
    rc = run_switch(ctx, args)
    assert rc == 0

    out = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    assert "Switched agy to account 2: bob@example.com" in out
    assert (
        "! agy is running (2 session(s)). "
        "Those sessions may keep using the old account until restarted."
    ) in out
    assert (
        "Finish the current turn, quit agy, then run `agy -c` "
        "to continue the same conversation on the new account."
    ) in out


def test_switch_normal_when_no_agy_running(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    ctx.procs = lambda: []

    args = argparse.Namespace(selector="2", force=False, wait=False, resume=False)
    rc = run_switch(ctx, args)
    assert rc == 0

    out = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    assert "Switched agy to account 2: bob@example.com" in out
    assert "New agy sessions use it. Restart any agy that's already running." in out
    assert "! agy is running" not in out


def test_switch_wait_polls_until_exit(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)

    # 1 proc -> 1 proc -> 0 procs
    p = AgyProcess(pid=999, started_at=None)
    states: list[list[AgyProcess]] = [[p], [p], []]
    calls: list[int] = []

    def fake_procs() -> list[AgyProcess]:
        calls.append(len(calls) + 1)
        if states:
            return states.pop(0)
        return []

    ctx.procs = fake_procs

    args = argparse.Namespace(selector="2", force=False, wait=True, wait_timeout=0.0, resume=False)
    rc = run_switch(ctx, args)
    assert rc == 0

    err = ctx.err.getvalue() if isinstance(ctx.err, StringIO) else ""
    assert "Waiting for agy to exit… (Ctrl+C to cancel)" in err
    out = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    assert "Switched agy to account 2: bob@example.com" in out


def test_switch_wait_timeout_raises(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    p = AgyProcess(pid=999, started_at=None)
    ctx.procs = lambda: [p]

    args = argparse.Namespace(selector="2", force=False, wait=True, wait_timeout=3.0, resume=False)
    with pytest.raises(NothingToDo) as exc_info:
        run_switch(ctx, args)

    assert exc_info.value.code == 2
    assert exc_info.value.message == "agy is still running."
    assert exc_info.value.hint == "Close it or drop --wait-timeout."


def test_switch_resume_executes_runner(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    executed_commands: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> int:
        executed_commands.append(cmd)
        return 42

    ctx.runner = fake_runner
    args = argparse.Namespace(selector="2", force=False, wait=False, resume=True)
    rc = run_switch(ctx, args)

    assert rc == 42
    assert len(executed_commands) == 1
    assert executed_commands[0] == [str(agy_exe()), "-c"]


def test_switch_resume_already_active(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    executed_commands: list[list[str]] = []
    ctx.runner = lambda cmd: executed_commands.append(cmd) or 0  # type: ignore[func-returns-value]
    args = argparse.Namespace(selector="1", force=False, wait=False, resume=True)
    rc = run_switch(ctx, args)
    assert rc == 0
    assert len(executed_commands) == 1
    assert executed_commands[0] == [str(agy_exe()), "-c"]
    out = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    assert "Already on account 1" in out


def test_switch_resume_and_json_conflict(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    ctx.json = True

    args = argparse.Namespace(selector="2", force=False, wait=False, resume=True)
    with pytest.raises(UsageError) as exc_info:
        run_switch(ctx, args)

    assert "Cannot use --resume with --json" in exc_info.value.message


def test_switch_inside_agy_without_force_raises(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    ctx.inside_agy = lambda: True

    args = argparse.Namespace(selector="2", force=False, wait=False, resume=False)
    with pytest.raises(UnsafeOperation) as exc_info:
        run_switch(ctx, args)

    assert "You're running mswap inside agy." in exc_info.value.message
    assert exc_info.value.hint == "Run with --force to switch anyway."


def test_switch_inside_agy_with_force_succeeds_and_warns(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    ctx.inside_agy = lambda: True

    args = argparse.Namespace(selector="2", force=True, wait=False, resume=False)
    rc = run_switch(ctx, args)
    assert rc == 0

    err = ctx.err.getvalue() if isinstance(ctx.err, StringIO) else ""
    expected_warn = (
        "! You're running mswap inside agy. Switching changes the login this agy session uses."
    )
    assert expected_warn in err
    out = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    assert "Switched agy to account 2: bob@example.com" in out


def test_switch_inside_agy_resume_refusal(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    ctx.inside_agy = lambda: True

    args = argparse.Namespace(selector="2", force=True, wait=False, resume=True)
    with pytest.raises(UsageError) as exc_info:
        run_switch(ctx, args)

    assert "Can't resume from inside agy." in exc_info.value.message
    assert exc_info.value.hint == "Run `mswap switch --resume` in a normal terminal."


def test_add_new_inside_agy_without_force_raises(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    ctx.inside_agy = lambda: True

    args = argparse.Namespace(new=True, force=False, alias=None)
    with pytest.raises(UnsafeOperation) as exc_info:
        run_add(ctx, args)

    assert "You're running mswap inside agy." in exc_info.value.message
    assert exc_info.value.hint == "Run with --force to switch anyway."


def test_add_new_inside_agy_with_force_succeeds_and_warns(ctx: AppContext) -> None:
    _seed_two_accounts(ctx)
    ctx.inside_agy = lambda: True

    # Configure FakeHttp for token refresh and whoami
    from mswap.util.http import FakeHttp

    assert isinstance(ctx.http, FakeHttp)
    ctx.http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"access_token": "ya29.FAKE-refreshed", "expires_in": 3600, "token_type": "Bearer"}
        ),
    )
    ctx.http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response({"email": "alice@example.com"}),
    )

    args = argparse.Namespace(new=True, force=True, alias=None)
    rc = run_add(ctx, args)
    assert rc == 0

    err = ctx.err.getvalue() if isinstance(ctx.err, StringIO) else ""
    expected_warn = (
        "! You're running mswap inside agy. Switching changes the login this agy session uses."
    )
    assert expected_warn in err
    out = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    assert "Updated account 1: alice@example.com" in out or "account 1" in out


def test_doctor_agy_sessions_check(ctx: AppContext) -> None:
    ctx.procs = lambda: []
    chk_empty = check_agy_sessions(ctx)
    assert chk_empty.id == "agy.sessions"
    assert chk_empty.status == "ok"
    assert chk_empty.message == "agy isn't running"

    ctx.procs = lambda: [
        AgyProcess(pid=101, started_at=None),
        AgyProcess(pid=102, started_at=None),
    ]
    chk_running = check_agy_sessions(ctx)
    assert chk_running.id == "agy.sessions"
    assert chk_running.status == "warn"
    assert chk_running.message == "2 agy session(s) running"
