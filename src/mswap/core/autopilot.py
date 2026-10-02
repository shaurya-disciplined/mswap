"""Autopilot execution runner, state persistence, events, and write-back detection.

Pre-mortem & Workflow States (Weave State Machine Model):
--------------------------------------------------------------------------------
State        | Event / Condition                | Next State   | Actions / Output
-------------+----------------------------------+--------------+-----------------------------------
idle         | timer_elapsed / tick_start       | checking     | Read store & refresh usage
checking     | writeback_detected               | cooling-down | Emit 'writeback_suspected', hold
checking     | decision.kind == "hold"          | idle         | Save state, emit hold event
checking     | decision.kind == "blocked"       | idle         | Save state, emit blocked event
checking     | decision.kind == "switch" & dry  | idle         | Save state, emit switch (dry_run)
checking     | decision == "switch" & in_agy    | idle         | Convert to hold (pass --force)
checking     | decision == "switch" & !dry_run  | switching    | Run switcher.switch(ctx, target)
checking     | unhandled_error                  | error        | Emit error event, exit or retry
switching    | switch_succeeded                 | cooling-down | Record switch, emit switch event
switching    | switch_failed                    | idle / error | Rollback, emit error, retain state
cooling-down | elapsed < cooldown_s             | idle         | Policy returns hold ("cooldown")
cooling-down | elapsed >= cooldown_s            | idle         | Ready for next evaluation
cooling-down | writeback_suspected &            | idle         | Hold for 2*cooldown_s
             | elapsed < 2*cooldown_s           |              |
error        | recoverable_error & loop_mode    | idle         | Sleep interval, retry next tick
error        | fatal_error | --once             | terminated   | Exit with code 1

Write-back Detector State Transitions:
--------------------------------------------------------------------------------
Condition                             | Transition / Action
--------------------------------------+-----------------------------------------
active_slot == last_to_slot           | Normal: Active matches target. No-op.
active_slot == last_from_slot AND     | Write-back Detected: agy reverted login.
elapsed_since_switch <= 2 hours       | - Emit 'writeback_suspected' event
                                      | - Set writeback_suspected_at = now
                                      | - Hold for at least 2 * cooldown_s
                                      | - Return Decision(hold, "agy seems...")
manual switch via `mswap switch`      | Manual override:
                                      | - Sets last_from_slot and last_to_slot
                                      | - Resets writeback_suspected_at = None
                                      | - Write-back detector will not trigger
elapsed_since_switch > 2 hours        | Expired: Login changes treated as normal.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mswap.core.errors import CorruptState
from mswap.core.identity import find_by_fp
from mswap.core.models import Account
from mswap.core.policy import (
    Candidate,
    Decision,
    State,
    _diff_seconds,
    _pressure,
    decide,
)
from mswap.core.policy import (
    Settings as PolicySettings,
)
from mswap.core.settings import Settings
from mswap.core.store import data_dir, live_target
from mswap.core.usage import refresh_usage
from mswap.util.fsx import write_private_text


@dataclass(frozen=True)
class AutopilotState(State):
    """Autopilot state preserved across decision ticks, including write-back tracking."""

    writeback_suspected_at: datetime | None = None
    last_decision: dict[str, Any] | None = None


class AutopilotStateStore:
    """Persistent storage for autopilot state in autopilot.json."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else (data_dir() / "autopilot.json")

    def load(self) -> AutopilotState:
        """Load autopilot state from disk or return default initial state."""
        if not self.path.exists():
            return AutopilotState()

        try:
            content = self.path.read_text(encoding="utf-8")
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("autopilot.json must contain a JSON object.")
        except Exception as e:
            raise CorruptState(
                f"Failed to read autopilot state file '{self.path}': {e}",
                hint="Delete autopilot.json or run `mswap doctor`.",
            ) from e

        last_switch_at: datetime | None = None
        raw_switch = data.get("last_switch_at")
        if raw_switch:
            last_switch_at = datetime.fromisoformat(str(raw_switch))
            if last_switch_at.tzinfo is None:
                last_switch_at = last_switch_at.replace(tzinfo=UTC)

        writeback_suspected_at: datetime | None = None
        raw_wb = data.get("writeback_suspected_at")
        if raw_wb:
            writeback_suspected_at = datetime.fromisoformat(str(raw_wb))
            if writeback_suspected_at.tzinfo is None:
                writeback_suspected_at = writeback_suspected_at.replace(tzinfo=UTC)

        last_from_slot = data.get("last_from_slot")
        if last_from_slot is not None:
            last_from_slot = int(last_from_slot)

        last_to_slot = data.get("last_to_slot")
        if last_to_slot is not None:
            last_to_slot = int(last_to_slot)

        raw_prev = data.get("prev_active_remaining")
        prev_active_remaining: dict[str, float] = {}
        if isinstance(raw_prev, dict):
            for k, v in raw_prev.items():
                if isinstance(v, (int, float)):
                    prev_active_remaining[str(k)] = float(v)

        last_decision = data.get("last_decision")
        if not isinstance(last_decision, dict):
            last_decision = None

        return AutopilotState(
            last_switch_at=last_switch_at,
            last_from_slot=last_from_slot,
            last_to_slot=last_to_slot,
            prev_active_remaining=prev_active_remaining,
            writeback_suspected_at=writeback_suspected_at,
            last_decision=last_decision,
        )

    def save(self, state: AutopilotState | State) -> None:
        """Atomically persist autopilot state to autopilot.json."""
        wb_at = getattr(state, "writeback_suspected_at", None)
        last_dec = getattr(state, "last_decision", None)

        data = {
            "last_switch_at": (state.last_switch_at.isoformat() if state.last_switch_at else None),
            "last_from_slot": state.last_from_slot,
            "last_to_slot": state.last_to_slot,
            "prev_active_remaining": dict(state.prev_active_remaining),
            "writeback_suspected_at": wb_at.isoformat() if wb_at else None,
            "last_decision": last_dec,
        }

        write_private_text(self.path, json.dumps(data, indent=2))


