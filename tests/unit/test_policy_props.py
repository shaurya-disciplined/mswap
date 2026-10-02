"""Hypothesis property-based tests for mswap autopilot policy engine."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from mswap.core.models import Bucket, Pool, QuotaSnapshot
from mswap.core.policy import Candidate, Settings, State, _pressure, decide

settings.register_profile("ci", max_examples=500, deadline=None)
settings.register_profile("dev", max_examples=50, deadline=None)

if os.getenv("CI"):
    settings.load_profile("ci")
else:
    settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))

BASE_TIME = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@st.composite
def bucket_strategy(draw: st.DrawFn, window: str = "5h") -> Bucket:
    remaining = draw(st.floats(min_value=0.0, max_value=1.0, width=32))
    # Bucket.__post_init__ erases reset_at when remaining >= 1.0
    if remaining < 1.0:
        offset_hours = draw(st.integers(min_value=1, max_value=168))
        reset_at = BASE_TIME + timedelta(hours=offset_hours)
    else:
        reset_at = None
    return Bucket(window=window, remaining=remaining, reset_at=reset_at)


@st.composite
def quota_snapshot_strategy(draw: st.DrawFn, fetched_at: datetime) -> QuotaSnapshot:
    has_gemini = draw(st.booleans())
    has_3p = draw(st.booleans())
    if not has_gemini and not has_3p:
        has_gemini = True

    pools: list[Pool] = []
    if has_gemini:
        b_5h = draw(bucket_strategy("5h"))
        b_weekly = draw(bucket_strategy("weekly"))
        pools.append(Pool(key="gemini", name="Gemini", buckets=(b_5h, b_weekly)))
    if has_3p:
        b_5h = draw(bucket_strategy("5h"))
        b_weekly = draw(bucket_strategy("weekly"))
        pools.append(Pool(key="3p", name="Claude & GPT", buckets=(b_5h, b_weekly)))

    return QuotaSnapshot(fetched_at=fetched_at, pools=tuple(pools))


@st.composite
def candidate_strategy(draw: st.DrawFn, slot: int, now: datetime) -> Candidate:
    disabled = draw(st.booleans())
    quarantined = draw(st.booleans())
    has_snapshot = draw(st.booleans())
    if has_snapshot:
        age_seconds = draw(st.integers(min_value=-60, max_value=1800))
        fetched_at = now - timedelta(seconds=age_seconds)
        snapshot = draw(quota_snapshot_strategy(fetched_at))
    else:
        fetched_at = None
        snapshot = None

    return Candidate(
        slot=slot,
        disabled=disabled,
        quarantined=quarantined,
        snapshot=snapshot,
        fetched_at=fetched_at,
    )


@st.composite
def policy_inputs_strategy(
    draw: st.DrawFn,
) -> tuple[Sequence[Candidate], int | None, Settings, State, datetime]:
    now = BASE_TIME
    threshold = draw(st.integers(min_value=50, max_value=99))
    margin = draw(st.integers(min_value=0, max_value=50))
    cooldown_s = draw(st.integers(min_value=60, max_value=3600))
    strategy: Literal["best", "consume-first"] = draw(st.sampled_from(["best", "consume-first"]))
    focus: Literal["auto", "gemini", "3p", "both"] = draw(
        st.sampled_from(["auto", "gemini", "3p", "both"])
    )
    max_data_age_s = draw(st.integers(min_value=60, max_value=1800))

    settings_obj = Settings(
        threshold=threshold,
        margin=margin,
        cooldown_s=cooldown_s,
        strategy=strategy,
        focus=focus,
        max_data_age_s=max_data_age_s,
    )

    num_candidates = draw(st.integers(min_value=1, max_value=6))
    candidates = [draw(candidate_strategy(slot=i, now=now)) for i in range(1, num_candidates + 1)]

    active_slot_choice = draw(st.sampled_from([None, *[c.slot for c in candidates], 99]))

    has_last_switch = draw(st.booleans())
    if has_last_switch:
        elapsed = draw(st.integers(min_value=-60, max_value=7200))
        last_switch_at = now - timedelta(seconds=elapsed)
    else:
        last_switch_at = None

    has_prev_rem = draw(st.booleans())
    prev_rem: dict[str, float] = {}
    if has_prev_rem:
        prev_rem["gemini"] = draw(st.floats(min_value=0.0, max_value=1.0))
        prev_rem["3p"] = draw(st.floats(min_value=0.0, max_value=1.0))

    state = State(
        last_switch_at=last_switch_at,
        last_from_slot=draw(st.sampled_from([None, 1, 2])),
        last_to_slot=draw(st.sampled_from([None, 1, 2])),
        prev_active_remaining=prev_rem,
    )

    return candidates, active_slot_choice, settings_obj, state, now


@given(inputs=policy_inputs_strategy())
def test_p1_target_validity(
    inputs: tuple[Sequence[Candidate], int | None, Settings, State, datetime],
) -> None:
    """P1: The target is never active, disabled, quarantined or stale."""
    cands, active_slot, s, st_obj, now = inputs
    decision, _ = decide(cands, active_slot, s, st_obj, now)

    if decision.kind == "switch":
        assert decision.target_slot is not None
        assert decision.target_slot != active_slot
        cand_map = {c.slot: c for c in cands}
        assert decision.target_slot in cand_map
        target_c = cand_map[decision.target_slot]
        assert not target_c.disabled
        assert not target_c.quarantined
        assert target_c.snapshot is not None
        assert target_c.fetched_at is not None
        diff = (now - target_c.fetched_at).total_seconds()
        assert diff <= s.max_data_age_s


@given(inputs=policy_inputs_strategy())
def test_p2_no_switch_in_cooldown(
    inputs: tuple[Sequence[Candidate], int | None, Settings, State, datetime],
) -> None:
    """P2: No switch while in cooldown."""
    cands, active_slot, s, st_obj, now = inputs
    decision, _ = decide(cands, active_slot, s, st_obj, now)

    if st_obj.last_switch_at is not None:
        elapsed = (now - st_obj.last_switch_at).total_seconds()
        if elapsed < s.cooldown_s:
            assert decision.kind != "switch"
            if decision.kind == "hold" and active_slot is not None:
                # If active is fresh, cooldown reason must be returned
                cand_map = {c.slot: c for c in cands}
                active_c = cand_map.get(active_slot)
                if (
                    active_c is not None
                    and active_c.snapshot is not None
                    and active_c.fetched_at is not None
                    and (now - active_c.fetched_at).total_seconds() <= s.max_data_age_s
                ):
                    assert "cooldown (" in decision.reason


@given(inputs=policy_inputs_strategy())
def test_p3_switch_target_pressure_under_threshold(
    inputs: tuple[Sequence[Candidate], int | None, Settings, State, datetime],
) -> None:
    """P3: On a switch, pressure(target) < T."""
    cands, active_slot, s, st_obj, now = inputs
    decision, _ = decide(cands, active_slot, s, st_obj, now)

    if decision.kind == "switch":
        assert decision.target_pressure is not None
        t = s.threshold / 100.0
        assert round(decision.target_pressure, 6) < round(t, 6)


@given(inputs=policy_inputs_strategy())
def test_p4_best_strategy_optimality(
    inputs: tuple[Sequence[Candidate], int | None, Settings, State, datetime],
) -> None:
    """P4: (best) pressure(target) <= pressure of every other eligible candidate under T."""
    cands, active_slot, s, st_obj, now = inputs
    if s.strategy != "best":
        return
    decision, _ = decide(cands, active_slot, s, st_obj, now)

    if decision.kind == "switch":
        t = s.threshold / 100.0
        focus = decision.focus
        target_slot = decision.target_slot
        assert decision.target_pressure is not None
        target_p = decision.target_pressure

        for c in cands:
            if c.slot == active_slot or c.disabled or c.quarantined:
                continue
            if c.snapshot is None or c.fetched_at is None:
                continue
            if (now - c.fetched_at).total_seconds() > s.max_data_age_s:
                continue
            c_p = _pressure(c, focus)
            if round(c_p, 6) < round(t, 6):
                # (target_p, target_slot) <= (c_p, c.slot)
                assert (round(target_p, 6), target_slot) <= (round(c_p, 6), c.slot)


@given(inputs=policy_inputs_strategy())
def test_p5_anti_flip_flop(
    inputs: tuple[Sequence[Candidate], int | None, Settings, State, datetime],
) -> None:
    """P5: Anti-flip-flop: applying decision and re-evaluating never switches back to prev slot."""
    cands, active_slot, s, st_obj, now = inputs
    decision, next_state = decide(cands, active_slot, s, st_obj, now)

    if decision.kind == "switch":
        new_active = decision.target_slot
        assert new_active is not None
        # Call decide immediately with the new active slot and next_state
        dec2, _ = decide(cands, active_slot=new_active, s=s, st=next_state, now=now)
        # It must never switch back to active_slot
        if dec2.kind == "switch":
            assert dec2.target_slot != active_slot
        else:
            assert dec2.target_slot is None


@given(inputs=policy_inputs_strategy())
def test_p6_determinism(
    inputs: tuple[Sequence[Candidate], int | None, Settings, State, datetime],
) -> None:
    """P6: Determinism: the same inputs give the same output."""
    cands, active_slot, s, st_obj, now = inputs
    dec1, st1 = decide(cands, active_slot, s, st_obj, now)
    dec2, st2 = decide(cands, active_slot, s, st_obj, now)

    assert dec1 == dec2
    assert st1 == st2
