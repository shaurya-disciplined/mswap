"""Integration tests for usage cache, adaptive polling, 429 backoff, and list rendering."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mswap.agy.paths import agy_exe
from mswap.agy.tokens import fingerprint
from mswap.cli import main
from mswap.core.models import Account
from mswap.core.store import AccountStore, live_target, slot_target
from mswap.util.http import FakeHttp, HttpResponse, json_response
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob


def _seed_config(root: Path) -> None:
    exe = agy_exe()
    exe_sig = f"{exe.stat().st_size}:{int(exe.stat().st_mtime)}" if exe.exists() else "dummy:1"
    for fname in ("config.json", "client.json"):
        cfg = root / fname
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


def _load_fixture(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "fixtures" / "api" / name
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _seed_accounts(
    store: AccountStore, vault: MemoryVault, count: int, now: datetime
) -> list[Account]:
    accounts = []
    fresh_expiry = (now + timedelta(hours=2)).isoformat()
    for i in range(1, count + 1):
        blob = make_blob(i, expiry=fresh_expiry)
        vault.write(slot_target(i), blob, f"user{i}@example.com")
        acc = Account(
            slot=i,
            email=f"user{i}@example.com",
            fp=fingerprint(blob),
            added_at=now,
            updated_at=now,
        )
        accounts.append(acc)
    store.save(accounts)
    vault.write(live_target(), make_blob(1, expiry=fresh_expiry), "antigravity")
    return accounts


def test_list_five_accounts_makes_five_calls_then_zero(
    vault: MemoryVault,
    http: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    home = Path(os.environ["MSWAP_HOME"])
    _seed_config(home)
    store = AccountStore(home)
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    _seed_accounts(store, vault, 5, now)

    quota_json = _load_fixture("quota_summary.json")
    for _ in range(5):
        http.add(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
            json_response(quota_json),
        )

    # Act 1: First list run
    rc1 = main(["list"])
    assert rc1 == 0
    captured1 = capsys.readouterr()
    assert "user1@example.com (active)" in captured1.out
    assert len(http.requests) == 5

    # Act 2: Immediate second list run (within TTL)
    rc2 = main(["list"])
    assert rc2 == 0
    captured2 = capsys.readouterr()
    assert "user1@example.com (active)" in captured2.out

    # Assert: Exactly 5 total HTTP calls across both runs (0 calls on second run)
    assert len(http.requests) == 5


def test_429_sets_backoff_and_next_list_within_backoff_makes_no_calls(
    vault: MemoryVault,
    http: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    home = Path(os.environ["MSWAP_HOME"])
    _seed_config(home)
    store = AccountStore(home)
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    _seed_accounts(store, vault, 1, now)

    # First request returns 429
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        HttpResponse(status=429, body=b"Rate limit exceeded", headers={}),
    )

    # Act 1: First run gets 429 and enters backoff
    rc1 = main(["list"])
    assert rc1 == 0
    captured1 = capsys.readouterr()
    assert len(http.requests) == 1
    assert "rate-limited, retrying after" in captured1.out

    # Act 2: Immediate second run within backoff
    rc2 = main(["list"])
    assert rc2 == 0
    captured2 = capsys.readouterr()
    # Assert: Still exactly 1 HTTP call (no calls made on second run)
    assert len(http.requests) == 1
    assert "rate-limited, retrying after" in captured2.out


def test_force_bypasses_staleness_but_not_backoff(
    vault: MemoryVault,
    http: FakeHttp,
    clock: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    home = Path(os.environ["MSWAP_HOME"])
    _seed_config(home)
    store = AccountStore(home)
    now = clock.now()
    _seed_accounts(store, vault, 1, now)

    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        HttpResponse(status=429, body=b"Rate limit exceeded", headers={}),
    )

    # Trigger 429 and backoff
    main(["list"])
    capsys.readouterr()
    assert len(http.requests) == 1

    # Act 1: Run with --refresh during backoff
    # Backoff is respected even with --refresh!
    rc_refresh = main(["list", "--refresh"])
    assert rc_refresh == 0
    captured_refresh = capsys.readouterr()
    assert len(http.requests) == 1  # No new HTTP call made
    assert "rate-limited, retrying after" in captured_refresh.out

    # Act 2: Advance clock past 60s backoff
    clock.advance(65.0)
    http._routes.clear()
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )

    # Now --refresh attempts fetch again and succeeds, clearing backoff
    rc_after = main(["list", "--refresh"])
    assert rc_after == 0
    captured_after = capsys.readouterr()
    assert len(http.requests) == 2  # New HTTP call succeeded
    assert "Gemini" in captured_after.out
    assert "rate-limited" not in captured_after.out


def test_list_stale_shows_age_suffix_and_json_flag(
    vault: MemoryVault,
    http: FakeHttp,
    clock: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    home = Path(os.environ["MSWAP_HOME"])
    _seed_config(home)
    store = AccountStore(home)
    now = clock.now()
    _seed_accounts(store, vault, 1, now)

    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )

    # First run fetches data
    main(["list"])
    capsys.readouterr()

    # Advance clock by 3 minutes (180s > active TTL of 60s)
    clock.advance(180.0)
    # Refresh fails, preserving older snapshot
    http._routes.clear()
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        HttpResponse(status=500, body=b"Server Error", headers={}),
    )

    # Act 1: Human list shows age
    rc = main(["list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "3m ago" in captured.out

    # Act 2: JSON list shows stale=True
    rc_json = main(["list", "--json"])
    assert rc_json == 0
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    acc_usage = data["data"]["accounts"][0]["usage"]
    assert acc_usage["stale"] is True
    assert acc_usage["error"] is not None


def test_last_check_failed_preserves_older_snapshot(
    vault: MemoryVault,
    http: FakeHttp,
    clock: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("MSWAP_DEBUG", "1")
    home = Path(os.environ["MSWAP_HOME"])
    _seed_config(home)
    store = AccountStore(home)
    now = clock.now()
    _seed_accounts(store, vault, 1, now)

    # First fetch succeeds
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )
    main(["list"])
    capsys.readouterr()

    # Advance clock and force refresh with 500 error
    clock.advance(200.0)
    http._routes.clear()
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        HttpResponse(status=500, body=b"Internal Server Error", headers={}),
    )
    # retry also fails
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        HttpResponse(status=500, body=b"Internal Server Error", headers={}),
    )

    rc = main(["list", "--refresh"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Gemini" in captured.out
    assert "last check failed:" in captured.out