def record_switch_state(
    path_or_store: Path | str | AutopilotStateStore,
    from_slot: int | None,
    to_slot: int,
    now: datetime,
) -> None:
    """Record a completed switch (manual or auto) in autopilot state."""
    store = (
        path_or_store
        if isinstance(path_or_store, AutopilotStateStore)
        else AutopilotStateStore(path_or_store)
    )
    current = store.load()
    updated = AutopilotState(
        last_switch_at=now,
        last_from_slot=from_slot,
        last_to_slot=to_slot,
        prev_active_remaining=current.prev_active_remaining,
        writeback_suspected_at=None,
        last_decision=current.last_decision,
    )
    store.save(updated)


def tick(
    ctx: Any,
    settings: Settings | PolicySettings,
    *,
    dry_run: bool = False,
    force: bool = False,
    cache_only: bool = False,
) -> Decision:
    """Execute one evaluation and execution tick of the autopilot loop."""
    now: datetime = ctx.clock.now() if hasattr(ctx, "clock") else datetime.now(UTC)

    if isinstance(settings, Settings):
        policy_settings = settings.autopilot.to_policy_settings()
    else:
        policy_settings = settings

    store_path = ctx.store.root / "autopilot.json" if hasattr(ctx, "store") else None
    state_store = getattr(ctx, "autopilot_store", None) or AutopilotStateStore(store_path)
    state = state_store.load()

    # Step a: Load accounts and refresh usage snapshots
    accounts: list[Account] = ctx.store.load()
    entries = refresh_usage(ctx, accounts, force=False, cache_only=cache_only)

    # Step b: Determine active slot from live credential fingerprint
    live = ctx.vault.read(live_target())
    active_acc = find_by_fp(accounts, live)
    active_slot = active_acc.slot if active_acc else None

    # Construct policy candidates
    candidates: list[Candidate] = []
    for acc in accounts:
        entry = entries.get(acc.slot)
        candidates.append(
            Candidate(
                slot=acc.slot,
                disabled=acc.disabled,
                quarantined=acc.quarantined is not None,
                snapshot=entry.snapshot if entry else None,
                fetched_at=entry.fetched_at if entry else None,
            )
        )

    active_cand = next((c for c in candidates if c.slot == active_slot), None)
    active_pressure: float | None = None
    if active_cand and active_cand.snapshot:
        active_pools = tuple(p.key for p in active_cand.snapshot.pools)
        active_pressure = _pressure(active_cand, active_pools)

    # Step c: WRITE-BACK DETECTOR
    # If the login went back to the old account within 2 hours without mswap doing it:
    is_writeback_detected = False
    if (
        state.last_switch_at is not None
        and state.last_to_slot is not None
        and state.last_from_slot is not None
        and active_slot == state.last_from_slot
        and state.last_from_slot != state.last_to_slot
    ):
        elapsed_switch = _diff_seconds(now, state.last_switch_at)
        if 0 <= elapsed_switch <= 7200:
            is_writeback_detected = True

    is_writeback_hold = False
    if state.writeback_suspected_at is not None:
        elapsed_wb = _diff_seconds(now, state.writeback_suspected_at)
        if 0 <= elapsed_wb < policy_settings.cooldown_s * 2:
            is_writeback_hold = True

    if is_writeback_detected or is_writeback_hold:
        suspected_at = now if is_writeback_detected else state.writeback_suspected_at
        if is_writeback_detected:
            ctx.events.emit(
                "writeback_suspected",
                from_slot=state.last_from_slot,
                to_slot=state.last_to_slot,
            )

        wb_reason = (
            "agy seems to have switched the login back. Restart agy sessions after switching."
        )
        decision = Decision(
            kind="hold",
            target_slot=None,
            reason=wb_reason,
            focus=(),
            active_pressure=active_pressure,
            target_pressure=None,
        )

        dec_dict: dict[str, Any] = {
            "event": decision.kind,
            "reason": decision.reason,
            "from_slot": active_slot,
            "to_slot": None,
            "focus": [],
            "active_pressure": decision.active_pressure,
            "target_pressure": None,
            "dry_run": dry_run,
        }

        new_state = AutopilotState(
            last_switch_at=state.last_switch_at,
            last_from_slot=state.last_from_slot,
            last_to_slot=state.last_to_slot,
            prev_active_remaining=state.prev_active_remaining,
            writeback_suspected_at=suspected_at,
            last_decision=dec_dict,
        )
        state_store.save(new_state)

        ctx.events.emit(
            decision.kind,
            reason=decision.reason,
            from_slot=active_slot,
            to_slot=decision.target_slot,
            focus=list(decision.focus),
            active_pressure=decision.active_pressure,
            target_pressure=decision.target_pressure,
            dry_run=dry_run,
        )
        return decision

    # Step d: Evaluate policy
    decision, new_policy_state = decide(candidates, active_slot, policy_settings, state, now)

    # Step e: Handle switch action
    state_to_save: AutopilotState
    if decision.kind == "switch":
        assert decision.target_slot is not None
        is_inside = (
            ctx.inside_agy() if hasattr(ctx, "inside_agy") and callable(ctx.inside_agy) else False
        )
        if is_inside and not force:
            decision = Decision(
                kind="hold",
                target_slot=None,
                reason="running inside agy; pass --force to allow",
                focus=decision.focus,
                active_pressure=decision.active_pressure,
                target_pressure=decision.target_pressure,
            )
            state_to_save = AutopilotState(
                last_switch_at=state.last_switch_at,
                last_from_slot=state.last_from_slot,
                last_to_slot=state.last_to_slot,
                prev_active_remaining=new_policy_state.prev_active_remaining,
                writeback_suspected_at=state.writeback_suspected_at,
            )
        else:
            if not dry_run:
                from mswap.core import switcher

                switcher.switch(ctx, str(decision.target_slot), force=force, source="autopilot")
                state_to_save = AutopilotState(
                    last_switch_at=now,
                    last_from_slot=active_slot,
                    last_to_slot=decision.target_slot,
                    prev_active_remaining=new_policy_state.prev_active_remaining,
                    writeback_suspected_at=None,
                )
            else:
                state_to_save = AutopilotState(
                    last_switch_at=state.last_switch_at,
                    last_from_slot=state.last_from_slot,
                    last_to_slot=state.last_to_slot,
                    prev_active_remaining=new_policy_state.prev_active_remaining,
                    writeback_suspected_at=state.writeback_suspected_at,
                )
    else:
        state_to_save = AutopilotState(
            last_switch_at=state.last_switch_at,
            last_from_slot=state.last_from_slot,
            last_to_slot=state.last_to_slot,
            prev_active_remaining=new_policy_state.prev_active_remaining,
            writeback_suspected_at=state.writeback_suspected_at,
        )

    # Step f: Save state and emit event
    decision_dict = {
        "event": decision.kind,
        "reason": decision.reason,
        "from_slot": active_slot,
        "to_slot": decision.target_slot,
        "focus": list(decision.focus),
        "active_pressure": decision.active_pressure,
        "target_pressure": decision.target_pressure,
        "dry_run": dry_run,
        "source": "autopilot",
    }
    state_to_save = dataclasses.replace(state_to_save, last_decision=decision_dict)
    state_store.save(state_to_save)

    ctx.events.emit(
        decision.kind,
        reason=decision.reason,
        from_slot=active_slot,
        to_slot=decision.target_slot,
        focus=list(decision.focus),
        active_pressure=decision.active_pressure,
        target_pressure=decision.target_pressure,
        dry_run=dry_run,
        source="autopilot",
    )

    return decision
