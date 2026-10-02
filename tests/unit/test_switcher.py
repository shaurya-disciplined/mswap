"""Unit tests for switch transaction, target resolution, failure injection, and recovery."""

from __future__ import annotations

import argparse
import json
from io import StringIO
from pathlib import Path

import pytest

from mswap.agy.tokens import fingerprint
from mswap.cli.commands.switch import run as run_switch
from mswap.cli.context import AppContext
from mswap.core.errors import (
    CorruptState,
    MswapError,
    NothingToDo,
    UnsafeOperation,
    UsageError,
    VaultError,
)
from mswap.core.identity import fp_or_none
from mswap.core.models import Account, Quarantine
from mswap.core.store import (
    backup_last,
    backup_original,
    live_target,
    slot_target,
)
from mswap.core.switcher import recover, resolve_target, switch
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob


def _setup_accounts(
    ctx: AppContext,
    count: int = 2,
    *,
    disabled_slots: set[int] | None = None,
    quarantined_slots: set[int] | None = None,
    aliases: dict[int, str] | None = None,
) -> list[Account]:
    disabled_slots = disabled_slots or set()
    quarantined_slots = quarantined_slots or set()
    aliases = aliases or {}

    accounts: list[Account] = []
    now = ctx.clock.now()

    for i in range(1, count + 1):
        blob = make_blob(i)
        fp = fingerprint(blob)
        email = f"user{i}@example.com"
        quarantine = Quarantine("invalid_grant", now) if i in quarantined_slots else None
        acc = Account(
            slot=i,
            email=email,
            fp=fp,
            added_at=now,
            updated_at=now,
            alias=aliases.get(i),
            disabled=(i in disabled_slots),
            quarantined=quarantine,
        )
        accounts.append(acc)
        ctx.vault.write(slot_target(i), blob, email)

    ctx.store.save(accounts)
    return accounts


def test_switch_happy_path(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx, 2)
    blob1 = ctx.vault.read(slot_target(1))
    blob2 = ctx.vault.read(slot_target(2))
    assert blob1 is not None and blob2 is not None

    # Account 1 is active
    ctx.vault.write(live_target(), blob1, "antigravity")

    # Switch to account 2
    res = switch(ctx, "2")
    assert res.status == "switched"
    assert res.from_account == accounts[0]
    assert res.to_account == accounts[1]
    assert res.agy_running is False

    # Live target updated
    assert ctx.vault.read(live_target()) == blob2
    # Backups written
    assert ctx.vault.read(backup_last()) == blob1
    assert ctx.vault.read(backup_original()) == blob1
    # Account 1 slot refreshed with previous live
    assert ctx.vault.read(slot_target(1)) == blob1

    # Journal committed
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "committed"
    assert st["from_fp"] == accounts[0].fp
    assert st["to_fp"] == accounts[1].fp

    # Event emitted
    events_path = ctx.events.path
    assert events_path.exists()
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["event"] == "switch"
    assert ev["from_slot"] == 1
    assert ev["to_slot"] == 2
    assert ev["forced"] is False

    # Second switch to account 1: backup-original is preserved (not overwritten)
    ctx.vault.write(backup_original(), b"original-content", "antigravity")
    res2 = switch(ctx, "1")
    assert res2.status == "switched"
    assert ctx.vault.read(backup_original()) == b"original-content"
    assert ctx.vault.read(backup_last()) == blob2


def test_switch_already_active(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx, 2)
    blob1 = ctx.vault.read(slot_target(1))
    assert blob1 is not None
    ctx.vault.write(live_target(), blob1, "antigravity")

    vault = ctx.vault
    assert isinstance(vault, MemoryVault)
    initial_write_count = len([c for c in vault.calls if c[0] == "write"])

    res = switch(ctx, "1")
    assert res.status == "already_active"
    assert res.from_account == accounts[0]
    assert res.to_account == accounts[0]
    assert res.agy_running is False

    # No writes performed
    write_calls = [c for c in vault.calls if c[0] == "write"][initial_write_count:]
    assert len(write_calls) == 0


def test_switch_no_accounts_saved(ctx: AppContext) -> None:
    with pytest.raises(MswapError) as exc_info:
        switch(ctx, "1")
    assert "No accounts saved yet." in exc_info.value.message
    assert exc_info.value.hint == "Run `mswap add` first."


