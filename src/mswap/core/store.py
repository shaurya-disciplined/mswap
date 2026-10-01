"""Local data storage and credential target name helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mswap.agy.tokens import fingerprint

LIVE_USER = "antigravity"


def data_dir() -> Path:
    """Return mswap local data directory path."""
    if "MSWAP_HOME" in os.environ:
        return Path(os.environ["MSWAP_HOME"])
    return Path.home() / ".mswap"


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


def load_accounts() -> list[dict[str, Any]]:
    """Load accounts metadata list from accounts.json."""
    accounts_file = data_dir() / "accounts.json"
    if not accounts_file.exists():
        return []
    try:
        content = accounts_file.read_text(encoding="utf-8")
        data = json.loads(content)
        accounts = data.get("accounts", [])
        if isinstance(accounts, list):
            return accounts
        return []
    except (OSError, json.JSONDecodeError):
        return []


def save_accounts(accounts: list[dict[str, Any]]) -> None:
    """Save accounts metadata atomically to accounts.json, sorted by slot."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    accounts_file = d / "accounts.json"
    sorted_accounts = sorted(accounts, key=lambda a: int(a["slot"]))
    tmp = accounts_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"accounts": sorted_accounts}, indent=2), encoding="utf-8")
    tmp.replace(accounts_file)


def find_active(accounts: list[dict[str, Any]], blob: bytes | None) -> dict[str, Any] | None:
    """Identify the active account matching the live credential blob's fingerprint."""
    if not blob:
        return None
    fp = fingerprint(blob)
    return next((a for a in accounts if a.get("fp") == fp), None)
