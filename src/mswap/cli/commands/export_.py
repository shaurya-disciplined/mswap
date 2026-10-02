"""Command to export accounts to a passphrase-encrypted bundle.

Owns selector resolution, bundle creation, and secure file permission application.
Must never touch agy's live login target or print plain credentials.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from mswap import __version__
from mswap.cli.context import AppContext
from mswap.core.bundle import (
    check_crypto_available,
    encrypt_bundle,
    get_export_passphrase,
    refuse_existing,
    write_secure_file,
)
from mswap.core.errors import CorruptState, UsageError
from mswap.core.models import Account, account_to_json
from mswap.core.store import slot_target
from mswap.core.switcher import resolve_target
from mswap.ui import jsonout


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the export command."""
    check_crypto_available()

    accounts = ctx.store.load()
    if not accounts:
        raise UsageError("No accounts saved yet.", hint="Run `mswap add` first.")

    target_file = Path(args.file)
    if target_file.exists() or target_file.is_symlink():
        raise refuse_existing(target_file)

    selectors_raw = getattr(args, "accounts", None)
    if selectors_raw:
        sel_list = [s.strip() for s in selectors_raw.split(",") if s.strip()]
        if not sel_list:
            raise UsageError("No valid account selectors provided in --accounts.")
        selected_slots: set[int] = set()
        to_export: list[Account] = []
        for sel in sel_list:
            target = resolve_target(accounts, sel, active=None)
            if target.slot not in selected_slots:
                selected_slots.add(target.slot)
                to_export.append(target)
    else:
        to_export = list(accounts)

    if not to_export:
        raise UsageError("No accounts selected to export.")

    account_entries = []
    for acc in to_export:
        blob = ctx.vault.read(slot_target(acc.slot))
        if blob is None:
            raise CorruptState(f"Saved login for account {acc.slot} is missing.")
        acc_dict = account_to_json(acc)
        acc_dict["blob"] = base64.b64encode(blob).decode("ascii")
        account_entries.append(acc_dict)

    passphrase = get_export_passphrase(ctx.env, confirm=True)

    now = ctx.clock.now()
    plaintext = {
        "exported_at": now.isoformat(),
        "mswap_version": __version__,
        "accounts": account_entries,
    }

    bundle = encrypt_bundle(plaintext, passphrase)
    bundle_json = json.dumps(bundle, indent=2)
    write_secure_file(target_file, bundle_json)

    if ctx.json:
        data = {
            "file": str(target_file),
            "count": len(to_export),
        }
        print(jsonout.ok("export", data), file=ctx.out)
        return 0

    ok_mark = ctx.theme.ok(ctx.theme.glyph_ok)
    print(f"{ok_mark} Exported {len(to_export)} account(s) to {target_file}.", file=ctx.out)
    print(
        ctx.theme.warn("Anyone with this file AND the passphrase can use these accounts."),
        file=ctx.out,
    )
    return 0