def test_switch_unknown_live_without_force(ctx: AppContext) -> None:
    _setup_accounts(ctx, 2)
    unknown_blob = make_blob(99)
    ctx.vault.write(live_target(), unknown_blob, "antigravity")

    vault = ctx.vault
    assert isinstance(vault, MemoryVault)
    calls_before = list(vault.calls)

    with pytest.raises(UnsafeOperation) as exc_info:
        switch(ctx, "2", force=False)

    err = exc_info.value
    assert err.code == 6
    assert "agy is signed in to an account mswap hasn't saved." in err.message
    assert "mswap add" in (err.hint or "")
    assert "mswap switch --force" in (err.hint or "")

    # Assert MemoryVault calls contain NO write after the check
    new_calls = vault.calls[len(calls_before) :]
    assert not any(action == "write" for action, _ in new_calls)


def test_switch_unknown_live_with_force(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx, 2)
    unknown_blob = make_blob(99)
    ctx.vault.write(live_target(), unknown_blob, "antigravity")

    res = switch(ctx, "2", force=True)
    assert res.status == "switched"
    assert res.from_account is None
    assert res.to_account == accounts[1]

    # Live target is now account 2
    assert ctx.vault.read(live_target()) == ctx.vault.read(slot_target(2))
    # backup-last holds the unknown login
    assert ctx.vault.read(backup_last()) == unknown_blob


def test_switch_live_absent_signed_out(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx, 2)
    blob2 = ctx.vault.read(slot_target(2))
    assert blob2 is not None

    # No live credential exists (agy signed out)
    assert ctx.vault.read(live_target()) is None

    res = switch(ctx, "2")
    assert res.status == "switched"
    assert res.from_account is None
    assert res.to_account == accounts[1]

    assert ctx.vault.read(live_target()) == blob2
    # No backups written since live was None
    assert ctx.vault.read(backup_last()) is None
    assert ctx.vault.read(backup_original()) is None


def test_switch_target_blob_missing(ctx: AppContext) -> None:
    _setup_accounts(ctx, 2)
    ctx.vault.delete(slot_target(2))

    with pytest.raises(CorruptState) as exc_info:
        switch(ctx, "2")
    assert "Saved login for account 2 is missing." in exc_info.value.message


def test_switch_target_blob_damaged(ctx: AppContext) -> None:
    _setup_accounts(ctx, 2)
    ctx.vault.write(slot_target(2), b'{"bad": "json"}', "user2@example.com")

    with pytest.raises(CorruptState) as exc_info:
        switch(ctx, "2")
    assert "Saved login is damaged." in exc_info.value.message


def test_switch_quarantined_account(ctx: AppContext) -> None:
    accounts = _setup_accounts(ctx, 2, quarantined_slots={2})
    blob1 = ctx.vault.read(slot_target(1))
    assert blob1 is not None
    ctx.vault.write(live_target(), blob1, "antigravity")

    with pytest.raises(UnsafeOperation) as exc_info:
        switch(ctx, "2", force=False)
    assert "is quarantined" in exc_info.value.message

    # With force=True it switches
    res = switch(ctx, "2", force=True)
    assert res.status == "switched"
    assert res.to_account == accounts[1]


@pytest.mark.parametrize("fail_n", [1, 2, 3, 4])
def test_switch_failure_injection(ctx: AppContext, fail_n: int, tmp_path: Path) -> None:
    """Failure injection with MemoryVault(fail_on_write=N) for N = 1, 2, 3, 4.

    After each failure, the live entry equals the ORIGINAL live blob (rollback)
    or the journal is failed with recovery able to restore it. Assert exact final state.
    """
    _setup_accounts(ctx, 2)
    original_live = make_blob(1)
    ctx.vault.write(live_target(), original_live, "antigravity")

    # Set up failing MemoryVault
    failing_vault = MemoryVault(fail_on_write=fail_n)
    for target in ctx.vault.list("mswaptest:"):
        blob = ctx.vault.read(target)
        if blob is not None:
            failing_vault.entries[target] = (blob, "antigravity")

    ctx_failing = AppContext(
        vault=failing_vault,
        http=ctx.http,
        clock=ctx.clock,
        store=ctx.store,
        env=ctx.env,
        out=ctx.out,
        err=ctx.err,
        theme=ctx.theme,
        json=ctx.json,
        quiet=ctx.quiet,
        lock=ctx.lock,
        journal=ctx.journal,
        events=ctx.events,
    )

    with pytest.raises(VaultError):
        switch(ctx_failing, "2")

    # Final state: live entry equals ORIGINAL live blob
    assert failing_vault.read(live_target()) == original_live

    # Journal is failed
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "failed"


