"""Token management, parsing, fingerprinting, and refresh for agy."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from mswap.core.errors import CorruptState, MswapError
from mswap.util.http import Http

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - OAuth endpoint URL


def parse_go_time(s: str | None) -> datetime | None:
    """Parse a Go/RFC 3339 timestamp with arbitrary fractional seconds digits."""
    if not s:
        return None
    # Truncate fractional seconds beyond 6 digits for Python's fromisoformat
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def validate_blob(blob: bytes) -> dict[str, Any]:
    """Validate that a blob is valid JSON and has a non-empty refresh_token."""
    with contextlib.suppress(Exception):
        data = json.loads(blob)
        if isinstance(data, dict):
            token = data.get("token")
            if (
                isinstance(token, dict)
                and isinstance(token.get("refresh_token"), str)
                and token["refresh_token"]
            ):
                return data
    raise CorruptState(
        "Saved login is damaged.",
        hint="Sign in as that account in agy and run `mswap add`.",
    )


def fingerprint(blob: bytes) -> str:
    """Identify an account copy by its refresh token, without storing the token."""
    rt = json.loads(blob)["token"]["refresh_token"]
    return hashlib.sha256(rt.encode("utf-8")).hexdigest()[:16]


def refresh(refresh_token: str, http: Http) -> dict[str, Any]:
    """Refresh an access token using discovered agy OAuth client details."""
    from mswap.agy.client_discovery import load_client, save_client_secret

    cfg = load_client()
    candidates: list[str] = [cfg["client_secret"]] if cfg.get("client_secret") else []
    candidates += [s for s in cfg.get("secrets", []) if s not in candidates]
    last_err: Exception | None = None

    for secret in candidates:
        resp = http.request(
            "POST",
            TOKEN_URL,
            form={
                "client_id": cfg["client_id"],
                "client_secret": secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status == 200:
            if cfg.get("client_secret") != secret:
                save_client_secret(secret)
            return resp.json()  # type: ignore[no-any-return]

        detail = ""
        try:
            err = resp.json()
            if isinstance(err, dict):
                detail = err.get("error_description") or err.get("error", {}).get("message") or ""
        except (ValueError, AttributeError):
            pass
        if not detail:
            detail = resp.body.decode(errors="replace")[:300]

        last_err = MswapError(f"HTTP {resp.status}: {detail}")
        if "client secret" in detail.lower() or "invalid_client" in detail.lower():
            continue
        raise last_err

    if last_err is not None:
        raise last_err
    raise MswapError("token refresh failed")


def ensure_fresh(blob: bytes, http: Http, now: datetime) -> tuple[str, bytes | None]:
    """Return a usable access token, plus an updated blob if refreshed."""
    data = json.loads(blob)
    tok = data["token"]
    exp = parse_go_time(tok.get("expiry"))

    now_cmp = now if now.tzinfo is not None else now.replace(tzinfo=dt.UTC)
    if exp is not None:
        exp_cmp = exp if exp.tzinfo is not None else exp.replace(tzinfo=dt.UTC)
        if exp_cmp - timedelta(minutes=2) > now_cmp:
            return str(tok["access_token"]), None

    fresh = refresh(tok["refresh_token"], http)
    tok["access_token"] = fresh["access_token"]
    expires_in = int(fresh.get("expires_in", 3599))
    expiry = now_cmp + timedelta(seconds=expires_in)
    tok["expiry"] = expiry.isoformat()
    return str(tok["access_token"]), json.dumps(data, separators=(",", ":")).encode("utf-8")
