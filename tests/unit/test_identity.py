"""Unit tests for core/identity.py."""

from __future__ import annotations

from datetime import UTC, datetime

from mswap.core.identity import find_by_fp, fp_or_none
from mswap.core.models import Account
from tests.conftest import make_blob


def test_fp_or_none() -> None:
    assert fp_or_none(None) is None
    assert fp_or_none(b"not json") is None
    blob = make_blob(1)
    fp = fp_or_none(blob)
    assert fp is not None
    assert len(fp) == 16


def test_find_by_fp() -> None:
    blob1 = make_blob(1)
    blob2 = make_blob(2)
    fp1 = fp_or_none(blob1)
    fp2 = fp_or_none(blob2)
    assert fp1 is not None and fp2 is not None

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    accounts = [
        Account(slot=1, email="alice@example.com", fp=fp1, added_at=now, updated_at=now),
        Account(slot=2, email="bob@example.com", fp=fp2, added_at=now, updated_at=now),
    ]

    assert find_by_fp(accounts, blob1) == accounts[0]
    assert find_by_fp(accounts, blob2) == accounts[1]
    assert find_by_fp(accounts, make_blob(3)) is None
    assert find_by_fp(accounts, None) is None
    assert find_by_fp(accounts, b"corrupt") is None