def test_switch_readback_mismatch_triggers_rollback(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_accounts(ctx, 2)
    original_live = make_blob(1)
    ctx.vault.write(live_target(), original_live, "antigravity")

    orig_read = ctx.vault.read

    # Return different bytes when live_target is read back
    def mocked_read(target: str) -> bytes | None:
        if target == live_target() and ctx.journal.state() is not None:
            st = ctx.journal.state()
            if st and st.get("state") == "begun":
                return b"corrupted-bytes-different-from-target"
        return orig_read(target)

    monkeypatch.setattr(ctx.vault, "read", mocked_read)

    with pytest.raises(VaultError) as exc_info:
        switch(ctx, "2")
    assert "Read-back after write didn't match." in exc_info.value.message

    # Live was rolled back to original_live
    assert ctx.vault.read(live_target()) == original_live
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "failed"


def test_recovery_scenarios(ctx: AppContext) -> None:
    """Recovery: begun + live fp == to_fp → committed; begun + live unchanged → failed;

    begun + live == other + backup-last fp == live_fp → restored;
    any other combination → CorruptState.
    """
    _setup_accounts(ctx, 2)
    blob1 = make_blob(1)
    blob2 = make_blob(2)
    blob_other = make_blob(3)

    fp1 = fp_or_none(blob1)
    fp2 = fp_or_none(blob2)
    assert fp1 is not None and fp2 is not None

    # Case 0: No journal -> returns None
    assert recover(ctx) is None

    # Case 0b: Journal state committed or failed -> returns None
    ctx.journal.begin("switch", from_fp=fp1, to_fp=fp2, live_fp=fp1)
    ctx.journal.commit()
    assert recover(ctx) is None
    ctx.journal.clear()

    # Case A: begun + live_fp == to_fp -> committed
    ctx.vault.write(live_target(), blob2, "antigravity")
    ctx.journal.begin("switch", from_fp=fp1, to_fp=fp2, live_fp=fp1)
    msg = recover(ctx)
    assert msg is not None
    assert "switch had completed" in msg
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "committed"
    ctx.journal.clear()

    # Case B: begun + live_fp == saved live_fp (unchanged) -> failed
    ctx.vault.write(live_target(), blob1, "antigravity")
    ctx.journal.begin("switch", from_fp=fp1, to_fp=fp2, live_fp=fp1)
    msg = recover(ctx)
    assert msg is not None
    assert "marked failed" in msg
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "failed"
    ctx.journal.clear()

    # Case C: begun + live_fp == other + backup-last fp == live_fp -> restored
    ctx.vault.write(live_target(), blob_other, "antigravity")
    ctx.vault.write(backup_last(), blob1, "antigravity")
    ctx.journal.begin("switch", from_fp=fp1, to_fp=fp2, live_fp=fp1)
    msg = recover(ctx)
    assert msg is not None
    assert "restored previous login" in msg
    assert ctx.vault.read(live_target()) == blob1
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "failed"
    ctx.journal.clear()

    # Case D: any other combination -> CorruptState
    ctx.vault.write(live_target(), blob_other, "antigravity")
    ctx.vault.write(backup_last(), make_blob(4), "antigravity")
    ctx.journal.begin("switch", from_fp=fp1, to_fp=fp2, live_fp=fp1)
    with pytest.raises(CorruptState) as exc_info:
        recover(ctx)
    assert (
        "An interrupted switch transaction could not be safely recovered." in exc_info.value.message
    )


def test_target_resolution_and_rotation(ctx: AppContext) -> None:
    """Rotation: [1 active, 2 disabled, 3] → 3; [1, 2 quarantined] → NothingToDo;

    explicit "2" on a disabled account works; explicit quarantined without force → UnsafeOperation.
    """
    now = ctx.clock.now()

    # Rotation: [1 active, 2 disabled, 3] -> 3
    a1 = Account(1, "u1@example.com", "fp1", now, now)
    a2 = Account(2, "u2@example.com", "fp2", now, now, disabled=True)
    a3 = Account(3, "u3@example.com", "fp3", now, now)
    accounts = [a1, a2, a3]

    res = resolve_target(accounts, None, active=a1)
    assert res.slot == 3

    # Rotation wrap: [1, 2 disabled, 3 active] -> 1
    res_wrap = resolve_target(accounts, None, active=a3)
    assert res_wrap.slot == 1

    # Rotation with active=None -> lowest eligible (1)
    res_no_active = resolve_target(accounts, None, active=None)
    assert res_no_active.slot == 1

    # Rotation: [1, 2 quarantined] -> NothingToDo
    q = Quarantine("invalid_grant", now)
    a2_q = Account(2, "u2@example.com", "fp2", now, now, quarantined=q)
    accounts_q = [a1, a2_q]
    with pytest.raises(NothingToDo) as exc_info:
        resolve_target(accounts_q, None, active=a1)
    assert "Only one account is available to switch to." in exc_info.value.message

    # Rotation with 1 account saved total
    with pytest.raises(NothingToDo) as exc_info_one:
        resolve_target([a1], None, active=a1)
    assert "Only one account saved." in exc_info_one.value.message

    # Explicit "2" on disabled account works
    res_disabled = resolve_target(accounts, "2", active=a1)
    assert res_disabled.slot == 2

    # Explicit by email
    res_email = resolve_target(accounts, "U3@example.com", active=a1)
    assert res_email.slot == 3

    # Explicit by alias
    a1_alias = Account(1, "u1@example.com", "fp1", now, now, alias="work")
    res_alias = resolve_target([a1_alias, a2], "work", active=None)
    assert res_alias.slot == 1

    # Explicit missing
    with pytest.raises(UsageError) as exc_info:
        resolve_target(accounts, "99", active=a1)
    assert "No account matching '99'." in exc_info.value.message

    # Rotation with include_disabled=True
    res_inc_disabled = resolve_target(accounts, None, active=a1, include_disabled=True)
    assert res_inc_disabled.slot == 2


def test_switch_rotation_with_include_disabled(ctx: AppContext) -> None:
    _setup_accounts(ctx, 3, disabled_slots={2})
    blob1 = ctx.vault.read(slot_target(1))
    assert blob1 is not None
    ctx.vault.write(live_target(), blob1, "antigravity")

    # Normal rotation skips 2 -> goes to 3
    res_normal = switch(ctx)
    assert res_normal.to_account.slot == 3

    # Reset live to 1
    ctx.vault.write(live_target(), blob1, "antigravity")
    # With include_disabled_in_rotation=True -> goes to 2
    res_inc = switch(ctx, include_disabled_in_rotation=True)
    assert res_inc.to_account.slot == 2


def test_cli_switch_command(ctx: AppContext) -> None:
    _setup_accounts(ctx, 2)
    blob1 = ctx.vault.read(slot_target(1))
    assert blob1 is not None
    ctx.vault.write(live_target(), blob1, "antigravity")

    # 1. Switched human output
    args = argparse.Namespace(selector="2", force=False)
    rc = run_switch(ctx, args)
    assert rc == 0
    out_val = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    assert "Switched agy to account" in out_val
    assert "user2@example.com" in out_val
    assert "New agy sessions use it" in out_val

    # 2. Already active human output
    ctx.out = StringIO()
    args_same = argparse.Namespace(selector="2", force=False)
    rc_same = run_switch(ctx, args_same)
    assert rc_same == 0
    out_same = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    assert "Already on account 2: user2@example.com" in out_same

    # 3. JSON output
    ctx.out = StringIO()
    ctx.json = True
    args_json = argparse.Namespace(selector="1", force=False)
    rc_json = run_switch(ctx, args_json)
    assert rc_json == 0
    out_json = ctx.out.getvalue() if isinstance(ctx.out, StringIO) else ""
    parsed = json.loads(out_json)
    assert parsed["schema"] == 1
    assert parsed["ok"] is True
    assert parsed["command"] == "switch"
    assert parsed["data"]["status"] == "switched"
    assert parsed["data"]["from_slot"] == 2
    assert parsed["data"]["to_slot"] == 1
    assert parsed["data"]["agy_running"] is False

    # 4. Recovery message printed to stderr
    ctx.json = False
    ctx.out = StringIO()
    ctx.err = StringIO()
    fp1 = fingerprint(blob1)
    ctx.journal.begin("switch", from_fp=fp1, to_fp="dummy", live_fp=fp1)
    rc_rec = run_switch(ctx, argparse.Namespace(selector="1", force=False))
    assert rc_rec == 0
    err_rec = ctx.err.getvalue() if isinstance(ctx.err, StringIO) else ""
    assert "Recovered from interrupted switch" in err_rec


def test_sign_out_live_happy_path(ctx: AppContext) -> None:
    from mswap.core.switcher import sign_out_live

    blob1 = make_blob(1)
    ctx.vault.write(live_target(), blob1, "antigravity")

    sign_out_live(ctx)

    assert ctx.vault.read(live_target()) is None
    assert ctx.vault.read(backup_last()) == blob1

    st = ctx.journal.state()
    assert st is not None
    assert st["op"] == "sign_out"
    assert st["state"] == "committed"

    events_path = ctx.events.path
    assert events_path.exists()
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line).get("event") == "sign_out" for line in lines)


