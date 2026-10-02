"""Unit tests for core/usage.py refresh_usage orchestration and ordering."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from mswap.agy.paths import agy_exe
from mswap.cli.context import AppContext
from mswap.core.models import Account
from mswap.core.store import AccountStore, live_target, slot_target
from mswap.core.usage import refresh_usage
from mswap.util.http import json_response
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob


def _seed_client_config(store: AccountStore) -> None:
    exe = agy_exe()
    exe_sig = f"{exe.stat().st_size}:{int(exe.stat().st_mtime)}" if exe.exists() else "dummy:1"
    cfg = store.root / "client.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "exe_sig": exe_sig,
                "client_id": "1071006060591-testclient.apps.googleusercontent.com",
                "client_secret": "GOCSPX-testsecret",
                "version": "1.2.12",
            }
        ),
        encoding="utf-8",
    )


def _load_fixture(name: str) -> dict:
    p = Path(__file__).resolve().parent.parent / "fixtures" / "api" / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_parallel_fetch_preserves_account_order(ctx: AppContext, vault: MemoryVault) -> None:
    # Arrange: 4 accounts in a non-sorted slot order (e.g. slots 4, 2, 3, 1)
    _seed_client_config(ctx.store)
    now = ctx.clock.now()
    slots = [4, 2, 3, 1]
    accounts = []
    fresh_expiry = (now + timedelta(hours=2)).isoformat()
    for s in slots:
        blob = make_blob(s, expiry=fresh_expiry)
        vault.write(slot_target(s), blob, f"user{s}@example.com")
        accounts.append(
            Account(
                slot=s,
                email=f"user{s}@example.com",
                fp=f"fp0000000000000{s}",
                added_at=now,
                updated_at=now,
            )
        )
    ctx.store.save(accounts)
    vault.write(live_target(), make_blob(4, expiry=fresh_expiry), "antigravity")

    # Seed quota responses for each account
    for _ in range(len(slots)):
        ctx.http.add(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
            json_response(_load_fixture("quota_summary.json")),
        )

    # Act
    results = refresh_usage(ctx, accounts)

    # Assert: Output dict keys exactly match the input order: [4, 2, 3, 1]
    assert list(results.keys()) == [4, 2, 3, 1]
    for s in slots:
        assert results[s].snapshot is not None
        assert results[s].error is None


def test_quarantined_accounts_never_hit_network(ctx: AppContext, vault: MemoryVault) -> None:
    # Arrange
    _seed_client_config(ctx.store)
    now = ctx.clock.now()
    from mswap.core.models import Quarantine

    acc = Account(
        slot=1,
        email="quarantined@example.com",
        fp="fp_quarantined_1",
        added_at=now,
        updated_at=now,
        quarantined=Quarantine(reason="invalid_grant", at=now),
    )
    ctx.store.save([acc])
    vault.write(live_target(), make_blob(1), "antigravity")

    # Act: No HTTP routes configured on FakeHttp, will raise AssertionError if any request made
    results = refresh_usage(ctx, [acc], force=True)

    # Assert: Returned entry with quarantined error kind, zero network requests
    assert len(results) == 1
    entry = results[1]
    assert entry.snapshot is None
    assert entry.error is not None
    assert entry.error["kind"] == "quarantined"
    assert "saved login expired or revoked" in entry.error["message"]


def test_refresh_usage_fetches_and_persists_plan(ctx: AppContext, vault: MemoryVault) -> None:
    # Arrange
    _seed_client_config(ctx.store)
    now = ctx.clock.now()
    fresh_expiry = (now + timedelta(hours=2)).isoformat()
    blob = make_blob(1, expiry=fresh_expiry)
    vault.write(slot_target(1), blob, "user1@example.com")
    vault.write(live_target(), blob, "antigravity")

    acc = Account(
        slot=1,
        email="user1@example.com",
        fp="fp00000000000001",
        added_at=now,
        updated_at=now,
        plan=None,
    )
    ctx.store.save([acc])

    ctx.http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )
    ctx.http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        json_response({"paidTier": {"id": "g1-pro-tier"}}),
    )

    # Act
    results = refresh_usage(ctx, [acc])

    # Assert
    assert 1 in results
    saved_accs = ctx.store.load()
    assert len(saved_accs) == 1
    assert saved_accs[0].plan == "g1-pro-tier"
    assert "plan_fetched_at" in saved_accs[0].extra


def test_refresh_usage_plan_cached_daily(ctx: AppContext, vault: MemoryVault) -> None:
    # Arrange
    _seed_client_config(ctx.store)
    now = ctx.clock.now()
    fresh_expiry = (now + timedelta(hours=2)).isoformat()
    blob = make_blob(1, expiry=fresh_expiry)
    vault.write(slot_target(1), blob, "user1@example.com")
    vault.write(live_target(), blob, "antigravity")

    acc = Account(
        slot=1,
        email="user1@example.com",
        fp="fp00000000000001",
        added_at=now,
        updated_at=now,
        plan="g1-pro-tier",
        extra={"plan_fetched_at": now.isoformat()},
    )
    ctx.store.save([acc])

    ctx.http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )

    # Act: No route for loadCodeAssist added; if called it would raise AssertionError
    results = refresh_usage(ctx, [acc], force=True)

    # Assert: Succeeded without error, plan unchanged
    assert 1 in results
    assert ctx.store.load()[0].plan == "g1-pro-tier"


def test_refresh_usage_plan_failure_ignored(ctx: AppContext, vault: MemoryVault) -> None:
    # Arrange
    _seed_client_config(ctx.store)
    now = ctx.clock.now()
    fresh_expiry = (now + timedelta(hours=2)).isoformat()
    blob = make_blob(1, expiry=fresh_expiry)
    vault.write(slot_target(1), blob, "user1@example.com")
    vault.write(live_target(), blob, "antigravity")

    acc = Account(
        slot=1,
        email="user1@example.com",
        fp="fp00000000000001",
        added_at=now,
        updated_at=now,
        plan=None,
    )
    ctx.store.save([acc])

    ctx.http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )
    from mswap.util.http import HttpResponse

    ctx.http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        HttpResponse(status=500, body=b"Internal Server Error", headers={}),
    )

    # Act: refresh_usage ignores plan failure and returns quota snapshot
    results = refresh_usage(ctx, [acc])

    # Assert
    assert 1 in results
    assert results[1].snapshot is not None
    assert ctx.store.load()[0].plan is None
