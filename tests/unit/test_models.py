"""Unit tests for core/models module."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest

from mswap.core.models import Account, Quarantine, account_from_json, account_to_json


def test_quarantine_frozen_and_slots() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    q = Quarantine(reason="invalid_grant", at=now)
    assert q.reason == "invalid_grant"
    assert q.at == now
    with pytest.raises(FrozenInstanceError):
        q.reason = "revoked"  # type: ignore[misc]


def test_account_frozen_and_slots() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    a = Account(
        slot=1,
        email="alice@example.com",
        fp="5c48cb6c4dc2e6e1",
        added_at=now,
        updated_at=now,
    )
    assert a.slot == 1
    assert a.email == "alice@example.com"
    assert a.alias is None
    assert not a.disabled
    assert a.quarantined is None
    assert a.plan is None
    assert a.extra == {}
    with pytest.raises(FrozenInstanceError):
        a.slot = 2  # type: ignore[misc]


def test_account_from_json_defaults() -> None:
    data = {
        "slot": 1,
        "email": "alice@example.com",
        "fp": "5c48cb6c4dc2e6e1",
        "added_at": "2026-10-02T01:23:28+05:30",
    }
    a = account_from_json(data)
    assert a.slot == 1
    assert a.email == "alice@example.com"
    assert a.fp == "5c48cb6c4dc2e6e1"
    assert a.added_at.isoformat() == "2026-10-02T01:23:28+05:30"
    assert a.updated_at == a.added_at
    assert a.alias is None
    assert not a.disabled
    assert a.quarantined is None
    assert a.plan is None
    assert a.extra == {}


def test_account_from_json_naive_datetime_attaches_local_tz() -> None:
    data = {
        "slot": 1,
        "email": "alice@example.com",
        "fp": "5c48cb6c4dc2e6e1",
        "added_at": "2026-10-02T01:23:28",
    }
    a = account_from_json(data)
    assert a.added_at.tzinfo is not None
    assert a.updated_at.tzinfo is not None
    assert a.added_at.strftime("%Y-%m-%dT%H:%M:%S") == "2026-10-02T01:23:28"


def test_account_from_json_quarantined() -> None:
    data: dict[str, Any] = {
        "slot": 2,
        "email": "bob@example.com",
        "fp": "deadbeef12345678",
        "added_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-02T00:00:00Z",
        "quarantined": {
            "reason": "revoked",
            "at": "2026-10-02T01:00:00Z",
        },
    }
    a = account_from_json(data)
    assert a.quarantined is not None
    assert a.quarantined.reason == "revoked"
    assert a.quarantined.at.tzinfo == UTC


def test_account_from_json_preserves_extra_fields() -> None:
    data = {
        "slot": 3,
        "email": "charlie@example.com",
        "fp": "aabbccddeeff0011",
        "added_at": "2026-10-01T00:00:00Z",
        "color": "red",
        "custom_num": 42,
    }
    a = account_from_json(data)
    assert a.extra == {"color": "red", "custom_num": 42}


def test_account_to_json_merges_extra_and_serializes_quarantine() -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    q = Quarantine(reason="invalid_grant", at=now)
    a = Account(
        slot=1,
        email="alice@example.com",
        fp="5c48cb6c4dc2e6e1",
        added_at=now,
        updated_at=now,
        alias="work",
        disabled=True,
        quarantined=q,
        plan="g1-pro-tier",
        extra={"color": "red", "tags": ["primary"]},
    )
    serialized = account_to_json(a)
    assert serialized["slot"] == 1
    assert serialized["email"] == "alice@example.com"
    assert serialized["fp"] == "5c48cb6c4dc2e6e1"
    assert serialized["alias"] == "work"
    assert serialized["disabled"] is True
    assert serialized["plan"] == "g1-pro-tier"
    assert serialized["color"] == "red"
    assert serialized["tags"] == ["primary"]
    assert serialized["quarantined"] == {
        "reason": "invalid_grant",
        "at": now.isoformat(),
    }


def test_account_full_round_trip() -> None:
    now = datetime(2026, 10, 2, 12, 34, 56, tzinfo=UTC)
    orig = Account(
        slot=5,
        email="eve@example.com",
        fp="1122334455667788",
        added_at=now,
        updated_at=now,
        alias="backup",
        disabled=False,
        quarantined=Quarantine(reason="unknown", at=now),
        plan="standard",
        extra={"notes": "test account"},
    )
    dumped = account_to_json(orig)
    restored = account_from_json(dumped)
    assert restored == orig
    assert restored.extra == orig.extra
    assert restored.quarantined == orig.quarantined


def test_account_from_json_with_datetime_and_quarantine_objects() -> None:
    now_naive = datetime(2026, 10, 2, 12, 0)
    q = Quarantine(reason="invalid_grant", at=datetime(2026, 10, 2, 12, 0, tzinfo=UTC))
    data: dict[str, Any] = {
        "slot": 1,
        "email": "alice@example.com",
        "fp": "5c48cb6c4dc2e6e1",
        "added_at": now_naive,
        "updated_at": now_naive,
        "quarantined": q,
        "extra": {"nested_extra": True},
    }
    acc = account_from_json(data)
    assert acc.added_at.tzinfo is not None
    assert acc.updated_at.tzinfo is not None
    assert acc.quarantined == q
    assert acc.extra.get("nested_extra") is True
