"""Unit tests for core/autopilot.py state store, runner tick, and dry-run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mswap.agy.tokens import fingerprint
from mswap.cli.context import AppContext
from mswap.core.autopilot import AutopilotState, AutopilotStateStore, record_switch_state, tick
from mswap.core.errors import CorruptState
from mswap.core.models import Account, Bucket, Pool, QuotaSnapshot
from mswap.core.policy import Settings as PolicySettings
from mswap.core.store import live_target, slot_target
from mswap.core.usage_cache import UsageCache
from tests.conftest import make_blob


def _make_snapshot(rem: float, now: datetime) -> QuotaSnapshot:
    bucket = Bucket(window="5h", remaining=rem, reset_at=now + timedelta(hours=3))
    pool = Pool(key="gemini", name="Gemini", buckets=(bucket,))
    return QuotaSnapshot(fetched_at=now, pools=(pool,))


def _setup_test_accounts(ctx: AppContext) -> list[Account]:
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
    # Set slot 1 as active
    ctx.vault.write(live_target(), make_blob(1), "antigravity")
    return accounts


def test_autopilot_state_store_missing_returns_default(tmp_path: Path) -> None:
    store = AutopilotStateStore(tmp_path / "autopilot.json")
    st = store.load()
    assert st.last_switch_at is None
    assert st.last_from_slot is None
    assert st.last_to_slot is None
    assert st.writeback_suspected_at is None
    assert st.last_decision is None


def test_autopilot_state_store_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "autopilot.json"
    store = AutopilotStateStore(p)
    now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    wb_now = datetime(2026, 10, 2, 14, 5, tzinfo=UTC)
    st = AutopilotState(
        last_switch_at=now,
        last_from_slot=1,
        last_to_slot=2,
        prev_active_remaining={"gemini": 0.45},
        writeback_suspected_at=wb_now,
        last_decision={"event": "switch", "to_slot": 2},
    )
    store.save(st)

    loaded = store.load()
    assert loaded.last_switch_at == now
    assert loaded.last_from_slot == 1
    assert loaded.last_to_slot == 2
    assert loaded.prev_active_remaining == {"gemini": 0.45}
    assert loaded.writeback_suspected_at == wb_now
    assert loaded.last_decision == {"event": "switch", "to_slot": 2}


def test_autopilot_state_store_corrupt_raises_corrupt_state(tmp_path: Path) -> None:
    p = tmp_path / "autopilot.json"
    p.write_text("invalid json {", encoding="utf-8")
    store = AutopilotStateStore(p)
    with pytest.raises(CorruptState):
        store.load()


def test_record_switch_state(tmp_path: Path) -> None:
    p = tmp_path / "autopilot.json"
    now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    record_switch_state(p, from_slot=1, to_slot=2, now=now)

    store = AutopilotStateStore(p)
    st = store.load()
    assert st.last_switch_at == now
    assert st.last_from_slot == 1
    assert st.last_to_slot == 2
    assert st.writeback_suspected_at is None


def test_tick_switch_decision(ctx: AppContext) -> None:
    accounts = _setup_test_accounts(ctx)
    now = ctx.clock.now()

    # Slot 1 is exhausted (0.05 remaining = 95% used >= 90% threshold)
    # Slot 2 has 0.90 remaining (10% used)
    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=False, force=False)

    assert decision.kind == "switch"
    assert decision.target_slot == 2

    # Check live target was switched in vault
    live = ctx.vault.read(live_target())
    assert live == make_blob(2)

    # Check state was saved
    store = AutopilotStateStore(ctx.store.root / "autopilot.json")
    st = store.load()
    assert st.last_switch_at == now
    assert st.last_from_slot == 1
    assert st.last_to_slot == 2
    assert st.last_decision is not None
    assert st.last_decision["event"] == "switch"
    assert st.last_decision["to_slot"] == 2
    assert st.last_decision["dry_run"] is False


def test_tick_dry_run_never_writes_vault(ctx: AppContext) -> None:
    accounts = _setup_test_accounts(ctx)
    now = ctx.clock.now()

    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    # Clear calls on MemoryVault recorded during setup
    ctx.vault.calls.clear()

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=True, force=False)

    assert decision.kind == "switch"
    assert decision.target_slot == 2

    # Assert MemoryVault.calls has NO writes
    writes = [c for c in ctx.vault.calls if c[0] == "write"]
    assert len(writes) == 0, f"Expected no writes during dry-run, found: {writes}"

    # Verify live target is still slot 1
    live = ctx.vault.read(live_target())
    assert live == make_blob(1)

    # Check state recorded dry_run flag
    store = AutopilotStateStore(ctx.store.root / "autopilot.json")
    st = store.load()
    assert st.last_decision is not None
    assert st.last_decision["dry_run"] is True
    # last_switch_at should NOT be recorded as now on dry-run
    assert st.last_switch_at is None


def test_tick_hold_below_threshold(ctx: AppContext) -> None:
    accounts = _setup_test_accounts(ctx)
    now = ctx.clock.now()

    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.80, now), now)  # 20% used < 90%
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=False, force=False)

    assert decision.kind == "hold"
    assert "below the 90% threshold" in decision.reason
    assert decision.target_slot is None

    # Live target unchanged
    live = ctx.vault.read(live_target())
    assert live == make_blob(1)


def test_tick_blocked_all_exhausted(ctx: AppContext) -> None:
    accounts = _setup_test_accounts(ctx)
    now = ctx.clock.now()

    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)  # 95% used
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.05, now), now)  # 95% used

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=False, force=False)

    assert decision.kind == "blocked"
    assert "every other account is at or above the threshold" in decision.reason
    assert decision.target_slot is None


def test_tick_inside_agy_without_force_converts_to_hold(ctx: AppContext) -> None:
    accounts = _setup_test_accounts(ctx)
    now = ctx.clock.now()

    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    # Simulate running inside agy
    ctx.inside_agy = lambda: True

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=False, force=False)

    assert decision.kind == "hold"
    assert "running inside agy; pass --force to allow" in decision.reason

    # Vault was NOT modified
    live = ctx.vault.read(live_target())
    assert live == make_blob(1)


def test_tick_inside_agy_with_force_allows_switch(ctx: AppContext) -> None:
    accounts = _setup_test_accounts(ctx)
    now = ctx.clock.now()

    cache = UsageCache(ctx.store.root / "usage.json")
    cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)
    cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

    ctx.inside_agy = lambda: True

    settings = PolicySettings(threshold=90, margin=10, cooldown_s=300)
    decision = tick(ctx, settings, dry_run=False, force=True)

    assert decision.kind == "switch"
    assert decision.target_slot == 2

    live = ctx.vault.read(live_target())
    assert live == make_blob(2)