def test_sign_out_live_when_none(ctx: AppContext) -> None:
    from mswap.core.switcher import sign_out_live

    assert ctx.vault.read(live_target()) is None
    sign_out_live(ctx)
    assert ctx.vault.read(live_target()) is None
    assert ctx.journal.state() is None


def test_sign_out_live_failure_rollback(ctx: AppContext) -> None:
    from mswap.core.switcher import sign_out_live

    blob1 = make_blob(1)
    ctx.vault.write(live_target(), blob1, "antigravity")

    def fail_delete(target: str) -> bool:
        raise RuntimeError("Delete failed")

    ctx.vault.delete = fail_delete  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="Delete failed"):
        sign_out_live(ctx)

    assert ctx.vault.read(live_target()) == blob1
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "failed"


def test_recover_sign_out_completed(ctx: AppContext) -> None:
    blob1 = make_blob(1)
    fp1 = fingerprint(blob1)
    ctx.journal.begin("sign_out", from_fp=fp1, to_fp=None, live_fp=fp1)
    # live is None (sign_out deleted it)
    assert ctx.vault.read(live_target()) is None

    msg = recover(ctx)
    assert msg == "Recovered from interrupted sign-out: sign-out had completed."
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "committed"


def test_recover_sign_out_never_happened(ctx: AppContext) -> None:
    blob1 = make_blob(1)
    fp1 = fingerprint(blob1)
    ctx.vault.write(live_target(), blob1, "antigravity")
    ctx.journal.begin("sign_out", from_fp=fp1, to_fp=None, live_fp=fp1)

    msg = recover(ctx)
    assert (
        msg == "Recovered from interrupted sign-out: sign-out never happened and was marked failed."
    )
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "failed"


