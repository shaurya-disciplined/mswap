"""Unit tests for agy tokens module."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mswap.agy.paths import agy_exe
from mswap.agy.tokens import ensure_fresh, fingerprint, parse_go_time, refresh
from mswap.core.errors import MswapError
from mswap.util.http import FakeHttp, json_response
from tests.conftest import make_blob


def _seed_config(secrets: list[str] | None = None, client_secret: str | None = None) -> None:
    config_file = Path(os.environ["MSWAP_HOME"]) / "config.json"
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


def test_parse_go_time_formats() -> None:
    # 7 digits with offset
    t1 = parse_go_time("2026-09-29T21:14:52.9821871+05:30")
    assert t1 is not None
    assert t1.year == 2026
    assert t1.month == 9
    assert t1.day == 29
    assert t1.hour == 21
    assert t1.minute == 14
    assert t1.second == 52
    assert t1.microsecond == 982187
    assert t1.utcoffset() == timedelta(hours=5, minutes=30)

    # ...Z format
    t2 = parse_go_time("2026-09-29T21:14:52.9821871Z")
    assert t2 is not None
    assert t2.utcoffset() == timedelta(0)

    # No fraction
    t3 = parse_go_time("2026-09-29T21:14:52+05:30")
    assert t3 is not None
    assert t3.microsecond == 0

    # 9 digits
    t4 = parse_go_time("2026-09-29T21:14:52.982187123+05:30")
    assert t4 is not None
    assert t4.microsecond == 982187

    # Garbage and empty
    assert parse_go_time("garbage") is None
    assert parse_go_time("") is None
    assert parse_go_time(None) is None


def test_fingerprint() -> None:
    blob = make_blob(1)
    fp = fingerprint(blob)
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)
    assert fp == fingerprint(blob)


def test_ensure_fresh_when_not_expired(http: FakeHttp) -> None:
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    expiry = (now + timedelta(minutes=5)).isoformat()
    blob = make_blob(1, expiry=expiry)

    tok, updated = ensure_fresh(blob, http, now)
    assert tok == "ya29.FAKE-access-1"
    assert updated is None
    assert len(http.requests) == 0


def test_ensure_fresh_when_expired_refreshes(http: FakeHttp) -> None:
    _seed_config()
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

    fixture_path = (
        Path(__file__).resolve().parent.parent / "fixtures" / "api" / "token_refresh.json"
    )
    refresh_resp = json.loads(fixture_path.read_text(encoding="utf-8"))
    http.add("POST", "https://oauth2.googleapis.com/token", json_response(refresh_resp))

    tok, updated = ensure_fresh(blob, http, now)

    assert tok == "ya29.FAKE-refreshed"
    assert updated is not None

    updated_data = json.loads(updated.decode("utf-8"))
    assert updated_data["token"]["access_token"] == "ya29.FAKE-refreshed"
    assert updated_data["token"]["refresh_token"] == "1//FAKE-refresh-1"
    assert updated_data["auth_method"] == "consumer"
    assert updated_data["custom_unknown_field"] == "preserved"

    parsed_expiry = parse_go_time(updated_data["token"]["expiry"])
    assert parsed_expiry is not None
    assert parsed_expiry == now + timedelta(seconds=3599)


def test_refresh_multiple_secrets_retries(http: FakeHttp) -> None:
    _seed_config(
        secrets=["GOCSPX-FAKEbadsecret123456789012", "GOCSPX-FAKEgoodsecret12345678901"],
        client_secret="",
    )

    # First secret returns 401 invalid_client, second secret succeeds
    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_client", "error_description": "The client secret is invalid."},
            status=401,
        ),
        json_response({"access_token": "ya29.FAKE-retried-token", "expires_in": 3599}),
    )

    result = refresh("1//FAKE-refresh-1", http)
    assert result["access_token"] == "ya29.FAKE-retried-token"

    config_file = Path(os.environ["MSWAP_HOME"]) / "config.json"
    saved_cfg = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved_cfg["client_secret"] == "GOCSPX-FAKEgoodsecret12345678901"


def test_refresh_invalid_grant_raises_immediately(http: FakeHttp) -> None:
    _seed_config(
        secrets=["GOCSPX-FAKEbadsecret123456789012", "GOCSPX-FAKEgoodsecret12345678901"],
        client_secret="",
    )

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_grant", "error_description": "Token has been revoked."}, status=400
        ),
    )

    with pytest.raises(MswapError, match=r"HTTP 400: Token has been revoked\."):
        refresh("1//FAKE-refresh-1", http)

    assert len(http.requests) == 1  # Did not retry on invalid_grant


def test_refresh_all_secrets_fail_raises(http: FakeHttp) -> None:
    _seed_config(
        secrets=["GOCSPX-FAKEbadsecret123456789012"],
        client_secret="",
    )

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_client", "error_description": "Bad client secret"}, status=401
        ),
    )

    with pytest.raises(MswapError, match="HTTP 401: Bad client secret"):
        refresh("1//FAKE-refresh-1", http)
