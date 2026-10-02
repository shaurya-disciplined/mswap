"""Command to add or update an Antigravity account."""

from __future__ import annotations

import argparse
import dataclasses

from mswap.agy.api import whoami
from mswap.agy.tokens import TokenService, fingerprint
from mswap.cli.context import AppContext
from mswap.core.errors import NotSignedIn, UsageError
from mswap.core.models import ALIAS_REGEX, Account, validate_alias
from mswap.core.store import (
    LIVE_USER,
    backup_original,
    live_target,
    slot_target,
)
from mswap.core.switcher import recover, sign_out_live
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the add command."""
    recovery_msg = recover(ctx)
    if recovery_msg:
        print(ctx.theme.dim(recovery_msg), file=ctx.err)

    new = getattr(args, "new", False) if isinstance(args, argparse.Namespace) else ("--new" in args)
    alias = getattr(args, "alias", None) if isinstance(args, argparse.Namespace) else None
    if alias is not None and not ALIAS_REGEX.match(alias):
        raise UsageError(
            "Invalid alias name.",
            hint=(
                "Aliases must start with a lowercase letter, contain only a-z, 0-9, and '-', "
                "and be at most 20 characters."
            ),
        )

    blob = ctx.vault.read(live_target())
    if not blob:
        raise NotSignedIn("agy isn't signed in.", hint="Run `agy`, sign in, then try again.")

    token_service = TokenService(ctx)
    token = token_service.fresh_for_live(blob)
    email = whoami(token, ctx.http)
    accounts = ctx.store.load()
    existing = next((a for a in accounts if a.email.lower() == email.lower()), None)

    if alias is not None:
        validate_alias(alias, accounts, exclude_slot=existing.slot if existing else None)

    # Backup original once
    if ctx.vault.read(backup_original()) is None:
        ctx.vault.write(backup_original(), blob, LIVE_USER)

    now = ctx.clock.now()
    if existing:
        slot = existing.slot
        idx = accounts.index(existing)
        target_alias = alias if alias is not None else existing.alias
        accounts[idx] = dataclasses.replace(
            existing,
            fp=fingerprint(blob),
            updated_at=now,
            alias=target_alias,
            quarantined=None,
        )
        verb = "Updated"
        status = "updated"
        final_alias = target_alias
    else:
        slot = max((a.slot for a in accounts), default=0) + 1
        accounts.append(
            Account(
                slot=slot,
                email=email,
                fp=fingerprint(blob),
                added_at=now,
                updated_at=now,
                alias=alias,
            )
        )
        verb = "Added"
        status = "added"
        final_alias = alias

    ctx.vault.write(slot_target(slot), blob, email)
    ctx.store.save(accounts)

    if new:
        sign_out_live(ctx)

    if ctx.json:
        data = {
            "status": status,
            "slot": slot,
            "email": email,
            "alias": final_alias,
            "signed_out": bool(new),
        }
        print(jsonout.ok("add", data), file=ctx.out)
        return 0

    ok_mark = ctx.theme.ok(ctx.theme.glyph_ok)
    slot_str = ctx.theme.bold(str(slot))
    print(f"{ok_mark} {verb} account {slot_str}: {email}", file=ctx.out)

    if new:
        print(
            f"{ok_mark} Signed agy out on this PC only. The saved copy stays valid.",
            file=ctx.out,
        )
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
