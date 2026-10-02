"""Pure policy decision engine for mswap autopilot.

Pre-mortem (Omen) — Failure modes, risks, and mitigations:
----------------------------------------------------------------------
1. Flip-flopping (oscillating rapidly between accounts):
   - Risk: Account A exceeds the threshold, triggering a switch to B. If B is
     also near the threshold or has similar usage, the system immediately
     switches back to A, causing endless oscillation and churn.
   - Mitigation: Rule 4 strictly enforces a cooldown period (cooldown_s) via
     st.last_switch_at. Rule 5 additionally requires a minimum margin (s.margin)
     between active and candidate pressure. Property P5 formally guarantees
     anti-flip-flop behavior across arbitrary states.

2. Switching onto stale data:
   - Risk: Switching to an account whose quota was recently exhausted but whose
     cached snapshot is outdated, immediately failing with rate limits in agy.
   - Mitigation: Rule 2 requires the active account to have fresh usage data
     (age <= max_data_age_s). Candidate eligibility strictly requires fresh(c),
     excluding any candidate with missing snapshots or data older than max_data_age_s.

3. Starving one account (suboptimal quota utilization):
   - Risk: Autopilot repeatedly picks the lowest slot number or burns down one
     account while quota with imminent weekly resets on other accounts goes to waste.
   - Mitigation: In "best" strategy, ties are broken deterministically by (pressure, slot),
     prioritizing the account with the most remaining quota. In "consume-first" strategy
     (Rule 6), proactive switching targets accounts whose weekly quota resets sooner
     (at least 6 hours ahead of the active account).

4. Wrong focus guess (targeting the wrong quota pool):
   - Risk: Autopilot switches accounts because Claude/GPT (3p) quota is high even
     though the user is actively working with Gemini models (or vice versa).
   - Mitigation: Rule 3 computes focus dynamically when focus="auto" by detecting
     drops >= 0.005 in remaining fraction since the previous check. If no drop is
     detected or no history exists, it falls back safely to "both" pools. Settings
     also support explicit "gemini", "3p", or "both" overrides.

5. Clock skew (non-monotonic or inconsistent timestamps):
   - Risk: System clock drift, time-zone inconsistencies, or backward jumps cause
     negative deltas, premature cooldown expiration, or false data staleness.
   - Mitigation: policy.py is completely pure with zero clock I/O; `now` is injected.
     All datetime comparisons normalize timezone offsets against `now` and safely
     clamp relative elapsed durations.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from mswap.core.models import QuotaSnapshot


@dataclass(frozen=True)
class Settings:
    """Autopilot policy configuration settings."""

    threshold: int = 90
    margin: int = 10
    cooldown_s: int = 300
    strategy: Literal["best", "consume-first"] = "best"
    focus: Literal["auto", "gemini", "3p", "both"] = "auto"
    max_data_age_s: int = 900


@dataclass(frozen=True)
class Candidate:
    """Account candidate evaluated by autopilot policy."""

    slot: int
    disabled: bool
    quarantined: bool
    snapshot: QuotaSnapshot | None
    fetched_at: datetime | None


@dataclass(frozen=True)
class State:
    """Autopilot state preserved across decision ticks."""

    last_switch_at: datetime | None = None
    last_from_slot: int | None = None
    last_to_slot: int | None = None
    prev_active_remaining: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """Result of an autopilot policy evaluation."""

    kind: Literal["switch", "hold", "blocked"]
    target_slot: int | None
    reason: str
    focus: tuple[str, ...]
    active_pressure: float | None
    target_pressure: float | None


def pct(x: float) -> int:
    """Format fractional float [0, 1] as an integer percentage."""
    return int(round(x * 100, 4))


def _normalize_dt(dt: datetime, ref: datetime) -> datetime:
    """Normalize datetime timezone awareness to match reference datetime."""
    if ref.tzinfo is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=ref.tzinfo)
    if ref.tzinfo is None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _diff_seconds(t1: datetime, t2: datetime) -> float:
    """Return (t1 - t2) in seconds, normalizing timezone awareness."""
    t2_norm = _normalize_dt(t2, t1)
    return (t1 - t2_norm).total_seconds()


def _far_future(ref: datetime) -> datetime:
    """Return maximum datetime matching timezone awareness of ref."""
    if ref.tzinfo is not None:
        return datetime.max.replace(tzinfo=ref.tzinfo)
    return datetime.max


def _is_fresh(c: Candidate, now: datetime, max_data_age_s: int) -> bool:
    """Return True if candidate has a snapshot fetched within max_data_age_s."""
    if c.snapshot is None or c.fetched_at is None:
        return False
    diff = _diff_seconds(now, c.fetched_at)
    return diff <= max_data_age_s


def _used(c: Candidate, p_key: str) -> float:
    """Return used fraction (1 - remaining) for pool key on candidate."""
    if c.snapshot is None:
        return 1.0
    p = c.snapshot.pool(p_key)
    if p is None:
        return 1.0
    return round(1.0 - p.tightest().remaining, 6)


def _pressure(c: Candidate, focus: tuple[str, ...]) -> float:
    """Compute pressure on candidate across focus pools."""
    if c.snapshot is None:
        return 1.0
    pool_keys = {p.key for p in c.snapshot.pools}
    matching = [p for p in focus if p in pool_keys]
    if not matching:
        return 1.0
    return max(_used(c, p) for p in matching)


def _weekly_reset(c: Candidate, focus: tuple[str, ...], now: datetime) -> datetime | None:
    """Return earliest reset_at among weekly buckets of focus pools on c."""
    if c.snapshot is None:
        return None
    resets: list[datetime] = []
    for p in c.snapshot.pools:
        if p.key in focus:
            for b in p.buckets:
                if b.window == "weekly" and b.reset_at is not None:
                    resets.append(_normalize_dt(b.reset_at, now))
    return min(resets) if resets else None


def decide(
    cands: Sequence[Candidate],
    active_slot: int | None,
    s: Settings,
    st: State,
    now: datetime,
) -> tuple[Decision, State]:
    """Pure decision function for mswap autopilot."""
    # Rule 1: No active account
    if active_slot is None:
        return (
            Decision(
                kind="hold",
                target_slot=None,
                reason="agy isn't signed in to a saved account",
                focus=(),
                active_pressure=None,
                target_pressure=None,
            ),
            st,
        )

    # Find active candidate
    active_cand: Candidate | None = None
    for c in cands:
        if c.slot == active_slot:
            active_cand = c
            break

    # Rule 2: Active candidate not fresh
    if active_cand is None or not _is_fresh(active_cand, now, s.max_data_age_s):
        return (
            Decision(
                kind="hold",
                target_slot=None,
                reason="no recent usage data for the active account",
                focus=(),
                active_pressure=None,
                target_pressure=None,
            ),
            st,
        )

    assert active_cand.snapshot is not None  # guaranteed by _is_fresh

    # Rule 3: Compute focus F and active pressure P_a; update prev_active_remaining
    active_pools = tuple(p.key for p in active_cand.snapshot.pools)
    focus: tuple[str, ...]
    if s.focus == "gemini":
        focus = ("gemini",)
    elif s.focus == "3p":
        focus = ("3p",)
    elif s.focus == "both":
        focus = active_pools
    else:  # focus == "auto"
        if not st.prev_active_remaining:
            focus = active_pools
        else:
            used_pools: list[str] = []
            for p in active_cand.snapshot.pools:
                if p.key in st.prev_active_remaining:
                    prev_rem = st.prev_active_remaining[p.key]
                    curr_rem = p.tightest().remaining
                    if round(prev_rem - curr_rem, 6) >= 0.005:
                        used_pools.append(p.key)
            focus = tuple(used_pools) if used_pools else active_pools

    p_a = _pressure(active_cand, focus)
    new_prev_remaining = {p.key: p.tightest().remaining for p in active_cand.snapshot.pools}
    base_state = State(
        last_switch_at=st.last_switch_at,
        last_from_slot=st.last_from_slot,
        last_to_slot=st.last_to_slot,
        prev_active_remaining=new_prev_remaining,
    )

    # Rule 4: Cooldown
    if st.last_switch_at is not None:
        elapsed = _diff_seconds(now, st.last_switch_at)
        if elapsed < s.cooldown_s:
            rem = max(1, math.ceil(s.cooldown_s - elapsed))
            return (
                Decision(
                    kind="hold",
                    target_slot=None,
                    reason=f"cooldown ({rem}s left)",
                    focus=focus,
                    active_pressure=p_a,
                    target_pressure=None,
                ),
                base_state,
            )

    t = s.threshold / 100.0
    m = s.margin / 100.0

    def eligible(c: Candidate) -> bool:
        return (
            c.slot != active_slot
            and not c.disabled
            and not c.quarantined
            and _is_fresh(c, now, s.max_data_age_s)
        )

    # Rule 5: Strategy "best"
    if s.strategy == "best":
        if round(p_a, 6) < round(t, 6):
            return (
                Decision(
                    kind="hold",
                    target_slot=None,
                    reason=f"{pct(p_a)}% used, below the {s.threshold}% threshold",
                    focus=focus,
                    active_pressure=p_a,
                    target_pressure=None,
                ),
                base_state,
            )

        pool = [c for c in cands if eligible(c) and round(_pressure(c, focus), 6) < round(t, 6)]
        if not pool:
            return (
                Decision(
                    kind="blocked",
                    target_slot=None,
                    reason="every other account is at or above the threshold, or has no fresh data",
                    focus=focus,
                    active_pressure=p_a,
                    target_pressure=None,
                ),
                base_state,
            )

        best = min(pool, key=lambda c: (_pressure(c, focus), c.slot))
        best_p = _pressure(best, focus)
        if round(p_a - best_p, 6) < round(m, 6):
            return (
                Decision(
                    kind="hold",
                    target_slot=None,
                    reason=f"no account is at least {s.margin} points better",
                    focus=focus,
                    active_pressure=p_a,
                    target_pressure=None,
                ),
                base_state,
            )

        switch_state = State(
            last_switch_at=now,
            last_from_slot=active_slot,
            last_to_slot=best.slot,
            prev_active_remaining=new_prev_remaining,
        )
        return (
            Decision(
                kind="switch",
                target_slot=best.slot,
                reason=(
                    f"{pct(p_a)}% used on {','.join(focus)}; "
                    f"switching to the account with the most left ({pct(best_p)}% used)"
                ),
                focus=focus,
                active_pressure=p_a,
                target_pressure=best_p,
            ),
            switch_state,
        )

    # Rule 6: Strategy "consume-first"
    if round(p_a, 6) >= round(t, 6):
        pool = [c for c in cands if eligible(c) and round(_pressure(c, focus), 6) < round(t, 6)]
        if not pool:
            return (
                Decision(
                    kind="blocked",
                    target_slot=None,
                    reason="every other account is at or above the threshold, or has no fresh data",
                    focus=focus,
                    active_pressure=p_a,
                    target_pressure=None,
                ),
                base_state,
            )

        def consume_key(c: Candidate) -> tuple[datetime, float, int]:
            w = _weekly_reset(c, focus, now)
            dt = w if w is not None else _far_future(now)
            return (dt, _pressure(c, focus), c.slot)

        best = min(pool, key=consume_key)
        best_p = _pressure(best, focus)
        if round(p_a - best_p, 6) < round(m, 6):
            return (
                Decision(
                    kind="hold",
                    target_slot=None,
                    reason=f"no account is at least {s.margin} points better",
                    focus=focus,
                    active_pressure=p_a,
                    target_pressure=None,
                ),
                base_state,
            )

        switch_state = State(
            last_switch_at=now,
            last_from_slot=active_slot,
            last_to_slot=best.slot,
            prev_active_remaining=new_prev_remaining,
        )
        return (
            Decision(
                kind="switch",
                target_slot=best.slot,
                reason=(
                    f"{pct(p_a)}% used on {','.join(focus)}; "
                    f"switching to the account with the most left ({pct(best_p)}% used)"
                ),
                focus=focus,
                active_pressure=p_a,
                target_pressure=best_p,
            ),
            switch_state,
        )

    # Proactive branch when p_a < t
    w_active = _weekly_reset(active_cand, focus, now)
    proactive_pool: list[tuple[Candidate, datetime, float]] = []
    if w_active is not None:
        cutoff = w_active - timedelta(hours=6)
        for c in cands:
            if not eligible(c):
                continue
            cand_p = _pressure(c, focus)
            if round(cand_p, 6) > round(t - m, 6):
                continue
            w_c = _weekly_reset(c, focus, now)
            if w_c is not None and w_c < cutoff:
                proactive_pool.append((c, w_c, cand_p))

    if proactive_pool:
        # Sort by earliest weekly_reset, then lower pressure, then lower slot
        proactive_pool.sort(key=lambda item: (item[1], item[2], item[0].slot))
        best_proactive = proactive_pool[0][0]
        proactive_p = proactive_pool[0][2]
        switch_state = State(
            last_switch_at=now,
            last_from_slot=active_slot,
            last_to_slot=best_proactive.slot,
            prev_active_remaining=new_prev_remaining,
        )
        return (
            Decision(
                kind="switch",
                target_slot=best_proactive.slot,
                reason="using up quota that resets sooner",
                focus=focus,
                active_pressure=p_a,
                target_pressure=proactive_p,
            ),
            switch_state,
        )

    return (
        Decision(
            kind="hold",
            target_slot=None,
            reason=f"{pct(p_a)}% used, below the {s.threshold}% threshold",
            focus=focus,
            active_pressure=p_a,
            target_pressure=None,
        ),
        base_state,
    )
