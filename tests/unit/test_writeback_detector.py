"""Unit tests for the write-back detector and doctor autopilot.writeback check."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from mswap.agy.tokens import fingerprint
from mswap.cli.commands.doctor import check_autopilot_writeback
from mswap.cli.context import AppContext
from mswap.core.autopilot import AutopilotState, AutopilotStateStore, tick
from mswap.core.models import Account, Bucket, Pool, QuotaSnapshot
from mswap.core.policy import Settings as PolicySettings
from mswap.core.store import live_target, slot_target
from mswap.core.switcher import switch
from mswap.core.usage_cache import UsageCache
from tests.conftest import make_blob


def _make_snapshot(rem: float, now: datetime) -> QuotaSnapshot:
    bucket = Bucket(window="5h", remaining=rem, reset_at=now + timedelta(hours=3))
    pool = Pool(key="gemini", name="Gemini", buckets=(bucket,))
    return QuotaSnapshot(fetched_at=now, pools=(pool,))


def _setup_two_accounts(ctx: AppContext) -> list[Account]:
    now = ctx.clock.now()
    accounts: list[Account] = []
    for i in (1, 2):
        blob = make_blob(i)
        fp = fingerprint(blob)
        acc = Account(
            slot=i,
            email=f"user{i}@example.com",
            fp=fp,
            added_at=now,
            updated_at=now,
        )
        accounts.append(acc)
        ctx.vault.write(slot_target(i), blob, f"user{i}@example.com")

    ctx.store.save(accounts)
    return accounts


def test_writeback_detected_when_login_reverts_within_2h(ctx: AppContext) -> None:
    accounts = _setup_two_accounts(ctx)
    t0 = ctx.clock.now()

    # Pre-condition: autopilot previously switched from slot 1 to slot 2 30 minutes ago
    switch_time = t0 - timedelta(minutes=30)
    store = AutopilotStateStore(ctx.store.root / "autopilot.json")
    store.save(
        AutopilotState(
            last_switch_at=switch_time,
            last_from_slot=1,
            last_to_slot=2,
            writeback_suspected_at=None,
        )
    )

    # But active login in vault has reverted to slot 1 (e.g. agy wrote back old login)
    ctx.vault.write(live_target(), make_blob(1), "antigravity")

    # Both accounts have fresh usage data
    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, t0), t0)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, t0), t0)

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=False, force=False)

    # Assert hold decision with writeback reason
    assert decision.kind == "hold"
    assert "agy seems to have switched the login back" in decision.reason
    assert decision.target_slot is None

    # Assert state updated with writeback_suspected_at = now
    st = store.load()
    assert st.writeback_suspected_at == t0

    # Assert writeback_suspected event emitted in events.log
    events_file = ctx.store.root / "events.log"
    assert events_file.exists()
    lines = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    wb_events = [e for e in lines if e.get("event") == "writeback_suspected"]
    assert len(wb_events) == 1
    assert wb_events[0]["from_slot"] == 1
    assert wb_events[0]["to_slot"] == 2


def test_writeback_holds_until_2x_cooldown_passes(ctx: AppContext) -> None:
    accounts = _setup_two_accounts(ctx)
    t0 = ctx.clock.now()

    # Write-back detected at t0, cooldown is 300s -> hold for 600s
    store = AutopilotStateStore(ctx.store.root / "autopilot.json")
    store.save(
        AutopilotState(
            last_switch_at=t0 - timedelta(minutes=10),
            last_from_slot=1,
            last_to_slot=2,
            writeback_suspected_at=t0,
        )
    )
    # Active is slot 1
    ctx.vault.write(live_target(), make_blob(1), "antigravity")

    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, t0), t0)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, t0), t0)

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)

    # 1. At t0 + 500s (< 600s): still holds
    ctx.clock.advance(500)
    now_500 = ctx.clock.now()
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now_500), now_500)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now_500), now_500)

    dec_500 = tick(ctx, settings, dry_run=False, force=False)
    assert dec_500.kind == "hold"
    assert "agy seems to have switched the login back" in dec_500.reason

    # 2. At t0 + 601s (> 600s): 2*cooldown has passed, and if switch is > 2h old
    # Advance clock past 2 hours from last_switch_at
    ctx.clock.advance(7000)
    now_past = ctx.clock.now()
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now_past), now_past)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now_past), now_past)

    dec_past = tick(ctx, settings, dry_run=False, force=False)
    # Now write-back hold has expired, normal switch occurs
    assert dec_past.kind == "switch"
    assert dec_past.target_slot == 2


def test_writeback_not_detected_if_switch_older_than_2h(ctx: AppContext) -> None:
    accounts = _setup_two_accounts(ctx)
    t0 = ctx.clock.now()

    # Switch happened 2 hours and 1 minute ago (7260s)
    switch_time = t0 - timedelta(seconds=7260)
    store = AutopilotStateStore(ctx.store.root / "autopilot.json")
    store.save(
        AutopilotState(
            last_switch_at=switch_time,
            last_from_slot=1,
            last_to_slot=2,
            writeback_suspected_at=None,
        )
    )

    ctx.vault.write(live_target(), make_blob(1), "antigravity")

    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, t0), t0)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, t0), t0)

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=False, force=False)

    # Since > 2h, write-back detector is not triggered; it evaluates slot 1 as normal active account
    # and switches to slot 2
    assert decision.kind == "switch"
    assert decision.target_slot == 2


def test_manual_switch_updates_state_and_does_not_trigger_writeback(ctx: AppContext) -> None:
    accounts = _setup_two_accounts(ctx)
    # Active is slot 1
    ctx.vault.write(live_target(), make_blob(1), "antigravity")

    # Perform a manual switch to slot 2
    res = switch(ctx, "2")
    assert res.status == "switched"

    # Verify switcher recorded state in autopilot.json
    store = AutopilotStateStore(ctx.store.root / "autopilot.json")
    st = store.load()
    assert st.last_switch_at == ctx.clock.now()
    assert st.last_from_slot == 1
    assert st.last_to_slot == 2
    assert st.writeback_suspected_at is None

    # Now run tick: active is slot 2 (target), so write-back detector does NOT fire
    now = ctx.clock.now()
    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.50, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.50, now), now)

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=False, force=False)

    # Immediately after switch: holds due to normal cooldown, NOT write-back!
    assert decision.kind == "hold"
    assert "cooldown" in decision.reason

    # Advance clock past cooldown (301s), but still well within 2 hours
    ctx.clock.advance(301)
    now_past = ctx.clock.now()
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.50, now_past), now_past)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.50, now_past), now_past)

    decision_past = tick(ctx, settings, dry_run=False, force=False)
    # Now past cooldown: holds due to threshold, NOT write-back!
    assert decision_past.kind == "hold"
    assert "below the 90% threshold" in decision_past.reason


def test_doctor_autopilot_writeback_check(ctx: AppContext) -> None:
    # 1. Missing autopilot.json -> status ok
    chk_missing = check_autopilot_writeback(ctx)
    assert chk_missing.status == "ok"
    assert chk_missing.id == "autopilot.writeback"

    # 2. writeback_suspected_at within 24h -> status warn
    store = AutopilotStateStore(ctx.store.root / "autopilot.json")
    now = ctx.clock.now()
    store.save(
        AutopilotState(
            last_switch_at=now - timedelta(hours=2),
            last_from_slot=1,
            last_to_slot=2,
            writeback_suspected_at=now - timedelta(hours=1),
        )
    )
    chk_warn = check_autopilot_writeback(ctx)
    assert chk_warn.status == "warn"
    assert "write-back suspected recently" in chk_warn.message
    assert "Restart agy after switching" in (chk_warn.hint or "")

    # 3. writeback_suspected_at older than 24h -> status ok
    store.save(
        AutopilotState(
            last_switch_at=now - timedelta(hours=30),
            last_from_slot=1,
            last_to_slot=2,
            writeback_suspected_at=now - timedelta(hours=25),
        )
    )
    chk_old = check_autopilot_writeback(ctx)
    assert chk_old.status == "ok"
    assert "no write-back detected" in chk_old.message


def test_doctor_run_includes_writeback_check(ctx: AppContext) -> None:
    import argparse

    from mswap.cli.commands.doctor import run as run_doctor

    _setup_two_accounts(ctx)
    store = AutopilotStateStore(ctx.store.root / "autopilot.json")
    now = ctx.clock.now()
    store.save(
        AutopilotState(
            last_switch_at=now - timedelta(hours=2),
            last_from_slot=1,
            last_to_slot=2,
            writeback_suspected_at=now - timedelta(hours=1),
        )
    )
    args = argparse.Namespace(repair=False, online=False)
    run_doctor(ctx, args)
    out = ctx.out.getvalue()
    assert "write-back suspected recently" in out
