"""Data models for mswap accounts, quarantine, and quota."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from mswap.core.errors import UsageError

ALIAS_REGEX = re.compile(r"^[a-z][a-z0-9-]{0,19}$")


def validate_alias(
    alias: str,
    accounts: Sequence[Account],
    exclude_slot: int | None = None,
) -> None:
    """Validate alias format and uniqueness across accounts."""
    if not ALIAS_REGEX.match(alias):
        raise UsageError(
            "Invalid alias name.",
            hint=(
                "Aliases must start with a lowercase letter, contain only a-z, 0-9, and '-', "
                "and be at most 20 characters."
            ),
        )
    for a in accounts:
        if exclude_slot is not None and a.slot == exclude_slot:
            continue
        if a.alias is not None and a.alias.lower() == alias.lower():
            raise UsageError(
                f"Alias '{alias}' is already in use by account {a.slot}.",
                hint="Choose a unique alias.",
            )


@dataclass(frozen=True, slots=True)
class Quarantine:
    """Quarantine status for an account with a dead or revoked token."""

    reason: Literal["invalid_grant", "revoked", "unknown"]
    at: datetime  # aware UTC


@dataclass(frozen=True, slots=True)
class Account:
    """Antigravity user account metadata."""

    slot: int  # 1..99, stable, never reused while account exists
    email: str  # lowercase-normalised for comparison, stored as received
    fp: str  # 16 hex chars = sha256(refresh_token)[:16]
    added_at: datetime
    updated_at: datetime
    alias: str | None = None  # ^[a-z][a-z0-9-]{0,19}$, unique
    disabled: bool = False  # skipped by rotation/autopilot, explicit switch allowed
    quarantined: Quarantine | None = None
    plan: str | None = None  # e.g. "g1-pro-tier", from loadCodeAssist.paidTier.id
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False)


_KNOWN_ACCOUNT_KEYS = {
    "slot",
    "email",
    "fp",
    "added_at",
    "updated_at",
    "alias",
    "disabled",
    "quarantined",
    "plan",
    "extra",
}


def account_from_json(d: dict[str, Any]) -> Account:
    """Deserialize an Account from a dictionary, preserving unknown extra fields."""
    raw_added_at = d["added_at"]
    if isinstance(raw_added_at, datetime):
        added_at = raw_added_at
    else:
        added_at = datetime.fromisoformat(str(raw_added_at))
    if added_at.tzinfo is None:
        added_at = added_at.astimezone()

    raw_updated_at = d.get("updated_at")
    if raw_updated_at is not None:
        if isinstance(raw_updated_at, datetime):
            updated_at = raw_updated_at
        else:
            updated_at = datetime.fromisoformat(str(raw_updated_at))
        if updated_at.tzinfo is None:
            updated_at = updated_at.astimezone()
    else:
        updated_at = added_at

    quarantined: Quarantine | None = None
    q_data = d.get("quarantined")
    if isinstance(q_data, dict):
        raw_at = q_data["at"]
        parsed_at = raw_at if isinstance(raw_at, datetime) else datetime.fromisoformat(str(raw_at))
        q_at = (
            parsed_at.replace(tzinfo=UTC) if parsed_at.tzinfo is None else parsed_at.astimezone(UTC)
        )
        quarantined = Quarantine(reason=q_data["reason"], at=q_at)
    elif isinstance(q_data, Quarantine):
        quarantined = q_data

    extra = {k: v for k, v in d.items() if k not in _KNOWN_ACCOUNT_KEYS}
    if "extra" in d and isinstance(d["extra"], Mapping):
        extra.update(d["extra"])

    return Account(
        slot=int(d["slot"]),
        email=str(d["email"]),
        fp=str(d["fp"]),
        added_at=added_at,
        updated_at=updated_at,
        alias=d.get("alias"),
        disabled=bool(d.get("disabled", False)),
        quarantined=quarantined,
        plan=d.get("plan"),
        extra=extra,
    )


def account_to_json(a: Account) -> dict[str, Any]:
    """Serialize an Account to a dictionary for schema 2 JSON."""
    data: dict[str, Any] = {
        "slot": a.slot,
        "email": a.email,
        "fp": a.fp,
        "added_at": a.added_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
        "alias": a.alias,
        "disabled": a.disabled,
        "quarantined": (
            {
                "reason": a.quarantined.reason,
                "at": a.quarantined.at.isoformat(),
            }
            if a.quarantined is not None
            else None
        ),
        "plan": a.plan,
    }
    if a.extra:
        for k, v in a.extra.items():
            if k not in data:
                data[k] = v
    return data
