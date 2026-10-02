"""Antigravity and Google OAuth API client."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mswap.agy.install import os_arch, user_agent
from mswap.agy.tokens import parse_go_time
from mswap.core.errors import ApiError, MswapError, TokenExpired
from mswap.core.models import Bucket, Pool, QuotaSnapshot
from mswap.util.http import Http, HttpResponse

USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
QUOTA_SUMMARY_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
LOAD_CODE_ASSIST_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
FETCH_MODELS_URL = "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"


def _bucket_sort_key(b: Bucket) -> tuple[int, str]:
    if b.window == "5h":
        return (0, "")
    if b.window == "weekly":
        return (1, "")
    return (2, b.window)


def _pool_sort_key(p: Pool) -> tuple[int, str]:
    if p.key == "gemini":
        return (0, "")
    if p.key == "3p":
        return (1, "")
    return (2, p.key)


def _extract_detail(resp: HttpResponse) -> str:
    detail = ""
    with contextlib.suppress(Exception):
        err = resp.json()
        if isinstance(err, dict):
            error_obj = err.get("error")
            if isinstance(error_obj, dict):
                detail = str(error_obj.get("message", ""))
    if not detail:
        detail = resp.body.decode(errors="replace")[:300]
    return detail


class AgyApi:
    """Typed, resilient API client for Antigravity quota and user endpoints."""

    def __init__(
        self,
        http: Http,
        ua: str,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Any = None,
    ) -> None:
        self.http = http
        self.ua = ua
        self.sleeper = sleeper
        self.clock = clock

    def _now(self) -> datetime:
        if self.clock is not None:
            now_val = self.clock.now()
            if isinstance(now_val, datetime):
                return (
                    now_val.replace(tzinfo=UTC)
                    if now_val.tzinfo is None
                    else now_val.astimezone(UTC)
                )
        return datetime.now(UTC)

    def _request(
        self,
        method: str,
        url: str,
        *,
        access_token: str,
        json_body: Any = None,
        allow_404: bool = False,
        endpoint_name: str = "",
    ) -> HttpResponse:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self.ua,
        }
        resp = self.http.request(method, url, json_body=json_body, headers=headers)
        if 500 <= resp.status <= 599:
            self.sleeper(1.0)
            resp = self.http.request(method, url, json_body=json_body, headers=headers)

        if resp.status == 200:
            return resp

        if resp.status == 401:
            raise TokenExpired("Google rejected access token (HTTP 401).")

        if resp.status == 404:
            if allow_404:
                return resp
            raise ApiError(
                f"HTTP 404: {_extract_detail(resp)}",
                status=404,
                endpoint=endpoint_name or url,
                hint="agy's API may have changed. Run `mswap doctor`.",
            )

        if resp.status == 429:
            raise ApiError(
                "Google is rate-limiting quota checks.",
                status=429,
                endpoint=endpoint_name or url,
                kind="rate_limited",
                hint="Google is rate-limiting quota checks. mswap will retry later.",
            )

        raise ApiError(
            f"HTTP {resp.status}: {_extract_detail(resp)}",
            status=resp.status,
            endpoint=endpoint_name or url,
        )

    @staticmethod
    def _parse_summary(data: dict[str, Any], fetched_at: datetime) -> QuotaSnapshot:
        groups = data.get("groups")
        pools_map: dict[str, tuple[str, list[Bucket]]] = {}
        if isinstance(groups, list):
            for g in groups:
                if not isinstance(g, dict):
                    continue
                raw_buckets = g.get("buckets")
                if not isinstance(raw_buckets, list) or not raw_buckets:
                    continue
                valid_buckets = [b for b in raw_buckets if isinstance(b, dict)]
                if not valid_buckets:
                    continue
                first_id = str(valid_buckets[0].get("bucketId", ""))
                pool_key = first_id.split("-")[0] if "-" in first_id else first_id
                if not pool_key:
                    pool_key = "unknown"
                if pool_key == "gemini":
                    pool_name = "Gemini"
                elif pool_key == "3p":
                    pool_name = "Claude & GPT"
                else:
                    disp = g.get("displayName")
                    pool_name = str(disp) if disp else pool_key

                if pool_key not in pools_map:
                    pools_map[pool_key] = (pool_name, [])

                for b in valid_buckets:
                    window = str(b.get("window", ""))
                    rem_raw = b.get("remainingFraction", 0.0)
                    rem = float(rem_raw) if rem_raw is not None else 0.0
                    remaining = max(0.0, min(1.0, rem))
                    reset_at: datetime | None = None
                    if remaining < 1.0 and b.get("resetTime"):
                        try:
                            reset_at = parse_go_time(str(b["resetTime"]))
                        except Exception:
                            reset_at = None
                    pools_map[pool_key][1].append(
                        Bucket(
                            window=window,
                            remaining=remaining,
                            reset_at=reset_at,
                        )
                    )

        pools = [
            Pool(key=k, name=name, buckets=tuple(sorted(buckets, key=_bucket_sort_key)))
            for k, (name, buckets) in pools_map.items()
        ]
        pools.sort(key=_pool_sort_key)
        return QuotaSnapshot(fetched_at=fetched_at, pools=tuple(pools), source="summary")

    @staticmethod
    def _parse_models(data: dict[str, Any], fetched_at: datetime) -> QuotaSnapshot:
        models_dict = data.get("models")
        if not isinstance(models_dict, dict):
            models_dict = {}

        pool_fractions: dict[str, list[float]] = {}
        pool_resets: dict[str, list[datetime]] = {}
        pool_names: dict[str, str] = {}

        for _model_id, model_info in models_dict.items():
            if not isinstance(model_info, dict):
                continue
            disp = model_info.get("displayName")
            if not disp or not isinstance(disp, str) or not disp.strip():
                continue
            provider = str(model_info.get("modelProvider", ""))
            prov_upper = provider.upper()
            if prov_upper == "MODEL_PROVIDER_GOOGLE" or "GOOGLE" in prov_upper:
                pool_key = "gemini"
                pool_name = "Gemini"
            elif "ANTHROPIC" in prov_upper or "OPENAI" in prov_upper:
                pool_key = "3p"
                pool_name = "Claude & GPT"
            else:
                pool_key = provider.lower() if provider else "unknown"
                pool_name = provider if provider else "Unknown"

            if pool_key not in pool_fractions:
                pool_fractions[pool_key] = []
                pool_resets[pool_key] = []
                pool_names[pool_key] = pool_name

            quota_info = model_info.get("quotaInfo")
            if isinstance(quota_info, dict):
                rem_raw = quota_info.get("remainingFraction", 0.0)
                rem = float(rem_raw) if rem_raw is not None else 0.0
                rem = max(0.0, min(1.0, rem))
                raw_reset = quota_info.get("resetTime")
                if raw_reset:
                    with contextlib.suppress(Exception):
                        dt_val = parse_go_time(str(raw_reset))
                        pool_resets[pool_key].append(dt_val)
            else:
                rem = 0.0

            pool_fractions[pool_key].append(rem)

        pools: list[Pool] = []
        for pool_key, fractions in pool_fractions.items():
            min_rem = min(fractions) if fractions else 0.0
            resets = pool_resets.get(pool_key, [])
            earliest_reset = min(resets) if (min_rem < 1.0 and resets) else None
            bucket = Bucket(window="model", remaining=min_rem, reset_at=earliest_reset)
            pools.append(
                Pool(
                    key=pool_key,
                    name=pool_names[pool_key],
                    buckets=(bucket,),
                )
            )

        pools.sort(key=_pool_sort_key)
        return QuotaSnapshot(fetched_at=fetched_at, pools=tuple(pools), source="models")

    def quota_summary(self, access_token: str) -> QuotaSnapshot:
        """Fetch and parse Antigravity quota summary, falling back to models if 404."""
        resp = self._request(
            "POST",
            QUOTA_SUMMARY_URL,
            access_token=access_token,
            json_body={},
            allow_404=True,
            endpoint_name="v1internal:retrieveUserQuotaSummary",
        )
        if resp.status == 404:
            return self._models_fallback(access_token)

        try:
            data = resp.json()
        except Exception as err:
            raise ApiError(
                "Unexpected response from Google's quota API.",
                status=resp.status,
                endpoint="v1internal:retrieveUserQuotaSummary",
            ) from err

        if not isinstance(data, dict):
            raise ApiError(
                "Unexpected response from Google's quota API.",
                status=resp.status,
                endpoint="v1internal:retrieveUserQuotaSummary",
            )

        return self._parse_summary(data, self._now())

    def _models_fallback(self, access_token: str) -> QuotaSnapshot:
        """Fallback to fetchAvailableModels when quota summary endpoint is not found."""
        resp = self._request(
            "POST",
            FETCH_MODELS_URL,
            access_token=access_token,
            json_body={},
            allow_404=False,
            endpoint_name="v1internal:fetchAvailableModels",
        )
        try:
            data = resp.json()
        except Exception as err:
            raise ApiError(
                "Unexpected response from Google's quota API.",
                status=resp.status,
                endpoint="v1internal:fetchAvailableModels",
            ) from err

        if not isinstance(data, dict):
            raise ApiError(
                "Unexpected response from Google's quota API.",
                status=resp.status,
                endpoint="v1internal:fetchAvailableModels",
            )

        return self._parse_models(data, self._now())

    def plan(self, access_token: str) -> str | None:
        """Fetch tier ID from loadCodeAssist endpoint."""
        resp = self._request(
            "POST",
            LOAD_CODE_ASSIST_URL,
            access_token=access_token,
            json_body={"metadata": {"ideType": "ANTIGRAVITY"}},
            endpoint_name="v1internal:loadCodeAssist",
        )
        try:
            data = resp.json()
        except Exception as err:
            raise ApiError(
                "Unexpected response from Google's quota API.",
                status=resp.status,
                endpoint="v1internal:loadCodeAssist",
            ) from err

        if not isinstance(data, dict):
            return None

        paid_tier = data.get("paidTier")
        if isinstance(paid_tier, dict) and paid_tier.get("id"):
            return str(paid_tier["id"])
        current_tier = data.get("currentTier")
        if isinstance(current_tier, dict) and current_tier.get("id"):
            return str(current_tier["id"])
        return None

    def email(self, access_token: str) -> str:
        """Fetch authenticated user email from userinfo endpoint."""
        resp = self._request(
            "GET",
            USERINFO_URL,
            access_token=access_token,
            endpoint_name="userinfo",
        )
        try:
            data = resp.json()
        except Exception as err:
            raise ApiError(
                "Unexpected response from Google's userinfo API.",
                status=resp.status,
                endpoint="userinfo",
            ) from err

        if not isinstance(data, dict) or not data.get("email"):
            raise ApiError(
                "Missing email in userinfo response.",
                status=resp.status,
                endpoint="userinfo",
            )
        return str(data["email"])


def whoami(token: str, http: Http, ua: str | None = None) -> str:
    """Fetch the authenticated user's email address."""
    api = AgyApi(http, ua or "antigravity/1.0.0 windows/amd64")
    try:
        return api.email(token)
    except ApiError as e:
        if e.message == "Missing email in userinfo response.":
            return "unknown"
        raise MswapError(e.message, hint=e.hint) from e
    except Exception as e:
        raise MswapError(str(e)) from e


def quota_groups(
    token: str,
    http: Http,
    version: str,
    ua: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch user quota summary groups (legacy compatibility wrapper)."""
    resolved_ua = ua if ua is not None else user_agent(version, os_arch())
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": resolved_ua,
    }
    resp = http.request("POST", QUOTA_SUMMARY_URL, json_body={}, headers=headers)
    if resp.status != 200:
        detail = _extract_detail(resp)
        raise MswapError(f"HTTP {resp.status}: {detail}")
    with contextlib.suppress(Exception):
        data = resp.json()
        if isinstance(data, dict):
            groups = data.get("groups", [])
            if isinstance(groups, list):
                return groups
    return []
