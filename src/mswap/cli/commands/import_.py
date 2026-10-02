"""Command to import accounts from a passphrase-encrypted bundle.

Owns decryption, account reconciliation, slot allocation, and vault population.
Must never touch agy's live login target or write unencrypted tokens to accounts.json.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import json
from pathlib import Path

from mswap.agy.tokens import fingerprint, validate_blob
from mswap.cli.context import AppContext
from mswap.core.bundle import (
    check_crypto_available,
    decrypt_bundle,
    get_export_passphrase,
)
from mswap.core.errors import UsageError
from mswap.core.models import Account, account_from_json
from mswap.core.store import slot_target
from mswap.ui import jsonout


def _alias_taken(accounts: list[Account], alias: str, slot: int) -> bool:
    """Report whether another account (not `slot`) already uses this alias."""
    return any(a.alias and a.alias.lower() == alias.lower() and a.slot != slot for a in accounts)


def _parse_entry(item: object) -> tuple[Account, bytes]:
    """Validate one decrypted account entry up front so a bad entry writes nothing."""
    damaged = UsageError("Wrong passphrase or damaged file.")
    if not isinstance(item, dict) or "email" not in item or "blob" not in item:
        raise damaged
    try:
        blob = base64.b64decode(str(item["blob"]), validate=True)
        validate_blob(blob)
        # Strip the secret blob so it can never land in accounts.json
        account = account_from_json({k: v for k, v in item.items() if k != "blob"})
    except Exception as err:
        raise damaged from err
    return account, blob


@dataclasses.dataclass
class _Plan:
    """What an import will do, decided entirely before anything is written."""

    original: list[Account]
    accounts: list[Account]
    writes: list[tuple[int, str, bytes]]  # (slot, email, blob) in write order
    imported: int = 0
    updated: int = 0
    skipped: int = 0


def _plan_import(
    ctx: AppContext,
    accounts: list[Account],
    entries: list[tuple[Account, bytes]],
    *,
    force: bool,
) -> _Plan:
    """Decide slots and replacements for every entry without touching the vault or the store.

    Running out of slots therefore fails before any login has been written (no orphan vault
    entries).
    """
    plan = _Plan(original=accounts, accounts=list(accounts), writes=[])
    for incoming, blob in entries:
        existing = next(
            (a for a in plan.accounts if a.email.lower() == incoming.email.lower()), None
        )
        if existing is not None:
            # Replace only on --force, or when our copy is quarantined and the bundle's is not.
            replace = force or (existing.quarantined is not None and incoming.quarantined is None)
            if not replace:
                plan.skipped += 1
                continue
            slot = existing.slot
            alias = incoming.alias
            if alias and _alias_taken(plan.accounts, alias, slot):
                alias = existing.alias
            plan.accounts[plan.accounts.index(existing)] = dataclasses.replace(
                incoming,
                slot=slot,
                alias=alias,
                fp=fingerprint(blob),
                updated_at=ctx.clock.now(),
            )
            plan.updated += 1
        else:
            used_slots = {a.slot for a in plan.accounts}
            slot_or_none = next((s for s in range(1, 100) if s not in used_slots), None)
            if slot_or_none is None:
                raise UsageError("Account limit reached (maximum 99 accounts).")
            slot = slot_or_none
            alias = incoming.alias
            if alias and _alias_taken(plan.accounts, alias, slot):
                alias = None
            plan.accounts.append(
                dataclasses.replace(
                    incoming,
                    slot=slot,
                    alias=alias,
                    fp=fingerprint(blob),
                    updated_at=ctx.clock.now(),
                )
            )
            plan.imported += 1
        plan.writes.append((slot, incoming.email, blob))
    return plan


def _owner(plan: _Plan, slot: int) -> str:
    """Return the email that owned `slot` before the import (the vault's user label)."""
    return next((a.email for a in plan.original if a.slot == slot), "antigravity")


def _apply_import(ctx: AppContext, plan: _Plan) -> None:
    """Write the planned logins, then the metadata; undo the vault writes if anything fails."""
    done: list[tuple[int, bytes | None]] = []
    try:
        for slot, email, blob in plan.writes:
            previous = ctx.vault.read(slot_target(slot))
            ctx.vault.write(slot_target(slot), blob, email)
            done.append((slot, previous))
        ctx.store.save(plan.accounts)
    except Exception:
        for slot, previous in reversed(done):
            with contextlib.suppress(Exception):
                if previous is None:
                    ctx.vault.delete(slot_target(slot))
                else:
                    ctx.vault.write(slot_target(slot), previous, _owner(plan, slot))
        raise


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the import command."""
    check_crypto_available()

    target_file = Path(args.file)
    if not target_file.is_file():
        raise UsageError(f"Export file not found: {target_file}")

    try:
        content = target_file.read_text(encoding="utf-8")
        bundle_dict = json.loads(content)
    except Exception as err:
        raise UsageError("Wrong passphrase or damaged file.") from err

    passphrase = get_export_passphrase(ctx.env, confirm=False)
    plaintext = decrypt_bundle(bundle_dict, passphrase)

    accounts_data = plaintext.get("accounts", [])
    if not isinstance(accounts_data, list):
        raise UsageError("Wrong passphrase or damaged file.")

    force = bool(getattr(args, "force", False))
    entries = [_parse_entry(item) for item in accounts_data]

    with ctx.lock(timeout=10.0):
        plan = _plan_import(ctx, list(ctx.store.load()), entries, force=force)
        _apply_import(ctx, plan)

    imported, updated, skipped = plan.imported, plan.updated, plan.skipped

    if ctx.json:
        data = {
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
        }
        print(jsonout.ok("import", data), file=ctx.out)
        return 0

    ok_mark = ctx.theme.ok(ctx.theme.glyph_ok)
    print(f"{ok_mark} Imported {imported}, updated {updated}, skipped {skipped}.", file=ctx.out)
    return 0
