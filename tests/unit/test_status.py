"""Unit tests for mswap status command."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest

from mswap.agy.tokens import fingerprint
from mswap.cli.commands.status import _get_email_short, format_status, run
from mswap.cli.context import AppContext
from mswap.core.errors import UsageError
from mswap.core.models import Account, Bucket, Pool, QuotaSnapshot
from mswap.core.store import AccountStore, live_target, slot_target
from mswap.core.usage_cache import CacheEntry, UsageCache
from mswap.ui.theme import Theme
from mswap.util.clock import FrozenClock
from mswap.util.http import FakeHttp
from mswap.vault.memory import MemoryVault


def test_get_email_short() -> None:
    assert _get_email_short("alice@example.com") == "alice"
    assert _get_email_short("freeagysomething@example.com") == "freeagys"
    assert _get_email_short("1234567890@example.com") == "12345678"
    assert _get_email_short("short") == "short"
    assert _get_email_short("") == "?"


def test_format_status_with_complete_data() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    acc = Account(
        slot=1,
        email="freeagysomething@example.com",
        fp="fp1",
        alias="work",
        added_at=now,
        updated_at=now,
    )
    snap = QuotaSnapshot(
        fetched_at=now,
        pools=[
            Pool(
                key="gemini",
                name="Gemini",
                buckets=[
                    Bucket(window="5h", remaining=0.99, reset_at=now + timedelta(hours=3)),
                    Bucket(window="weekly", remaining=0.85, reset_at=now + timedelta(days=5)),
                ],
            ),
            Pool(
                key="3p",
                name="Claude & GPT",
                buckets=[
                    Bucket(window="5h", remaining=1.00, reset_at=None),
                    Bucket(window="weekly", remaining=0.70, reset_at=now + timedelta(days=4)),
                ],
            ),
        ],
    )
    entry = CacheEntry(
        fetched_at=now - timedelta(minutes=5),
        snapshot=snap,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )

    fmt = "{slot}:{email_short} G{gemini_5h}% C{3p_5h}%"
    text, data = format_status(fmt, acc, entry, now)
    assert text == "1:freeagys G99% C100%"
    assert data["slot"] == 1
    assert data["email"] == "freeagysomething@example.com"
    assert data["email_short"] == "freeagys"
    assert data["alias"] == "work"
    assert data["gemini_5h"] == 99
    assert data["gemini_week"] == 85
    assert data["3p_5h"] == 100
    assert data["3p_week"] == 70
    assert data["age"] == "5m"


def test_format_status_with_missing_quota_data() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    acc = Account(slot=2, email="bob@example.com", fp="fp2", added_at=now, updated_at=now)
    fmt = "{slot}:{email_short} G{gemini_5h}% C{3p_5h}%"
    text, data = format_status(fmt, acc, None, now)
    assert text == "2:bob G?% C?%"
    assert data["gemini_5h"] is None
    assert data["3p_5h"] is None
    assert data["age"] is None


def test_format_status_custom_placeholders() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    acc = Account(
        slot=1,
        email="alice@example.com",
        fp="fp1",
        alias="dev",
        added_at=now,
        updated_at=now,
    )
    entry = CacheEntry(
        fetched_at=now - timedelta(seconds=45),
        snapshot=None,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )
    custom_fmt = "[{slot} | {alias} | {email} | {age}]"
    text, _ = format_status(custom_fmt, acc, entry, now)
    assert text == "[1 | dev | alice@example.com | 45s]"


def test_format_status_unknown_placeholder_raises_usage_error() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    acc = Account(slot=1, email="alice@example.com", fp="fp1", added_at=now, updated_at=now)
    with pytest.raises(UsageError) as exc_info:
        format_status("{unknown_key}", acc, None, now)
    assert "Unknown format placeholder" in str(exc_info.value.message)


def test_status_run_no_accounts_exits_zero_silent(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "home")
    vault = MemoryVault()
    clock = FrozenClock(datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC))
    out = StringIO()
    ctx = AppContext(
        vault=vault,
        http=FakeHttp(),
        clock=clock,
        store=store,
        env={},
        out=out,
        err=StringIO(),
        theme=Theme(color=False),
        json=False,
        quiet=False,
    )

    args = argparse.Namespace(format=None, json=False)
    code = run(ctx, args)
    assert code == 0
    assert out.getvalue() == ""


def test_status_run_no_active_account_exits_zero_silent(tmp_path: Path) -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    store = AccountStore(tmp_path / "home")
    store.save([Account(slot=1, email="alice@example.com", fp="fp1", added_at=now, updated_at=now)])
    vault = MemoryVault()
    # live_target() is unset, so active is None
    clock = FrozenClock(now)
    out = StringIO()
    ctx = AppContext(
        vault=vault,
        http=FakeHttp(),
        clock=clock,
        store=store,
        env={},
        out=out,
        err=StringIO(),
        theme=Theme(color=False),
        json=False,
        quiet=False,
    )

    args = argparse.Namespace(format=None, json=False)
    code = run(ctx, args)
    assert code == 0
    assert out.getvalue() == ""


def test_status_run_active_account_with_cached_data_and_zero_http(tmp_path: Path) -> None:
    home = tmp_path / "home"
    store = AccountStore(home)
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    blob = b'{"token":{"refresh_token":"fake-rt-1","access_token":"ya29.1"}}'
    fp = fingerprint(blob)
    acc = Account(slot=1, email="alice@example.com", fp=fp, added_at=now, updated_at=now)
    store.save([acc])

    # Populate live target in vault so find_active returns acc
    vault = MemoryVault()
    vault.write(slot_target(1), blob, "alice@example.com")
    vault.write(live_target(), blob, "antigravity")

    # Populate usage cache
    cache = UsageCache(home / "usage.json")
    snap = QuotaSnapshot(
        fetched_at=now,
        pools=[
            Pool(
                key="gemini",
                name="Gemini",
                buckets=[Bucket(window="5h", remaining=0.95, reset_at=now + timedelta(hours=1))],
            ),
            Pool(
                key="3p",
                name="Claude & GPT",
                buckets=[Bucket(window="5h", remaining=0.50, reset_at=now + timedelta(hours=2))],
            ),
        ],
    )
    cache.put_snapshot(fp, snap, now)

    clock = FrozenClock(now)
    fake_http = FakeHttp()  # No routes registered
    out = StringIO()
    ctx = AppContext(
        vault=vault,
        http=fake_http,
        clock=clock,
        store=store,
        env={},
        out=out,
        err=StringIO(),
        theme=Theme(color=False),
        json=False,
        quiet=False,
    )

    args = argparse.Namespace(format=None, json=False)
    code = run(ctx, args)
    assert code == 0
    assert out.getvalue().strip() == "1:alice G95% C50%"
    assert len(fake_http.requests) == 0


def test_status_run_json_output(tmp_path: Path) -> None:
    home = tmp_path / "home"
    store = AccountStore(home)
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    blob = b'{"token":{"refresh_token":"fake-rt-1"}}'
    fp = fingerprint(blob)
    acc = Account(slot=1, email="alice@example.com", fp=fp, added_at=now, updated_at=now)
    store.save([acc])

    vault = MemoryVault()
    vault.write(live_target(), blob, "antigravity")

    clock = FrozenClock(now)
    out = StringIO()
    ctx = AppContext(
        vault=vault,
        http=FakeHttp(),
        clock=clock,
        store=store,
        env={},
        out=out,
        err=StringIO(),
        theme=Theme(color=False),
        json=True,
        quiet=False,
    )

    args = argparse.Namespace(format=None, json=True)
    code = run(ctx, args)
    assert code == 0

    payload = json.loads(out.getvalue())
    assert payload["schema"] == 1
    assert payload["ok"] is True
    assert payload["command"] == "status"
    assert payload["data"]["slot"] == 1
    assert payload["data"]["email"] == "alice@example.com"
    assert payload["data"]["formatted"] == "1:alice G?% C?%"
