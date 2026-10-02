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


def test_validate_alias_valid() -> None:
    from mswap.core.models import validate_alias

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    accounts = [Account(slot=1, email="a@example.com", fp="fp1", added_at=now, updated_at=now)]
    # Valid aliases should not raise
    for valid in ("a", "work", "a-b", "account-1234567890", "x123", "a" * 20):
        validate_alias(valid, accounts)


def test_validate_alias_invalid_patterns() -> None:
    from mswap.core.errors import UsageError
    from mswap.core.models import validate_alias

    accounts: list[Account] = []
    invalid_patterns = (
        "",
        "1work",
        "-work",
        "Work",
        "work_1",
        "a" * 21,
        "hello world",
        "test@foo",
    )
    for invalid in invalid_patterns:
        with pytest.raises(UsageError) as exc_info:
            validate_alias(invalid, accounts)
        assert exc_info.value.code == 64
        assert "Invalid alias name." in exc_info.value.message


def test_validate_alias_uniqueness() -> None:
    from mswap.core.errors import UsageError
    from mswap.core.models import validate_alias

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    accounts = [
        Account(
            slot=1,
            email="a@example.com",
            fp="fp1",
            added_at=now,
            updated_at=now,
            alias="primary",
        ),
        Account(
            slot=2,
            email="b@example.com",
            fp="fp2",
            added_at=now,
            updated_at=now,
            alias="secondary",
        ),
    ]

    # Updating account 1 to its own alias should pass when exclude_slot=1
    validate_alias("primary", accounts, exclude_slot=1)

    # Reusing account 1's alias for account 2 should fail
    with pytest.raises(UsageError) as exc_info:
        validate_alias("primary", accounts, exclude_slot=2)
    assert exc_info.value.code == 64
    assert "Alias 'primary' is already in use by account 1." in exc_info.value.message


def test_bucket_invariants_and_clamping() -> None:
    from mswap.core.models import Bucket

    reset = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    b1 = Bucket(window="5h", remaining=0.5, reset_at=reset)
    assert b1.window == "5h"
    assert b1.remaining == 0.5
    assert b1.reset_at == reset

    # Clamping below 0
    b_low = Bucket(window="5h", remaining=-0.5, reset_at=reset)
    assert b_low.remaining == 0.0

    # Clamping above 1 and resetting reset_at to None
    b_high = Bucket(window="5h", remaining=1.5, reset_at=reset)
    assert b_high.remaining == 1.0
    assert b_high.reset_at is None

    # remaining == 1.0 clears reset_at
    b_one = Bucket(window="weekly", remaining=1.0, reset_at=reset)
    assert b_one.reset_at is None

    with pytest.raises(FrozenInstanceError):
        b1.remaining = 0.9  # type: ignore[misc]


def test_bucket_json_roundtrip() -> None:
    from mswap.core.models import Bucket, bucket_from_json, bucket_to_json

    reset = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    b = Bucket(window="5h", remaining=0.85, reset_at=reset)
    d = bucket_to_json(b)
    assert d == {"window": "5h", "remaining": 0.85, "reset_at": "2026-10-02T12:00:00+00:00"}
    b2 = bucket_from_json(d)
    assert b2 == b

    # Test class methods
    assert b.to_json() == d
    assert Bucket.from_json(d) == b


def test_pool_tightest_and_json() -> None:
    from mswap.core.models import Bucket, Pool, pool_from_json, pool_to_json

    b1 = Bucket(window="5h", remaining=0.9, reset_at=None)
    b2 = Bucket(window="weekly", remaining=0.3, reset_at=None)
    pool = Pool(key="gemini", name="Gemini", buckets=(b1, b2))
    assert pool.key == "gemini"
    assert pool.name == "Gemini"
    assert pool.tightest() == b2

    d = pool_to_json(pool)
    assert d["key"] == "gemini"
    assert d["name"] == "Gemini"
    assert len(d["buckets"]) == 2
    assert pool_from_json(d) == pool

    # Empty buckets raises ValueError on tightest()
    empty_pool = Pool(key="3p", name="Claude & GPT", buckets=())
    with pytest.raises(ValueError, match="has no buckets"):
        empty_pool.tightest()


def test_quota_snapshot_methods_and_json() -> None:
    from mswap.core.models import (
        Bucket,
        Pool,
        QuotaSnapshot,
        quota_snapshot_from_json,
        quota_snapshot_to_json,
    )

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    b = Bucket(window="5h", remaining=0.5, reset_at=None)
    p1 = Pool(key="gemini", name="Gemini", buckets=(b,))
    p2 = Pool(key="3p", name="Claude & GPT", buckets=(b,))
    snap = QuotaSnapshot(fetched_at=now, pools=(p1, p2), source="models")

    assert snap.pool("gemini") == p1
    assert snap.pool("3p") == p2
    assert snap.pool("nonexistent") is None
    assert snap.source == "models"

    d = quota_snapshot_to_json(snap)
    assert d["source"] == "models"
    assert d["fetched_at"] == "2026-10-02T12:00:00+00:00"
    assert len(d["pools"]) == 2

    snap2 = quota_snapshot_from_json(d)
    assert snap2 == snap