def test_recover_sign_out_restored_from_backup(ctx: AppContext) -> None:
    blob1 = make_blob(1)
    blob2 = make_blob(2)
    fp1 = fingerprint(blob1)
    # live has something else, but backup_last has blob1
    ctx.vault.write(live_target(), blob2, "antigravity")
    ctx.vault.write(backup_last(), blob1, "antigravity")
    ctx.journal.begin("sign_out", from_fp=fp1, to_fp=None, live_fp=fp1)

    msg = recover(ctx)
    assert msg == "Recovered from interrupted sign-out: restored previous login from backup."
    assert ctx.vault.read(live_target()) == blob1
    st = ctx.journal.state()
    assert st is not None
    assert st["state"] == "failed"


def test_recover_sign_out_corrupt_state(ctx: AppContext) -> None:
    blob1 = make_blob(1)
    blob2 = make_blob(2)
    blob3 = make_blob(3)
    fp1 = fingerprint(blob1)
    ctx.vault.write(live_target(), blob2, "antigravity")
    ctx.vault.write(backup_last(), blob3, "antigravity")
    ctx.journal.begin("sign_out", from_fp=fp1, to_fp=None, live_fp=fp1)

    with pytest.raises(CorruptState) as exc_info:
        recover(ctx)
    expected_msg = "An interrupted sign-out transaction could not be safely recovered."
    assert expected_msg in exc_info.value.message
