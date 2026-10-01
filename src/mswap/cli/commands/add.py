"""Command to add or update an Antigravity account."""

from __future__ import annotations

from mswap.agy.api import whoami
from mswap.agy.tokens import ensure_fresh, fingerprint
from mswap.cli.context import AppContext
from mswap.core.errors import MswapError
from mswap.core.store import (
    LIVE_USER,
    backup_last,
    backup_original,
    live_target,
    load_accounts,
    save_accounts,
    slot_target,
)
from mswap.ui.theme import bold, cyan, dim, green


def run(ctx: AppContext, args: list[str]) -> int:
    """Execute the add command."""
    new = "--new" in args
    blob = ctx.vault.read(live_target())
    if not blob:
        raise MswapError("agy isn't signed in. Run `agy`, sign in, then try again.")

    token, _ = ensure_fresh(blob, ctx.http, ctx.clock.now())
    email = whoami(token, ctx.http)
    accounts = load_accounts()
    existing = next((a for a in accounts if a["email"].lower() == email.lower()), None)

    # Backup original once
    if ctx.vault.read(backup_original()) is None:
        ctx.vault.write(backup_original(), blob, LIVE_USER)

    if existing:
        slot = int(existing["slot"])
        existing["fp"] = fingerprint(blob)
        verb = "Updated"
    else:
        slot = max((int(a["slot"]) for a in accounts), default=0) + 1
        accounts.append(
            {
                "slot": slot,
                "email": email,
                "fp": fingerprint(blob),
                "added_at": ctx.clock.now().isoformat(timespec="seconds"),
            }
        )
        verb = "Added"

    ctx.vault.write(slot_target(slot), blob, email)
    save_accounts(accounts)
    print(f"{green('✓')} {verb} account {bold(str(slot))}: {email}")

    if new:
        ctx.vault.write(backup_last(), blob, LIVE_USER)
        ctx.vault.delete(live_target())
        print(f"{green('✓')} Signed agy out on this PC only (the saved copy stays valid).")
        sign_in_hint = (
            f"  Now run {cyan('agy')}, sign in with the next Google account, "
            f"then run {cyan('mswap add')}."
        )
        print(sign_in_hint)
    else:
        warn_hint = (
            "  To add another: `mswap add --new`. "
            "Don't use agy's /logout, it can revoke saved logins."
        )
        print(dim(warn_hint))

    return 0
