"""Unit tests for agy tokens module."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mswap.agy.client_discovery import OAuthClient
from mswap.agy.paths import agy_exe
from mswap.agy.tokens import (
    ClientRejected,
    Fresh,
    TokenDead,
    TokenService,
    ensure_fresh,
    fingerprint,
    format_go_time,
    parse_go_time,
    refresh,
    validate_blob,
)
from mswap.cli.commands import list_
from mswap.cli.context import AppContext
from mswap.core.errors import ApiError, CorruptState
from mswap.core.models import Account, Quarantine
from mswap.core.store import live_target, slot_target
from mswap.util.http import FakeHttp, json_response
from tests.conftest import make_blob


def _seed_config(secrets: list[str] | None = None, client_secret: str | None = None) -> None:
    config_file = Path(os.environ["MSWAP_HOME"]) / "client.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    exe = agy_exe()
    exe_sig = f"{exe.stat().st_size}:{int(exe.stat().st_mtime)}" if exe.exists() else "dummy:1"
    sec_list = secrets or ["GOCSPX-FAKEsecret12345678901234"]
    config_file.write_text(
        json.dumps(
            {
                "exe_sig": exe_sig,
                "client_id": (
                    "1071006060591-faketestclient12345678901234.apps.googleusercontent.com"
                ),
                "secrets": sec_list,
                "client_secret": client_secret if client_secret is not None else sec_list[0],
                "version": "1.2.12",
            }
        ),
        encoding="utf-8",
    )


def test_parse_and_format_go_time_round_trip() -> None:
    # Test fractional digit variants: 0, 3, 6, 7, 9
    # Test timezone variants: Z, +05:30, -08:00
    cases = [
        "2026-10-02T13:00:00Z",
        "2026-10-02T13:00:00+05:30",
        "2026-10-02T13:00:00-08:00",
        "2026-10-02T13:00:00.123Z",
        "2026-10-02T13:00:00.123456+05:30",
        "2026-09-29T21:14:52.9821871+05:30",
        "2026-10-02T13:00:00.123456789-08:00",
    ]
    for case in cases:
        parsed = parse_go_time(case)
        assert parsed is not None
        assert parsed.tzinfo is not None

        # Format round trip preserves local time point
        formatted = format_go_time(parsed)
        reparsed = parse_go_time(formatted)
        assert reparsed == parsed

    # Check specific fields for 7 digits
    t7 = parse_go_time("2026-09-29T21:14:52.9821871+05:30")
    assert t7.year == 2026
    assert t7.microsecond == 982187
    assert t7.utcoffset() == timedelta(hours=5, minutes=30)

    # Check 9 digits truncated to 6
    t9 = parse_go_time("2026-09-29T21:14:52.123456789Z")
    assert t9.microsecond == 123456
    assert t9.utcoffset() == timedelta(0)

    # Garbage and empty strings raise CorruptState
    for bad in ["garbage", "", "   ", "not-iso", "2026-99-99T99:99:99Z"]:
        with pytest.raises(CorruptState):
            parse_go_time(bad)


def test_validate_blob() -> None:
    valid = make_blob(1)
    data = validate_blob(valid)
    assert data["token"]["refresh_token"] == "1//FAKE-refresh-1"

    # Non-JSON or missing refresh_token raises CorruptState
    with pytest.raises(CorruptState):
        validate_blob(b"not json")

    with pytest.raises(CorruptState):
        validate_blob(b"{}")

    with pytest.raises(CorruptState):
        validate_blob(b'{"token": {"refresh_token": ""}}')


def test_fingerprint() -> None:
    blob = make_blob(1)
    fp = fingerprint(blob)
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)
    assert fp == fingerprint(blob)


def test_ensure_fresh_when_not_expired(http: FakeHttp) -> None:
    client = OAuthClient("client_id", "client_secret")
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    expiry = (now + timedelta(minutes=5)).isoformat()
    blob = make_blob(1, expiry=expiry)

    fresh = ensure_fresh(blob, client, http, now)
    assert isinstance(fresh, Fresh)
    assert fresh.access_token == "ya29.FAKE-access-1"
    assert fresh.updated_blob is None
    assert len(http.requests) == 0

    # Also test tuple unpacking
    tok, updated = ensure_fresh(blob, client, http, now)
    assert tok == "ya29.FAKE-access-1"
    assert updated is None


def test_ensure_fresh_when_expired_refreshes(http: FakeHttp) -> None:
    client = OAuthClient("client_id", "client_secret")
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    expiry = (now + timedelta(minutes=1)).isoformat()  # <= 2min remaining

    raw = {
        "token": {
            "access_token": "ya29.FAKE-access-old",
            "token_type": "Bearer",
            "refresh_token": "1//FAKE-refresh-1",
            "expiry": expiry,
        },
        "auth_method": "consumer",
        "custom_unknown_field": "preserved",
    }
    blob = json.dumps(raw, separators=(",", ":")).encode("utf-8")

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response({"access_token": "ya29.FAKE-refreshed", "expires_in": 3599}),
    )

    fresh = ensure_fresh(blob, client, http, now)
    assert fresh.access_token == "ya29.FAKE-refreshed"
    assert fresh.updated_blob is not None

    updated_data = json.loads(fresh.updated_blob.decode("utf-8"))
    assert updated_data["token"]["access_token"] == "ya29.FAKE-refreshed"
    assert updated_data["token"]["refresh_token"] == "1//FAKE-refresh-1"
    assert updated_data["auth_method"] == "consumer"
    assert updated_data["custom_unknown_field"] == "preserved"

    parsed_expiry = parse_go_time(updated_data["token"]["expiry"])
    assert parsed_expiry is not None
    assert parsed_expiry == (now + timedelta(seconds=3599)).astimezone()


def test_ensure_fresh_error_matrix() -> None:
    client = OAuthClient("client_id", "client_secret")
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    expired_blob = make_blob(1, expiry=(now - timedelta(minutes=1)).isoformat())

    # 1. 400 invalid_grant -> TokenDead
    http1 = FakeHttp()
    http1.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_grant", "error_description": "Token revoked."},
            status=400,
        ),
    )
    with pytest.raises(TokenDead) as exc_info:
        ensure_fresh(expired_blob, client, http1, now)
    assert exc_info.value.code == 4
    assert "expired or was revoked" in exc_info.value.message

    # 2. 401 invalid_client -> ClientRejected
    http2 = FakeHttp()
    http2.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_client", "error_description": "The client secret is invalid."},
            status=401,
        ),
    )
    with pytest.raises(ClientRejected):
        ensure_fresh(expired_blob, client, http2, now)

    # 3. 500 server error -> ApiError
    http3 = FakeHttp()
    http3.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response({"error": "server_error"}, status=500),
    )
    with pytest.raises(ApiError) as api_exc:
        ensure_fresh(expired_blob, client, http3, now)
    assert api_exc.value.status == 500
    assert api_exc.value.endpoint == "oauth2/token"


def test_token_service_slot_refresh_writes_slot_only(ctx: AppContext) -> None:
    _seed_config()
    now = ctx.clock.now()
    account = Account(
        slot=1,
        email="alice@example.com",
        fp=fingerprint(make_blob(1)),
        added_at=now,
        updated_at=now,
    )
    ctx.store.save([account])

    expired_slot_blob = make_blob(1, expiry=(now - timedelta(minutes=10)).isoformat())
    live_blob = make_blob(2)
    ctx.vault.write(slot_target(1), expired_slot_blob, account.email)
    ctx.vault.write(live_target(), live_blob, "antigravity")

    # Clear vault calls recorded during setup
    ctx.vault.calls.clear()

    # FakeHttp returns 200 on refresh
    assert isinstance(ctx.http, FakeHttp)
    ctx.http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response({"access_token": "ya29.FAKE-slot-refreshed", "expires_in": 3599}),
    )

    svc = TokenService(ctx)
    token = svc.fresh_for_slot(account)
    assert token == "ya29.FAKE-slot-refreshed"

    # Assert slot 1 was written
    slot_writes = [c for c in ctx.vault.calls if c[0] == "write" and c[1] == slot_target(1)]
    assert len(slot_writes) == 1

    # Assert live_target was NEVER written
    live_writes = [c for c in ctx.vault.calls if c[0] == "write" and c[1] == live_target()]
    assert len(live_writes) == 0


def test_token_service_token_dead_quarantines_and_persists(ctx: AppContext) -> None:
    _seed_config()
    now = ctx.clock.now()
    account = Account(
        slot=1,
        email="alice@example.com",
        fp=fingerprint(make_blob(1)),
        added_at=now,
        updated_at=now,
    )
    ctx.store.save([account])

    expired_blob = make_blob(1, expiry=(now - timedelta(minutes=10)).isoformat())
    ctx.vault.write(slot_target(1), expired_blob, account.email)

    assert isinstance(ctx.http, FakeHttp)
    ctx.http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_grant", "error_description": "Token revoked."},
            status=400,
        ),
    )

    svc = TokenService(ctx)
    with pytest.raises(TokenDead):
        svc.fresh_for_slot(account)

    # Account is now quarantined in store
    loaded = ctx.store.load()
    assert loaded[0].quarantined is not None
    assert loaded[0].quarantined.reason == "invalid_grant"

    # Event "quarantine" was emitted
    log_file = ctx.store.root / "events.log"
    assert log_file.exists()
    events_text = log_file.read_text(encoding="utf-8")
    assert '"event": "quarantine"' in events_text
    assert '"slot": 1' in events_text


def test_token_service_success_clears_quarantine(ctx: AppContext) -> None:
    _seed_config()
    now = ctx.clock.now()
    quarantined_account = Account(
        slot=1,
        email="alice@example.com",
        fp=fingerprint(make_blob(1)),
        added_at=now,
        updated_at=now,
        quarantined=Quarantine(reason="invalid_grant", at=now),
    )
    ctx.store.save([quarantined_account])

    expired_blob = make_blob(1, expiry=(now - timedelta(minutes=10)).isoformat())
    ctx.vault.write(slot_target(1), expired_blob, quarantined_account.email)

    assert isinstance(ctx.http, FakeHttp)
    ctx.http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response({"access_token": "ya29.FAKE-recovered", "expires_in": 3599}),
    )

    svc = TokenService(ctx)
    token = svc.fresh_for_slot(quarantined_account)
    assert token == "ya29.FAKE-recovered"

    # Quarantine cleared in store
    loaded = ctx.store.load()
    assert loaded[0].quarantined is None


def test_token_service_double_invalid_client_raises_apierror(ctx: AppContext) -> None:
    _seed_config()
    now = ctx.clock.now()
    account = Account(
        slot=1,
        email="alice@example.com",
        fp=fingerprint(make_blob(1)),
        added_at=now,
        updated_at=now,
    )
    ctx.store.save([account])
    expired_blob = make_blob(1, expiry=(now - timedelta(minutes=10)).isoformat())
    ctx.vault.write(slot_target(1), expired_blob, account.email)

    assert isinstance(ctx.http, FakeHttp)
    # Both initial call and rediscovery retry return 401 invalid_client
    ctx.http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_client", "error_description": "Bad secret 1"},
            status=401,
        ),
        json_response(
            {"error": "invalid_client", "error_description": "Bad secret 2"},
            status=401,
        ),
    )

    svc = TokenService(ctx)
    with pytest.raises(ApiError) as exc_info:
        svc.fresh_for_slot(account)

    assert "agy's sign-in client changed and couldn't be re-detected." in exc_info.value.message
    assert exc_info.value.hint == "Run `mswap doctor`."


def test_token_service_fresh_for_live_never_writes_vault(ctx: AppContext) -> None:
    _seed_config()
    now = ctx.clock.now()
    live_blob = make_blob(1, expiry=(now - timedelta(minutes=10)).isoformat())
    ctx.vault.write(live_target(), live_blob, "antigravity")
    ctx.vault.calls.clear()

    assert isinstance(ctx.http, FakeHttp)
    ctx.http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response({"access_token": "ya29.FAKE-live-refreshed", "expires_in": 3599}),
    )

    svc = TokenService(ctx)
    token = svc.fresh_for_live(live_blob)
    assert token == "ya29.FAKE-live-refreshed"

    # In-memory only: zero write calls to vault
    writes = [c for c in ctx.vault.calls if c[0] == "write"]
    assert len(writes) == 0


def test_list_command_displays_quarantined_account(ctx: AppContext) -> None:
    now = ctx.clock.now()
    account = Account(
        slot=2,
        email="quarantined@example.com",
        fp=fingerprint(make_blob(2)),
        added_at=now,
        updated_at=now,
        quarantined=Quarantine(reason="invalid_grant", at=now),
    )
    ctx.store.save([account])

    import argparse

    list_.run(ctx, argparse.Namespace())

    from io import StringIO

    assert isinstance(ctx.out, StringIO)
    output = ctx.out.getvalue()
    expected = "     n/a  saved login expired or revoked  → sign in as it in agy, then `mswap add`"
    assert expected in output


def test_refresh_legacy_function(http: FakeHttp) -> None:
    _seed_config()
    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response({"access_token": "ya29.FAKE-refreshed", "expires_in": 3599}),
    )
    res = refresh("1//sample", http)
    assert res["access_token"] == "ya29.FAKE-refreshed"
