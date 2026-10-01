"""Unit tests for agy/api module."""

from __future__ import annotations

import pytest

from mswap.agy.api import quota_groups, whoami
from mswap.core.errors import MswapError
from mswap.util.http import FakeHttp, HttpResponse, json_response


def test_whoami_success(http: FakeHttp) -> None:
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response({"email": "alice@example.com"}),
    )
    email = whoami("token123", http)
    assert email == "alice@example.com"


def test_whoami_missing_email_returns_unknown(http: FakeHttp) -> None:
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response({}),
    )
    email = whoami("token123", http)
    assert email == "unknown"


def test_whoami_http_error(http: FakeHttp) -> None:
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response({"error": {"message": "Invalid credentials"}}, status=401),
    )
    with pytest.raises(MswapError, match="HTTP 401: Invalid credentials"):
        whoami("bad_token", http)


def test_whoami_http_error_raw_body(http: FakeHttp) -> None:
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        HttpResponse(status=500, body=b"Server error", headers={}),
    )
    with pytest.raises(MswapError, match="HTTP 500: Server error"):
        whoami("bad_token", http)


def test_quota_groups_success(http: FakeHttp) -> None:
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response({"groups": [{"displayName": "Gemini Models"}]}),
    )
    groups = quota_groups("token123", http, "1.2.12")
    assert len(groups) == 1
    assert groups[0]["displayName"] == "Gemini Models"


def test_quota_groups_missing_groups_key(http: FakeHttp) -> None:
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response({}),
    )
    groups = quota_groups("token123", http, "1.2.12")
    assert groups == []


def test_quota_groups_http_error(http: FakeHttp) -> None:
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response({"error": {"message": "Quota exceeded"}}, status=429),
    )
    with pytest.raises(MswapError, match="HTTP 429: Quota exceeded"):
        quota_groups("token123", http, "1.2.12")


def test_quota_groups_http_error_raw_body(http: FakeHttp) -> None:
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        HttpResponse(status=502, body=b"Bad Gateway", headers={}),
    )
    with pytest.raises(MswapError, match="HTTP 502: Bad Gateway"):
        quota_groups("token123", http, "1.2.12")
