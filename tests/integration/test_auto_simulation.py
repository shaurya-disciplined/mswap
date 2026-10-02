"""Integration simulation test for autopilot runner across a 2-hour scripted timeline."""

from __future__ import annotations

from datetime import datetime, timedelta

from mswap.agy.tokens import fingerprint
from mswap.cli.context import AppContext
from mswap.core.autopilot import AutopilotStateStore, tick
from mswap.core.models import Account, Bucket, Pool, QuotaSnapshot
from mswap.core.policy import Decision
from mswap.core.policy import Settings as PolicySettings
from mswap.core.store import live_target, slot_target
from mswap.core.usage_cache import UsageCache
from tests.conftest import make_blob


def _make_snapshot(rem: float, now: datetime) -> QuotaSnapshot:
    bucket = Bucket(window="5h", remaining=rem, reset_at=now + timedelta(hours=3))
    pool = Pool(key="gemini", name="Gemini", buckets=(bucket,))
    return QuotaSnapshot(fetched_at=now, pools=(pool,))


def test_scripted_2h_simulation_timeline(ctx: AppContext) -> None:
    """Scripted 2-hour timeline simulation test.

    Usage falls on account 1, account 2 is fresh.
    Asserts: exactly one switch at the right tick, and no flip-back.
    """
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
    # Account 1 starts active
    ctx.vault.write(live_target(), make_blob(1), "antigravity")

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    cache = UsageCache(ctx.store.root / "usage.json")

    switch_events: list[tuple[int, Decision, datetime]] = []

    # 120 ticks = 2 hours at 60 seconds per tick
    for minute in range(120):
        tick_time = ctx.clock.now()

        # Simulate account 1 quota burning down over time
        if minute < 20:
            rem1 = 0.50  # 50% used
        elif minute < 40:
            rem1 = 0.30  # 70% used
        elif minute < 50:
            rem1 = 0.15  # 85% used
        else:
            rem1 = 0.08  # 92% used (exceeds 90% threshold)

        rem2 = 0.90  # Account 2 remains fresh (10% used)

        # Update cached usage snapshots at each tick
        cache.put_snapshot(accounts[0].fp, _make_snapshot(rem1, tick_time), tick_time)
        cache.put_snapshot(accounts[1].fp, _make_snapshot(rem2, tick_time), tick_time)

        decision = tick(ctx, settings, dry_run=False, force=False)

        if decision.kind == "switch":
            switch_events.append((minute, decision, tick_time))

        # Advance clock by 60 seconds
        ctx.clock.advance(60)

    # Verification:
    # 1. Exactly one switch occurred across the entire 2 hours
    assert len(switch_events) == 1, (
        f"Expected exactly 1 switch, got {len(switch_events)}: {switch_events}"
    )

    # 2. The switch happened at minute 50 when remaining fell to 0.08 (92% used >= 90% threshold)
    switch_min, switch_dec, _ = switch_events[0]
    assert switch_min == 50
    assert switch_dec.target_slot == 2

    # 3. No flip-back: account 2 remains active at the end of the 2-hour timeline
    final_live = ctx.vault.read(live_target())
    assert final_live == make_blob(2)

    # 4. Autopilot state confirms account 2 is target and last_from_slot is 1
    state = AutopilotStateStore(ctx.store.root / "autopilot.json").load()
    assert state.last_from_slot == 1
    assert state.last_to_slot == 2
