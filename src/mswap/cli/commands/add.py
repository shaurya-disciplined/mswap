"""Command to add or update an Antigravity account."""

from __future__ import annotations

import argparse
import dataclasses

from mswap.agy.api import whoami
from mswap.agy.tokens import fingerprint
from mswap.cli.context import AppContext
from mswap.core.errors import NotSignedIn
from mswap.core.models import Account
from mswap.core.store import (
    LIVE_USER,
    backup_last,
    backup_original,
    live_target,
    slot_target,
)
from mswap.core.switcher import recover


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the add command."""
    recovery_msg = recover(ctx)
    if recovery_msg:
        print(ctx.theme.dim(recovery_msg), file=ctx.err)

    new = getattr(args, "new", False) if isinstance(args, argparse.Namespace) else ("--new" in args)
    blob = ctx.vault.read(live_target())
    if not blob:
        raise NotSignedIn("agy isn't signed in.", hint="Run `agy`, sign in, then try again.")

    from mswap.agy.tokens import TokenService

    token_service = TokenService(ctx)
    token = token_service.fresh_for_live(blob)
    email = whoami(token, ctx.http)
    accounts = ctx.store.load()
    existing = next((a for a in accounts if a.email.lower() == email.lower()), None)

    # Backup original once
    if ctx.vault.read(backup_original()) is None:
        ctx.vault.write(backup_original(), blob, LIVE_USER)

    now = ctx.clock.now()
    if existing:
        slot = existing.slot
        idx = accounts.index(existing)
        accounts[idx] = dataclasses.replace(
            existing,
            fp=fingerprint(blob),
            updated_at=now,
        )
        verb = "Updated"
    else:
        slot = max((a.slot for a in accounts), default=0) + 1
        accounts.append(
            Account(
                slot=slot,
                email=email,
                fp=fingerprint(blob),
                added_at=now,
                updated_at=now,
            )
        )
        verb = "Added"

    ctx.vault.write(slot_target(slot), blob, email)
    ctx.store.save(accounts)
    ok_mark = ctx.theme.ok("✓")
    slot_str = ctx.theme.bold(str(slot))
    print(f"{ok_mark} {verb} account {slot_str}: {email}", file=ctx.out)

    if new:
        ctx.vault.write(backup_last(), blob, LIVE_USER)
        ctx.vault.delete(live_target())
        signed_out_msg = f"{ok_mark} Signed agy out on this PC only (the saved copy stays valid)."
        print(signed_out_msg, file=ctx.out)
        sign_in_hint = (
            f"  Now run {ctx.theme.accent('agy')}, sign in with the next Google account, "
            f"then run {ctx.theme.accent('mswap add')}."
        )
        print(sign_in_hint, file=ctx.out)
    else:
        warn_hint = (
            "  To add another: `mswap add --new`. "
            "Don't use agy's /logout, it can revoke saved logins."
        )
        print(ctx.theme.dim(warn_hint), file=ctx.out)

    return 0
