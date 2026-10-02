"""Unit tests for agy/api module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mswap.agy.api import (
    FETCH_MODELS_URL,
    LOAD_CODE_ASSIST_URL,
    QUOTA_SUMMARY_URL,
    USERINFO_URL,
    AgyApi,
    quota_groups,
    whoami,
)
from mswap.core.errors import ApiError, MswapError, TokenExpired
from mswap.util.http import FakeHttp, HttpResponse, json_response

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "api"
VARIANTS_DIR = FIXTURES_DIR / "quota_summary_variants"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# 1. Parse rules and Schema Tolerance Matrix
# ---------------------------------------------------------------------------


def test_quota_summary_parse_rules(http: FakeHttp) -> None:
    data = _load_json(FIXTURES_DIR / "quota_summary.json")
    http.add("POST", QUOTA_SUMMARY_URL, json_response(data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    snapshot = api.quota_summary("ya29.FAKE-token")

    assert snapshot.source == "summary"
    assert len(snapshot.pools) == 2

    # Pools sorted: gemini, 3p, others alpha
    gemini_pool = snapshot.pools[0]
    assert gemini_pool.key == "gemini"
    assert gemini_pool.name == "Gemini"

    # Buckets sorted: 5h first, then weekly
    assert len(gemini_pool.buckets) == 2
    b_5h, b_week = gemini_pool.buckets[0], gemini_pool.buckets[1]
    assert b_5h.window == "5h"
    assert b_5h.remaining == 1.0
    assert b_5h.reset_at is None  # remaining == 1.0 clears reset_at

    assert b_week.window == "weekly"
    assert abs(b_week.remaining - 0.9910955) < 1e-6
    assert b_week.reset_at == datetime(2026, 10, 8, 4, 59, 12, tzinfo=UTC)

    p_3p = snapshot.pools[1]
    assert p_3p.key == "3p"
    assert p_3p.name == "Claude & GPT"
    assert len(p_3p.buckets) == 2
    assert p_3p.buckets[0].window == "5h"
    assert p_3p.buckets[1].window == "weekly"


def test_schema_tolerance_missing_fraction(http: FakeHttp) -> None:
    data = _load_json(VARIANTS_DIR / "missing_fraction.json")
    http.add("POST", QUOTA_SUMMARY_URL, json_response(data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    snapshot = api.quota_summary("ya29.FAKE-token")

    pool = snapshot.pool("gemini")
    assert pool is not None
    # 5h remaining is 0.5, weekly missing fraction defaults to 0.0
    b_5h = next(b for b in pool.buckets if b.window == "5h")
    b_weekly = next(b for b in pool.buckets if b.window == "weekly")
    assert b_5h.remaining == 0.5
    assert b_weekly.remaining == 0.0


def test_schema_tolerance_integer_one(http: FakeHttp) -> None:
    data = _load_json(VARIANTS_DIR / "integer_one.json")
    http.add("POST", QUOTA_SUMMARY_URL, json_response(data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    snapshot = api.quota_summary("ya29.FAKE-token")

    pool = snapshot.pool("3p")
    assert pool is not None
    bucket = pool.buckets[0]
    assert isinstance(bucket.remaining, float)
    assert bucket.remaining == 1.0
    assert bucket.reset_at is None


def test_schema_tolerance_extra_fields(http: FakeHttp) -> None:
    data = _load_json(VARIANTS_DIR / "extra_fields.json")
    http.add("POST", QUOTA_SUMMARY_URL, json_response(data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    snapshot = api.quota_summary("ya29.FAKE-token")

    pool = snapshot.pool("gemini")
    assert pool is not None
    assert len(pool.buckets) == 1
    assert pool.buckets[0].remaining == 0.8


def test_schema_tolerance_unknown_pool(http: FakeHttp) -> None:
    data = _load_json(VARIANTS_DIR / "unknown_pool.json")
    http.add("POST", QUOTA_SUMMARY_URL, json_response(data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    snapshot = api.quota_summary("ya29.FAKE-token")

    pool = snapshot.pool("beta")
    assert pool is not None
    assert pool.key == "beta"
    assert pool.name == "Beta Experimental Models"
    assert len(pool.buckets) == 2


def test_schema_tolerance_empty_groups(http: FakeHttp) -> None:
    data = _load_json(VARIANTS_DIR / "empty_groups.json")
    http.add("POST", QUOTA_SUMMARY_URL, json_response(data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    snapshot = api.quota_summary("ya29.FAKE-token")

    assert len(snapshot.pools) == 1
    assert snapshot.pools[0].key == "gemini"


def test_quota_summary_malformed_json(http: FakeHttp) -> None:
    http.add(
        "POST",
        QUOTA_SUMMARY_URL,
        HttpResponse(status=200, body=b"<html>not json</html>", headers={}),
    )

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    with pytest.raises(ApiError, match=r"Unexpected response from Google's quota API\."):
        api.quota_summary("ya29.FAKE-token")


# ---------------------------------------------------------------------------
# 2. HTTP Status Code Mapping
# ---------------------------------------------------------------------------


def test_status_401_raises_token_expired(http: FakeHttp) -> None:
    http.add("POST", QUOTA_SUMMARY_URL, HttpResponse(status=401, body=b"Unauthorized", headers={}))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    with pytest.raises(TokenExpired):
        api.quota_summary("ya29.FAKE-token")


def test_status_404_on_summary_falls_back_to_models(http: FakeHttp) -> None:
    models_data = _load_json(FIXTURES_DIR / "fetch_available_models.json")
    http.add("POST", QUOTA_SUMMARY_URL, HttpResponse(status=404, body=b"Not Found", headers={}))
    http.add("POST", FETCH_MODELS_URL, json_response(models_data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    snapshot = api.quota_summary("ya29.FAKE-token")

    assert snapshot.source == "models"
    assert len(snapshot.pools) == 2

    gemini = snapshot.pool("gemini")
    assert gemini is not None
    assert gemini.name == "Gemini"
    assert len(gemini.buckets) == 1
    assert gemini.buckets[0].window == "model"
    # min remaining between 0.85 and 0.70 is 0.70
    assert abs(gemini.buckets[0].remaining - 0.70) < 1e-6
    # earliest reset between 12:00:00Z and 10:30:00Z is 10:30:00Z
    assert gemini.buckets[0].reset_at == datetime(2026, 10, 2, 10, 30, 0, tzinfo=UTC)

    p_3p = snapshot.pool("3p")
    assert p_3p is not None
    assert p_3p.name == "Claude & GPT"
    assert len(p_3p.buckets) == 1
    assert p_3p.buckets[0].window == "model"
    # min remaining between 0.9 (anthropic) and 0.6 (openai) is 0.6
    assert abs(p_3p.buckets[0].remaining - 0.60) < 1e-6
    # earliest reset between 15:00:00Z and 14:00:00Z is 14:00:00Z
    assert p_3p.buckets[0].reset_at == datetime(2026, 10, 2, 14, 0, 0, tzinfo=UTC)


def test_status_404_on_models_fallback_raises_apierror(http: FakeHttp) -> None:
    http.add("POST", QUOTA_SUMMARY_URL, HttpResponse(status=404, body=b"Not Found", headers={}))
    http.add("POST", FETCH_MODELS_URL, HttpResponse(status=404, body=b"Not Found", headers={}))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    with pytest.raises(ApiError) as exc_info:
        api.quota_summary("ya29.FAKE-token")
    assert exc_info.value.status == 404
    assert exc_info.value.hint == "agy's API may have changed. Run `mswap doctor`."


def test_status_404_on_plan_raises_apierror(http: FakeHttp) -> None:
    http.add("POST", LOAD_CODE_ASSIST_URL, HttpResponse(status=404, body=b"Not Found", headers={}))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    with pytest.raises(ApiError) as exc_info:
        api.plan("ya29.FAKE-token")
    assert exc_info.value.status == 404
    assert exc_info.value.hint == "agy's API may have changed. Run `mswap doctor`."


def test_status_404_on_email_raises_apierror(http: FakeHttp) -> None:
    http.add("GET", USERINFO_URL, HttpResponse(status=404, body=b"Not Found", headers={}))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    with pytest.raises(ApiError) as exc_info:
        api.email("ya29.FAKE-token")
    assert exc_info.value.status == 404
    assert exc_info.value.hint == "agy's API may have changed. Run `mswap doctor`."


def test_status_429_rate_limited(http: FakeHttp) -> None:
    http.add(
        "POST",
        QUOTA_SUMMARY_URL,
        json_response({"error": {"message": "Resource exhausted"}}, status=429),
    )

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    with pytest.raises(ApiError) as exc_info:
        api.quota_summary("ya29.FAKE-token")
    assert exc_info.value.status == 429
    assert exc_info.value.kind == "rate_limited"
    assert exc_info.value.hint == "Google is rate-limiting quota checks. mswap will retry later."


def test_status_5xx_retry_success(http: FakeHttp) -> None:
    sleeps: list[float] = []
    data = _load_json(FIXTURES_DIR / "quota_summary.json")

    http.add("POST", QUOTA_SUMMARY_URL, HttpResponse(status=500, body=b"Backend Error", headers={}))
    http.add("POST", QUOTA_SUMMARY_URL, json_response(data))

    api = AgyApi(
        http,
        "antigravity/1.2.12 windows/amd64",
        sleeper=sleeps.append,
    )
    snapshot = api.quota_summary("ya29.FAKE-token")
    assert len(snapshot.pools) == 2
    assert sleeps == [1.0]


def test_status_5xx_retry_failure(http: FakeHttp) -> None:
    sleeps: list[float] = []
    http.add("POST", QUOTA_SUMMARY_URL, HttpResponse(status=503, body=b"Unavailable", headers={}))
    http.add("POST", QUOTA_SUMMARY_URL, HttpResponse(status=503, body=b"Unavailable", headers={}))

    api = AgyApi(
        http,
        "antigravity/1.2.12 windows/amd64",
        sleeper=sleeps.append,
    )
    with pytest.raises(ApiError) as exc_info:
        api.quota_summary("ya29.FAKE-token")
    assert exc_info.value.status == 503
    assert sleeps == [1.0]


# ---------------------------------------------------------------------------
# 3. plan and email Endpoints
# ---------------------------------------------------------------------------


def test_plan_paid_tier_priority(http: FakeHttp) -> None:
    data = {
        "paidTier": {"id": "g1-pro-tier"},
        "currentTier": {"id": "free-tier"},
    }
    http.add("POST", LOAD_CODE_ASSIST_URL, json_response(data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    assert api.plan("ya29.FAKE-token") == "g1-pro-tier"


def test_plan_current_tier_fallback(http: FakeHttp) -> None:
    data = {"currentTier": {"id": "free-tier"}}
    http.add("POST", LOAD_CODE_ASSIST_URL, json_response(data))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    assert api.plan("ya29.FAKE-token") == "free-tier"


def test_plan_missing_tiers(http: FakeHttp) -> None:
    http.add("POST", LOAD_CODE_ASSIST_URL, json_response({}))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    assert api.plan("ya29.FAKE-token") is None


def test_email_success(http: FakeHttp) -> None:
    http.add("GET", USERINFO_URL, json_response({"email": "alice@example.com"}))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    assert api.email("ya29.FAKE-token") == "alice@example.com"


def test_email_missing_raises_apierror(http: FakeHttp) -> None:
    http.add("GET", USERINFO_URL, json_response({}))

    api = AgyApi(http, "antigravity/1.2.12 windows/amd64")
    with pytest.raises(ApiError, match=r"Missing email in userinfo response\."):
        api.email("ya29.FAKE-token")


# ---------------------------------------------------------------------------
# 4. User-Agent Header on all endpoints
# ---------------------------------------------------------------------------


def test_ua_header_present_on_all_endpoints(http: FakeHttp) -> None:
    ua = "antigravity/1.2.12 windows/amd64"
    api = AgyApi(http, ua)

    http.add("POST", QUOTA_SUMMARY_URL, json_response({"groups": []}))
    api.quota_summary("ya29.FAKE-token")

    http.add("POST", LOAD_CODE_ASSIST_URL, json_response({}))
    api.plan("ya29.FAKE-token")

    http.add("GET", USERINFO_URL, json_response({"email": "alice@example.com"}))
    api.email("ya29.FAKE-token")

    http.add("POST", FETCH_MODELS_URL, json_response({"models": {}}))
    api._models_fallback("ya29.FAKE-token")

    assert len(http.requests) == 4
    for req in http.requests:
        assert req["headers"].get("User-Agent") == ua
        assert req["headers"].get("Authorization") == "Bearer ya29.FAKE-token"


# ---------------------------------------------------------------------------
# 5. Legacy functions backward compatibility
# ---------------------------------------------------------------------------


def test_whoami_success(http: FakeHttp) -> None:
    http.add(
        "GET",
        USERINFO_URL,
        json_response({"email": "alice@example.com"}),
    )
    email = whoami("token123", http)
    assert email == "alice@example.com"


def test_whoami_missing_email_returns_unknown(http: FakeHttp) -> None:
    http.add(
        "GET",
        USERINFO_URL,
        json_response({}),
    )
    email = whoami("token123", http)
    assert email == "unknown"


def test_whoami_http_error(http: FakeHttp) -> None:
    http.add(
        "GET",
        USERINFO_URL,
        json_response({"error": {"message": "Invalid credentials"}}, status=401),
    )
    with pytest.raises(MswapError):
        whoami("bad_token", http)


def test_quota_groups_success(http: FakeHttp) -> None:
    http.add(
        "POST",
        QUOTA_SUMMARY_URL,
        json_response({"groups": [{"displayName": "Gemini Models"}]}),
    )
    groups = quota_groups("token123", http, "1.2.12")
    assert len(groups) == 1
    assert groups[0]["displayName"] == "Gemini Models"


def test_quota_groups_missing_groups_key(http: FakeHttp) -> None:
    http.add(
        "POST",
        QUOTA_SUMMARY_URL,
        json_response({}),
    )
    groups = quota_groups("token123", http, "1.2.12")
    assert groups == []
