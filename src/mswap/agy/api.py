"""Antigravity and Google OAuth API client functions."""

from __future__ import annotations

from typing import Any

from mswap.core.errors import MswapError
from mswap.util.http import Http

USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
QUOTA_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"


def whoami(token: str, http: Http) -> str:
    """Fetch the authenticated user's email address."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = http.request("GET", USERINFO_URL, headers=headers)
    if resp.status != 200:
        detail = ""
        try:
            err = resp.json()
            if isinstance(err, dict):
                error_obj = err.get("error")
                if isinstance(error_obj, dict):
                    detail = str(error_obj.get("message", ""))
        except (ValueError, AttributeError):
            pass
        if not detail:
            detail = resp.body.decode(errors="replace")[:300]
        raise MswapError(f"HTTP {resp.status}: {detail}")
    data = resp.json()
    if isinstance(data, dict):
        return str(data.get("email", "unknown"))
    return "unknown"


def quota_groups(token: str, http: Http, version: str) -> list[dict[str, Any]]:
    """Fetch user quota summary groups."""
    ua = f"antigravity/{version} windows/amd64"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": ua,
        "Content-Type": "application/json",
    }
    resp = http.request("POST", QUOTA_URL, json_body={}, headers=headers)
    if resp.status != 200:
        detail = ""
        try:
            err = resp.json()
            if isinstance(err, dict):
                error_obj = err.get("error")
                if isinstance(error_obj, dict):
                    detail = str(error_obj.get("message", ""))
        except (ValueError, AttributeError):
            pass
        if not detail:
            detail = resp.body.decode(errors="replace")[:300]
        raise MswapError(f"HTTP {resp.status}: {detail}")
    data = resp.json()
    if isinstance(data, dict):
        groups = data.get("groups", [])
        if isinstance(groups, list):
            return groups
    return []
