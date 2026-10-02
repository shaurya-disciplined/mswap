"""Table tests for mswap autopilot policy engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mswap.core.models import Bucket, Pool, QuotaSnapshot
from mswap.core.policy import Candidate, Decision, Settings, State, decide


def _make_snapshot(
    fetched_at: datetime,
    gemini_remaining: float | None = 1.0,
    gemini_reset: datetime | None = None,
    gemini_weekly_remaining: float | None = 1.0,
    gemini_weekly_reset: datetime | None = None,
    p3_remaining: float | None = 1.0,
    p3_reset: datetime | None = None,
    p3_weekly_remaining: float | None = 1.0,
    p3_weekly_reset: datetime | None = None,
) -> QuotaSnapshot:
    """Helper to construct a QuotaSnapshot with predictable pool buckets."""
    pools: list[Pool] = []
    if gemini_remaining is not None:
        buckets: list[Bucket] = [
            Bucket(window="5h", remaining=gemini_remaining, reset_at=gemini_reset)
        ]
        if gemini_weekly_remaining is not None:
            buckets.append(
                Bucket(
                    window="weekly",
                    remaining=gemini_weekly_remaining,
                    reset_at=gemini_weekly_reset,
                )
            )
        pools.append(
            Pool(
                key="gemini",
                name="Gemini",
                buckets=tuple(buckets),
            )
        )
    if p3_remaining is not None:
        buckets = [Bucket(window="5h", remaining=p3_remaining, reset_at=p3_reset)]
        if p3_weekly_remaining is not None:
            buckets.append(
                Bucket(
                    window="weekly",
                    remaining=p3_weekly_remaining,
                    reset_at=p3_weekly_reset,
                )
            )
        pools.append(
            Pool(
                key="3p",
                name="Claude & GPT",
                buckets=tuple(buckets),
            )
        )
    return QuotaSnapshot(fetched_at=fetched_at, pools=tuple(pools))


def test_rule1_no_active_slot() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings()
    st = State()
    snap = _make_snapshot(now, gemini_remaining=0.5)
    cands = [Candidate(slot=1, disabled=False, quarantined=False, snapshot=snap, fetched_at=now)]
    decision, next_state = decide(cands, active_slot=None, s=s, st=st, now=now)

    assert decision == Decision(
        kind="hold",
        target_slot=None,
        reason="agy isn't signed in to a saved account",
        focus=(),
        active_pressure=None,
        target_pressure=None,
    )
    assert next_state == st


def test_rule2_active_not_in_candidates() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings()
    st = State()
    cands = [
        Candidate(
            slot=2,
            disabled=False,
            quarantined=False,
            snapshot=_make_snapshot(now),
            fetched_at=now,
        )
    ]
    decision, next_state = decide(cands, active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert decision.reason == "no recent usage data for the active account"
    assert decision.target_slot is None
    assert next_state == st


def test_rule2_active_snapshot_is_none() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings()
    st = State()
    cands = [
        Candidate(
            slot=1,
            disabled=False,
            quarantined=False,
            snapshot=None,
            fetched_at=now,
        )
    ]
    decision, next_state = decide(cands, active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert decision.reason == "no recent usage data for the active account"
    assert next_state == st


def test_rule2_active_fetched_at_is_none() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings()
    st = State()
    snap = _make_snapshot(now)
    cands = [
        Candidate(
            slot=1,
            disabled=False,
            quarantined=False,
            snapshot=snap,
            fetched_at=None,
        )
    ]
    decision, next_state = decide(cands, active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert decision.reason == "no recent usage data for the active account"
    assert next_state == st


def test_rule2_active_stale_data() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(max_data_age_s=900)
    st = State()
    old = now - timedelta(seconds=901)
    cands = [
        Candidate(
            slot=1,
            disabled=False,
            quarantined=False,
            snapshot=_make_snapshot(old),
            fetched_at=old,
        )
    ]
    decision, next_state = decide(cands, active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert decision.reason == "no recent usage data for the active account"
    assert next_state == st


def test_rule4_cooldown_boundary_hold() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, cooldown_s=300)
    # 299s elapsed -> 1s left
    st = State(last_switch_at=now - timedelta(seconds=299))
    # Active account is at 95% used (0.05 remaining)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.8, p3_remaining=0.8),
        fetched_at=now,
    )
    decision, next_state = decide([c1, c2], active_slot=1, s=s, st=st, now=now)

    assert decision.kind == "hold"
    assert decision.reason == "cooldown (1s left)"
    assert decision.target_slot is None
    # Rule 3 specifies state.prev_active_remaining is always updated
    assert "gemini" in next_state.prev_active_remaining


def test_rule4_cooldown_boundary_proceed() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, cooldown_s=300)
    # Exactly 300s elapsed -> proceeds to switch
    st = State(last_switch_at=now - timedelta(seconds=300))
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.8, p3_remaining=0.8),
        fetched_at=now,
    )
    decision, next_state = decide([c1, c2], active_slot=1, s=s, st=st, now=now)

    assert decision.kind == "switch"
    assert decision.target_slot == 2
    assert next_state.last_switch_at == now
    assert next_state.last_from_slot == 1
    assert next_state.last_to_slot == 2


def test_rule5_below_threshold_hold() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10)
    st = State()
    # 20% remaining = 80% used < 90% threshold
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.2, p3_remaining=0.3),
        fetched_at=now,
    )
    decision, _ = decide([c1], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert decision.reason == "80% used, below the 90% threshold"
    assert decision.target_slot is None


def test_rule5_at_exact_threshold_triggers() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10)
    st = State()
    # 10% remaining = exactly 90% used (P_a == T)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.1, p3_remaining=0.1),
        fetched_at=now,
    )
    # Candidate 2 is 20% used (80% remaining), margin 70 >= 10
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.8, p3_remaining=0.8),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.target_slot == 2
    assert "switching to the account with the most left (20% used)" in decision.reason


def test_rule5_margin_boundary_switch() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10)
    st = State()
    # Active is 90% used (0.10 remaining)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.10, p3_remaining=0.10),
        fetched_at=now,
    )
    # Candidate is 80% used (0.20 remaining) -> margin = 90 - 80 = exactly 10
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.20, p3_remaining=0.20),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.target_slot == 2


def test_rule5_margin_insufficient_hold() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10)
    st = State()
    # Active is 92% used (0.08 remaining)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.08, p3_remaining=0.08),
        fetched_at=now,
    )
    # Best candidate is 85% used (0.15 remaining) -> delta = 7% < 10%
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.15, p3_remaining=0.15),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert decision.reason == "no account is at least 10 points better"
    assert decision.target_slot is None


def test_rule5_all_others_exhausted_blocked() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10)
    st = State()
    # Active is 95% used
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    # Candidate 2 is 92% used (>= threshold 90%)
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.08, p3_remaining=0.08),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "blocked"
    assert (
        decision.reason == "every other account is at or above the threshold, or has no fresh data"
    )
    assert decision.target_slot is None


def test_disabled_candidate_excluded() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10)
    st = State()
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    # Candidate 2 has 100% remaining but is disabled
    c2 = Candidate(
        slot=2,
        disabled=True,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=1.0, p3_remaining=1.0),
        fetched_at=now,
    )
    # Candidate 3 has 50% remaining and is enabled
    c3 = Candidate(
        slot=3,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.5, p3_remaining=0.5),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2, c3], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.target_slot == 3


def test_quarantined_candidate_excluded() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10)
    st = State()
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    # Candidate 2 is quarantined
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=True,
        snapshot=_make_snapshot(now, gemini_remaining=1.0, p3_remaining=1.0),
        fetched_at=now,
    )
    c3 = Candidate(
        slot=3,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.5, p3_remaining=0.5),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2, c3], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.target_slot == 3


def test_stale_candidate_excluded() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, max_data_age_s=900)
    st = State()
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    # Candidate 2 is stale (> 900s)
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now - timedelta(seconds=901), gemini_remaining=1.0),
        fetched_at=now - timedelta(seconds=901),
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "blocked"


def test_candidate_missing_focus_pool_counts_as_pressure_1_0() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, focus="gemini")
    st = State()
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    # Candidate 2 only has 3p pool, missing gemini
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=None, p3_remaining=0.8),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    # Candidate 2 has pressure 1.0 (>= 0.90), so pool is empty -> blocked
    assert decision.kind == "blocked"


def test_focus_gemini_ignores_3p_pressure() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, focus="gemini")
    st = State()
    # Active: gemini is 95% used (0.05 remaining), 3p is only 10% used
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.9),
        fetched_at=now,
    )
    # Candidate 2: gemini is 20% used (0.8 remaining), 3p is 99% used (0.01 remaining)
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.8, p3_remaining=0.01),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.focus == ("gemini",)
    assert decision.target_slot == 2
    assert decision.target_pressure == 0.2


def test_focus_3p() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, focus="3p")
    st = State()
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.9, p3_remaining=0.05),
        fetched_at=now,
    )
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.01, p3_remaining=0.8),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.focus == ("3p",)
    assert decision.target_slot == 2


def test_focus_both() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, focus="both")
    st = State()
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.8),
        fetched_at=now,
    )
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.7, p3_remaining=0.6),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.focus == ("gemini", "3p")
    assert decision.target_slot == 2


def test_focus_auto_no_history_falls_back_to_both() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, focus="auto")
    st = State(prev_active_remaining={})  # no history
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.5),
        fetched_at=now,
    )
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.6, p3_remaining=0.6),
        fetched_at=now,
    )
    decision, next_state = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.focus == ("gemini", "3p")
    assert next_state.prev_active_remaining == {"gemini": 0.05, "3p": 0.5}


def test_focus_auto_picks_3p_when_only_3p_dropped() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, focus="auto")
    # History had gemini at 0.05, 3p at 0.5
    st = State(prev_active_remaining={"gemini": 0.05, "3p": 0.5})
    # Now: gemini unchanged (0.05), 3p dropped by 0.45 to 0.05 (>= 0.005)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    # Candidate 2: gemini 10% used, 3p 20% used
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.9, p3_remaining=0.8),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.focus == ("3p",)
    assert decision.target_slot == 2


def test_focus_auto_none_dropped_falls_back_to_both() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, focus="auto")
    st = State(prev_active_remaining={"gemini": 0.05, "3p": 0.05})
    # Quota did not drop (same remaining)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.8, p3_remaining=0.8),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.focus == ("gemini", "3p")


def test_consume_first_proactive_switch() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, strategy="consume-first")
    st = State()
    # Active is only 50% used (< 90% threshold), reset is in 24h
    act_reset = now + timedelta(hours=24)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(
            now,
            gemini_remaining=0.5,
            gemini_weekly_remaining=0.5,
            gemini_weekly_reset=act_reset,
            p3_remaining=0.5,
            p3_weekly_remaining=0.5,
            p3_weekly_reset=act_reset,
        ),
        fetched_at=now,
    )
    # Candidate 2 is 70% used (<= 90 - 10 = 80%), but resets in 10h (14h earlier > 6h)
    cand2_reset = now + timedelta(hours=10)
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(
            now,
            gemini_remaining=0.3,
            gemini_weekly_remaining=0.3,
            gemini_weekly_reset=cand2_reset,
            p3_remaining=0.3,
            p3_weekly_remaining=0.3,
            p3_weekly_reset=cand2_reset,
        ),
        fetched_at=now,
    )
    decision, next_state = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "switch"
    assert decision.target_slot == 2
    assert decision.reason == "using up quota that resets sooner"
    assert next_state.last_switch_at == now


def test_consume_first_proactive_blocked_by_margin() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, strategy="consume-first")
    st = State()
    act_reset = now + timedelta(hours=24)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(
            now,
            gemini_remaining=0.5,
            gemini_weekly_remaining=0.5,
            gemini_weekly_reset=act_reset,
            p3_remaining=0.5,
            p3_weekly_remaining=0.5,
            p3_weekly_reset=act_reset,
        ),
        fetched_at=now,
    )
    # Candidate 2 resets in 10h, BUT is 85% used (> 80% T - M limit)
    cand2_reset = now + timedelta(hours=10)
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(
            now,
            gemini_remaining=0.15,
            gemini_weekly_remaining=0.15,
            gemini_weekly_reset=cand2_reset,
            p3_remaining=0.15,
            p3_weekly_remaining=0.15,
            p3_weekly_reset=cand2_reset,
        ),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    # Cannot proactively switch because pressure > T - M; falls through to hold
    assert decision.kind == "hold"
    assert decision.reason == "50% used, below the 90% threshold"


def test_consume_first_proactive_within_6h_no_switch() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, strategy="consume-first")
    st = State()
    act_reset = now + timedelta(hours=24)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(
            now,
            gemini_remaining=0.5,
            gemini_weekly_reset=act_reset,
            p3_remaining=0.5,
            p3_weekly_reset=act_reset,
        ),
        fetched_at=now,
    )
    # Candidate 2 resets only 4h earlier (20h from now; needs > 6h earlier)
    cand2_reset = now + timedelta(hours=20)
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(
            now,
            gemini_remaining=0.5,
            gemini_weekly_reset=cand2_reset,
            p3_remaining=0.5,
            p3_weekly_reset=cand2_reset,
        ),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert decision.reason == "50% used, below the 90% threshold"


def test_consume_first_at_threshold_prefers_earlier_reset() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, strategy="consume-first")
    st = State()
    # Active is at 95% used (above threshold)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    # Candidate 2 is 50% used, weekly reset in 5 hours
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(
            now,
            gemini_remaining=0.5,
            gemini_weekly_remaining=0.5,
            gemini_weekly_reset=now + timedelta(hours=5),
            p3_remaining=0.5,
            p3_weekly_remaining=0.5,
            p3_weekly_reset=now + timedelta(hours=5),
        ),
        fetched_at=now,
    )
    # Candidate 3 is 20% used (more quota), but reset is in 48 hours
    c3 = Candidate(
        slot=3,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(
            now,
            gemini_remaining=0.8,
            gemini_weekly_remaining=0.8,
            gemini_weekly_reset=now + timedelta(hours=48),
            p3_remaining=0.8,
            p3_weekly_remaining=0.8,
            p3_weekly_reset=now + timedelta(hours=48),
        ),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2, c3], active_slot=1, s=s, st=st, now=now)
    # Consume-first prioritizes earlier weekly_reset (c2 in 5h) over lower pressure (c3 in 48h)
    assert decision.kind == "switch"
    assert decision.target_slot == 2


def test_state_updated_prev_active_remaining_even_on_hold() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10)
    st = State(prev_active_remaining={"gemini": 0.9})
    # Active is at 0.8 remaining
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.8, p3_remaining=0.85),
        fetched_at=now,
    )
    decision, next_state = decide([c1], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert next_state.prev_active_remaining == {"gemini": 0.8, "3p": 0.85}


def test_consume_first_at_threshold_margin_hold() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, strategy="consume-first")
    st = State()
    # Active is 92% used (0.08 remaining)
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.08, p3_remaining=0.08),
        fetched_at=now,
    )
    # Candidate 2 is 85% used (0.15 remaining) -> delta 7 < 10 margin
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.15, p3_remaining=0.15),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "hold"
    assert decision.reason == "no account is at least 10 points better"


def test_consume_first_at_threshold_blocked() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    s = Settings(threshold=90, margin=10, strategy="consume-first")
    st = State()
    c1 = Candidate(
        slot=1,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    # Candidate 2 is at 95% used (above threshold)
    c2 = Candidate(
        slot=2,
        disabled=False,
        quarantined=False,
        snapshot=_make_snapshot(now, gemini_remaining=0.05, p3_remaining=0.05),
        fetched_at=now,
    )
    decision, _ = decide([c1, c2], active_slot=1, s=s, st=st, now=now)
    assert decision.kind == "blocked"
    assert (
        decision.reason == "every other account is at or above the threshold, or has no fresh data"
    )


def test_helpers_with_timezone_normalization() -> None:
    from mswap.core.policy import _far_future, _normalize_dt, _pressure, _used, _weekly_reset

    aware_dt = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    naive_dt = datetime(2026, 10, 2, 12, 0)

    # _normalize_dt when ref is aware, dt is naive
    norm1 = _normalize_dt(naive_dt, aware_dt)
    assert norm1.tzinfo == UTC

    # _normalize_dt when ref is naive, dt is aware
    norm2 = _normalize_dt(aware_dt, naive_dt)
    assert norm2.tzinfo is None

    # _far_future with naive ref
    ff_naive = _far_future(naive_dt)
    assert ff_naive.tzinfo is None

    # helper calls with Candidate where snapshot is None
    cand_no_snap = Candidate(
        slot=1, disabled=False, quarantined=False, snapshot=None, fetched_at=None
    )
    assert _used(cand_no_snap, "gemini") == 1.0
    assert _pressure(cand_no_snap, ("gemini",)) == 1.0
    assert _weekly_reset(cand_no_snap, ("gemini",), aware_dt) is None

    # _used when snapshot exists but pool does not
    snap_3p_only = _make_snapshot(aware_dt, gemini_remaining=None, p3_remaining=0.5)
    cand_3p = Candidate(
        slot=2, disabled=False, quarantined=False, snapshot=snap_3p_only, fetched_at=aware_dt
    )
    assert _used(cand_3p, "gemini") == 1.0
