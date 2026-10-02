"""Switch transaction manager, target resolution, and crash recovery.

Owns the §A7 switch transaction algorithm, target rotation, and automatic journal recovery.
Must never leave unsaved live credentials overwritten without explicit force.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from mswap.agy.process import AgyProcess
from mswap.agy.tokens import validate_blob
from mswap.core.errors import (
    CorruptState,
    MswapError,
    NothingToDo,
    UnsafeOperation,
    UsageError,
    VaultError,
)
from mswap.core.events import Events
from mswap.core.identity import find_by_fp, fp_or_none
from mswap.core.journal import Journal
from mswap.core.locking import FileLock
from mswap.core.models import Account
from mswap.core.store import (
    LIVE_USER,
    AccountStore,
    backup_last,
    backup_original,
    live_target,
    slot_target,
)
from mswap.vault.base import Vault


class SwitchContext(Protocol):
    """Protocol defining dependencies needed for switch and recovery operations."""

    @property
    def vault(self) -> Vault: ...

    @property
    def store(self) -> AccountStore: ...

    @property
    def lock(self) -> Callable[..., FileLock]: ...

    @property
    def journal(self) -> Journal: ...

    @property
    def events(self) -> Events: ...

    @property
    def procs(self) -> Callable[[], list[AgyProcess]]: ...


@dataclass(frozen=True)
class SwitchResult:
    """Outcome of a switch operation."""

    status: Literal["switched", "already_active"]
    from_account: Account | None
    to_account: Account
    agy_running: bool


def resolve_target(
    accounts: Sequence[Account],
    selector: str | None,
    active: Account | None,
    *,
    include_disabled: bool = False,
) -> Account:
    """Resolve target account from selector or rotate to next eligible account."""
    if selector is None:
        if len(accounts) < 2:
            raise NothingToDo(
                "Only one account saved.",
                hint="Add another with `mswap add --new`.",
            )
        if include_disabled:
            eligible = [a for a in accounts if a.quarantined is None]
        else:
            eligible = [a for a in accounts if not a.disabled and a.quarantined is None]

        if len(eligible) < 2:
            raise NothingToDo(
                "Only one account is available to switch to.",
                hint="Add another with `mswap add --new`.",
            )

        sorted_eligible = sorted(eligible, key=lambda a: a.slot)
        if active is None:
            return sorted_eligible[0]

        higher = [a for a in sorted_eligible if a.slot > active.slot]
        if higher:
            return higher[0]
        return sorted_eligible[0]

    match: Account | None = None
    if selector.isdigit():
        slot_num = int(selector)
        match = next((a for a in accounts if a.slot == slot_num), None)
    elif "@" in selector:
        match = next((a for a in accounts if a.email.lower() == selector.lower()), None)
    else:
        match = next(
            (a for a in accounts if a.alias is not None and a.alias.lower() == selector.lower()),
            None,
        )

    if match is None:
        raise UsageError(f"No account matching '{selector}'.", hint="See `mswap list`.")

    return match


def switch(
    ctx: SwitchContext,
    selector: str | None = None,
    *,
    force: bool = False,
    include_disabled_in_rotation: bool = False,
) -> SwitchResult:
    """Switch active Antigravity account per §A7 transaction protocol."""
    with ctx.lock(timeout=10.0):
        accounts = ctx.store.load()
        if not accounts:
            raise MswapError("No accounts saved yet.", hint="Run `mswap add` first.")
        live = ctx.vault.read(live_target())
        active = find_by_fp(accounts, live)
        target = resolve_target(
            accounts,
            selector,
            active,
            include_disabled=include_disabled_in_rotation,
        )
        procs_fn = getattr(ctx, "procs", None)
        is_agy_running = bool(procs_fn()) if callable(procs_fn) else False
        if active and target.slot == active.slot:
            return SwitchResult("already_active", active, target, agy_running=is_agy_running)
        if live is not None and active is None and not force:
            raise UnsafeOperation(
                "agy is signed in to an account mswap hasn't saved.",
                hint=(
                    "Run `mswap add` to save it first, or `mswap switch --force` "
                    "(the current login is kept in backup-last)."
                ),
            )
        target_blob = ctx.vault.read(slot_target(target.slot))
        if target_blob is None:
            raise CorruptState(
                f"Saved login for account {target.slot} is missing.",
                hint="Sign in as it in agy and run `mswap add`.",
            )
        validate_blob(target_blob)
        if target.quarantined and not force:
            raise UnsafeOperation(
                f"Account {target.slot} is quarantined ({target.quarantined.reason}).",
                hint="Sign in as it in agy and run `mswap add`, or use `mswap switch --force`.",
            )
        ctx.journal.begin(
            op="switch",
            from_fp=active.fp if active else None,
            to_fp=target.fp,
            live_fp=fp_or_none(live),
        )
        try:
            if live is not None:
                ctx.vault.write(backup_last(), live, LIVE_USER)
                if ctx.vault.read(backup_original()) is None:
                    ctx.vault.write(backup_original(), live, LIVE_USER)
                if active is not None:
                    ctx.vault.write(slot_target(active.slot), live, active.email)
            ctx.vault.write(live_target(), target_blob, LIVE_USER)
            if ctx.vault.read(live_target()) != target_blob:
                raise VaultError("Read-back after write didn't match.")
        except Exception:
            if live is not None:
                ctx.vault.write(live_target(), live, LIVE_USER)
            ctx.journal.fail()
            raise
        ctx.journal.commit()
        ctx.events.emit(
            "switch",
            from_slot=active.slot if active else None,
            to_slot=target.slot,
            forced=force,
        )
    return SwitchResult("switched", active, target, agy_running=is_agy_running)


def sign_out_live(ctx: SwitchContext) -> None:
    """Sign agy out on this machine by deleting live credential and journaling."""
    with ctx.lock(timeout=10.0):
        live = ctx.vault.read(live_target())
        if live is None:
            return
        live_fprint = fp_or_none(live)
        ctx.journal.begin(
            op="sign_out",
            from_fp=live_fprint,
            to_fp=None,
            live_fp=live_fprint,
        )
        try:
            ctx.vault.write(backup_last(), live, LIVE_USER)
            ctx.vault.delete(live_target())
        except Exception:
            ctx.vault.write(live_target(), live, LIVE_USER)
            ctx.journal.fail()
            raise
        ctx.journal.commit()
        ctx.events.emit("sign_out", from_fp=live_fprint)


def recover(ctx: SwitchContext) -> str | None:
    """Recover an interrupted switch or sign-out transaction per §A7 rules.

    Returns a human sentence describing what action was taken, or None if nothing to do.
    """
    with ctx.lock(timeout=10.0):
        st = ctx.journal.state()
        if not st or st.get("state") != "begun":
            return None

        live = ctx.vault.read(live_target())
        live_fp = fp_or_none(live)
        to_fp = st.get("to_fp")
        saved_live_fp = st.get("live_fp")
        op = st.get("op", "switch")

        if op == "sign_out":
            if live_fp is None:
                ctx.journal.commit()
                return "Recovered from interrupted sign-out: sign-out had completed."

            if live_fp == saved_live_fp:
                ctx.journal.fail()
                return (
                    "Recovered from interrupted sign-out: "
                    "sign-out never happened and was marked failed."
                )

            backup = ctx.vault.read(backup_last())
            backup_fp = fp_or_none(backup)
            if backup is not None and backup_fp == saved_live_fp:
                ctx.vault.write(live_target(), backup, LIVE_USER)
                ctx.journal.fail()
                return "Recovered from interrupted sign-out: restored previous login from backup."

            raise CorruptState(
                "An interrupted sign-out transaction could not be safely recovered.",
                hint="Check your agy login and run `mswap add`.",
            )

        if live_fp is not None and live_fp == to_fp:
            ctx.journal.commit()
            return "Recovered from interrupted switch: switch had completed."

        if live_fp == saved_live_fp:
            ctx.journal.fail()
            return "Recovered from interrupted switch: switch never happened and was marked failed."

        backup = ctx.vault.read(backup_last())
        backup_fp = fp_or_none(backup)
        if backup is not None and backup_fp == saved_live_fp:
            ctx.vault.write(live_target(), backup, LIVE_USER)
            ctx.journal.fail()
            return "Recovered from interrupted switch: restored previous login from backup."

        raise CorruptState(
            "An interrupted switch transaction could not be safely recovered.",
            hint="Check your agy login and run `mswap add`.",
        )
