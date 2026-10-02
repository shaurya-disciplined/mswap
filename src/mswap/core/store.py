"""Local data storage, schema 2 account store, and credential target name helpers."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from mswap.agy.paths import data_dir as data_dir
from mswap.agy.tokens import fingerprint
from mswap.core.errors import CorruptState
from mswap.core.models import Account, account_from_json, account_to_json

LIVE_USER = "antigravity"


def legacy_dir() -> Path:
    """Return the legacy v1 data directory (~/.mswap)."""
    if "MSWAP_LEGACY_HOME" in os.environ:
        return Path(os.environ["MSWAP_LEGACY_HOME"])
    return Path.home() / ".mswap"


class AccountStore:
    """Versioned account metadata store in the platform data directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def migrate_if_needed(self) -> bool:
        """Migrate legacy v1 accounts and config to schema 2 if needed.

        Returns True if a migration was performed, False otherwise.
        """
        root_accounts = self.root / "accounts.json"
        if root_accounts.exists():
            return False

        legacy = legacy_dir()
        legacy_accounts = legacy / "accounts.json"
        if not legacy_accounts.exists():
            return False

        try:
            content = legacy_accounts.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as e:
            raise CorruptState(
                f"Couldn't read the old mswap data at {legacy_accounts}.",
                hint="Fix or move that file, then run mswap again.",
            ) from e

        if (
            not isinstance(data, dict)
            or "accounts" not in data
            or not isinstance(data["accounts"], list)
        ):
            raise CorruptState(
                f"Couldn't read the old mswap data at {legacy_accounts}.",
                hint="Fix or move that file, then run mswap again.",
            )

        # Rule b: only migrate if without "schema"
        if "schema" in data:
            return False

        migrated_accounts: list[Account] = []
        for item in data["accounts"]:
            if not isinstance(item, dict):
                raise CorruptState(
                    f"Couldn't read the old mswap data at {legacy_accounts}.",
                    hint="Fix or move that file, then run mswap again.",
                )
            raw_added_at = item.get("added_at")
            if not raw_added_at or not isinstance(raw_added_at, str):
                raise CorruptState(
                    f"Couldn't read the old mswap data at {legacy_accounts}.",
                    hint="Fix or move that file, then run mswap again.",
                )
            try:
                dt_added = datetime.fromisoformat(raw_added_at)
            except Exception as e:
                raise CorruptState(
                    f"Couldn't read the old mswap data at {legacy_accounts}.",
                    hint="Fix or move that file, then run mswap again.",
                ) from e

            if dt_added.tzinfo is None:
                dt_added = dt_added.astimezone()
            dt_updated = dt_added

            known_keys = {
                "slot",
                "email",
                "fp",
                "added_at",
                "updated_at",
                "alias",
                "disabled",
                "quarantined",
                "plan",
            }
            extra = {k: v for k, v in item.items() if k not in known_keys}

            acc = Account(
                slot=int(item["slot"]),
                email=str(item["email"]),
                fp=str(item["fp"]),
                added_at=dt_added,
                updated_at=dt_updated,
                alias=item.get("alias"),
                disabled=bool(item.get("disabled", False)),
                quarantined=None,
                plan=item.get("plan"),
                extra=extra,
            )
            migrated_accounts.append(acc)

        # Save to root first
        self.save(migrated_accounts)

        # Rename legacy accounts.json -> accounts.v1.bak (never delete)
        legacy_accounts.replace(legacy / "accounts.v1.bak")

        # If legacy config.json exists, copy to root/client.json and rename legacy to config.v1.bak
        legacy_config = legacy / "config.json"
        if legacy_config.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    self.root.chmod(0o700)
            root_client = self.root / "client.json"
            shutil.copy2(legacy_config, root_client)
            legacy_config.replace(legacy / "config.v1.bak")

        return True

    def load(self) -> list[Account]:
        """Load accounts metadata list from schema 2 accounts.json."""
        self.migrate_if_needed()
        accounts_file = self.root / "accounts.json"
        if not accounts_file.exists():
            return []

        try:
            content = accounts_file.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as e:
            raise CorruptState(
                f"Couldn't read accounts data at {accounts_file}.",
                hint="Fix or remove that file, then run mswap again.",
            ) from e

        if not isinstance(data, dict):
            raise CorruptState(
                f"Couldn't read accounts data at {accounts_file}.",
                hint="Fix or remove that file, then run mswap again.",
            )

        schema = data.get("schema", 2)
        if isinstance(schema, int) and schema > 2:
            raise CorruptState(
                "This data was written by a newer mswap.",
                hint="Upgrade mswap.",
            )

        accounts_raw = data.get("accounts", [])
        if not isinstance(accounts_raw, list):
            raise CorruptState(
                f"Couldn't read accounts data at {accounts_file}.",
                hint="Fix or remove that file, then run mswap again.",
            )

        return [account_from_json(acc) for acc in accounts_raw]

    def save(self, accounts: Sequence[Account]) -> None:
        """Save accounts metadata atomically to accounts.json, sorted by slot."""
        self.root.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            with contextlib.suppress(OSError):
                self.root.chmod(0o700)
        target_file = self.root / "accounts.json"
        tmp_file = target_file.with_suffix(".tmp")
        sorted_accounts = sorted(accounts, key=lambda a: a.slot)
        payload = {
            "schema": 2,
            "accounts": [account_to_json(a) for a in sorted_accounts],
        }
        tmp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_file.replace(target_file)


def live_target() -> str:
    """Return the Windows Credential Manager target name agy reads."""
    target = os.environ.get("MSWAP_LIVE_TARGET", "gemini:antigravity")
    if target == "gemini:antigravity" and "PYTEST_CURRENT_TEST" in os.environ:
        raise AssertionError(
            "Safety net triggered: live_target() returned gemini:antigravity under pytest!"
        )
    return target


def vault_prefix() -> str:
    """Return prefix for mswap credential targets."""
    return os.environ.get("MSWAP_VAULT_PREFIX", "mswap:")


def slot_target(slot: int) -> str:
    """Return the credential target name for an account slot."""
    return f"{vault_prefix()}slot{slot}"


def backup_last() -> str:
    """Return the credential target name for backup-last."""
    return f"{vault_prefix()}backup-last"


def backup_original() -> str:
    """Return the credential target name for backup-original."""
    return f"{vault_prefix()}backup-original"


def load_accounts(store: AccountStore | None = None) -> list[Account]:
    """Convenience helper to load accounts from AccountStore."""
    s = store or AccountStore(data_dir())
    return s.load()


def save_accounts(
    accounts: Sequence[Account | dict[str, Any]], store: AccountStore | None = None
) -> None:
    """Convenience helper to save accounts to AccountStore."""
    s = store or AccountStore(data_dir())
    accs: list[Account] = []
    for a in accounts:
        if isinstance(a, Account):
            accs.append(a)
        else:
            accs.append(account_from_json(a))
    s.save(accs)


def find_active(accounts: Sequence[Account | dict[str, Any]], blob: bytes | None) -> Any | None:
    """Identify the active account matching the live credential blob's fingerprint."""
    if not blob:
        return None
    try:
        fp = fingerprint(blob)
    except Exception:
        return None
    for a in accounts:
        if isinstance(a, Account):
            if a.fp == fp:
                return a
        elif isinstance(a, dict) and a.get("fp") == fp:
            return a
    return None
