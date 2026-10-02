"""Token management, parsing, fingerprinting, and refresh for agy."""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from mswap.agy.client_discovery import OAuthClient, discover, rediscover
from mswap.agy.paths import agy_exe
from mswap.core.errors import ApiError, CorruptState, MswapError, TokenDead
from mswap.core.models import Account, Quarantine
from mswap.util.http import Http


class TokenContext(Protocol):
    """Protocol for AppContext dependencies needed by TokenService."""

    @property
    def vault(self) -> Any: ...

    @property
    def http(self) -> Http: ...

    @property
    def clock(self) -> Any: ...

    @property
    def store(self) -> Any: ...

    @property
    def events(self) -> Any: ...


TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - OAuth endpoint URL


def parse_go_time(s: str) -> datetime:
    """Parse a Go/RFC 3339 timestamp with arbitrary fractional seconds digits."""
    if not isinstance(s, str) or not s.strip():
        raise CorruptState(
            "Saved login has an invalid timestamp.",
            hint="Sign in as that account in agy and run `mswap add`.",
        )
    # Truncate fractional seconds beyond 6 digits for Python's fromisoformat
    normalized = re.sub(r"\.(\d{6})\d+", r".\1", s.strip())
    normalized = normalized.replace("Z", "+00:00")
    try:
        dt_val = datetime.fromisoformat(normalized)
        if dt_val.tzinfo is None:
            dt_val = dt_val.astimezone()
        return dt_val
    except (ValueError, TypeError) as e:
        raise CorruptState(
            f"Saved login has an unparseable timestamp: {s!r}",
            hint="Sign in as that account in agy and run `mswap add`.",
        ) from e


def format_go_time(dt_val: datetime) -> str:
    """Format a datetime with microsecond precision and local offset per §A14."""
    return dt_val.astimezone().isoformat(timespec="microseconds")


def validate_blob(blob: bytes) -> dict[str, Any]:
    """Validate that a blob is valid JSON and has a non-empty refresh_token."""
    with contextlib.suppress(Exception):
        data = json.loads(blob)
        if isinstance(data, dict):
            token = data.get("token")
            if (
                isinstance(token, dict)
                and isinstance(token.get("refresh_token"), str)
                and token["refresh_token"].strip()
            ):
                return data
    raise CorruptState(
        "Saved login is damaged.",
        hint="Sign in as that account in agy and run `mswap add`.",
    )


def fingerprint(blob: bytes) -> str:
    """Identify an account copy by its refresh token, without storing the token."""
    data = validate_blob(blob)
    rt = data["token"]["refresh_token"]
    return hashlib.sha256(rt.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Fresh:
    """Result of an ensure_fresh operation."""

    access_token: str
    updated_blob: bytes | None

    def __iter__(self) -> Any:
        yield self.access_token
        yield self.updated_blob


class ClientRejected(Exception):
    """Internal exception raised when agy's OAuth client is rejected by Google."""


def ensure_fresh(
    blob: bytes,
    client: OAuthClient,
    http: Http,
    now: datetime,
) -> Fresh:
    """Return usable access token, refreshing if expiring within 120s."""
    data = validate_blob(blob)
    tok = data["token"]
    refresh_token = tok["refresh_token"]

    expiry_str = tok.get("expiry")
    is_fresh = False
    if expiry_str and isinstance(expiry_str, str):
        try:
            exp_dt = parse_go_time(expiry_str)
            now_cmp = now if now.tzinfo is not None else now.astimezone()
            exp_cmp = exp_dt if exp_dt.tzinfo is not None else exp_dt.astimezone()
            if exp_cmp - timedelta(seconds=120) > now_cmp:
                is_fresh = True
        except CorruptState:
            is_fresh = False

    if is_fresh:
        return Fresh(access_token=str(tok["access_token"]), updated_blob=None)

    resp = http.request(
        "POST",
        TOKEN_URL,
        form={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )

    if resp.status == 200:
        res_data = resp.json()
        if not isinstance(res_data, dict) or "access_token" not in res_data:
            raise ApiError(
                "OAuth token endpoint returned invalid response body.",
                status=resp.status,
                endpoint="oauth2/token",
            )
        new_access = str(res_data["access_token"])
        expires_in = int(res_data.get("expires_in", 3599))
        now_aware = now if now.tzinfo is not None else now.astimezone()
        new_expiry_dt = now_aware + timedelta(seconds=expires_in)

        tok["access_token"] = new_access
        tok["expiry"] = format_go_time(new_expiry_dt)

        updated_blob = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return Fresh(access_token=new_access, updated_blob=updated_blob)

    err_body: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        parsed = resp.json()
        if isinstance(parsed, dict):
            err_body = parsed

    error_code = str(err_body.get("error", ""))
    error_desc = str(err_body.get("error_description", ""))

    if resp.status == 400 and (error_code == "invalid_grant" or "invalid_grant" in error_desc):
        raise TokenDead(
            "This account's saved login has expired or was revoked.",
            hint="Sign in as it in agy, then run `mswap add`.",
        )

    if resp.status == 401 and (error_code == "invalid_client" or "invalid_client" in error_desc):
        raise ClientRejected()

    raise ApiError(
        f"OAuth token endpoint returned HTTP {resp.status}.",
        status=resp.status,
        endpoint="oauth2/token",
    )


def refresh(refresh_token: str, http: Http) -> dict[str, Any]:
    """Refresh an access token using discovered agy OAuth client details."""
    from mswap.agy.client_discovery import _config_file

    cfg_file = _config_file()
    client = discover(agy_exe(), cfg_file, http, sample_refresh_token=refresh_token)
    resp = http.request(
        "POST",
        TOKEN_URL,
        form={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    if resp.status == 200:
        return resp.json()  # type: ignore[no-any-return]
    if resp.status == 400:
        detail = ""
        with contextlib.suppress(Exception):
            err = resp.json()
            if isinstance(err, dict):
                detail = str(err.get("error_description", ""))
        err_msg = detail or resp.body.decode(errors="replace")[:300]
        raise MswapError(f"HTTP {resp.status}: {err_msg}")
    if resp.status == 401:
        raise MswapError("HTTP 401: Bad client secret")
    raise MswapError(f"HTTP {resp.status}: token refresh failed")


class TokenService:
    """Service for obtaining fresh access tokens and managing account quarantine."""

    def __init__(self, ctx: TokenContext) -> None:
        self.ctx = ctx

    def _client_cache_file(self) -> Path:
        root = Path(self.ctx.store.root)
        cf = root / "client.json"
        if not cf.exists():
            legacy = root / "config.json"
            if legacy.exists():
                return legacy
        return cf

    def _get_client(self, sample_refresh_token: str | None = None) -> OAuthClient:
        exe = agy_exe()
        cache_file = self._client_cache_file()
        return discover(exe, cache_file, self.ctx.http, sample_refresh_token)

    def _rediscover_client(self, sample_refresh_token: str | None = None) -> OAuthClient:
        exe = agy_exe()
        cache_file = self._client_cache_file()
        return rediscover(exe, cache_file, self.ctx.http, sample_refresh_token)

    def fresh_for_slot(self, account: Account) -> str:
        """Obtain a fresh access token for a saved account slot."""
        prefix = os.environ.get("MSWAP_VAULT_PREFIX", "mswap:")
        target = f"{prefix}slot{account.slot}"
        blob = self.ctx.vault.read(target)
        if blob is None:
            raise CorruptState(
                f"Saved login for account {account.slot} is missing.",
                hint="Sign in as it in agy and run `mswap add`.",
            )

        token_data = validate_blob(blob)
        sample_rt = token_data["token"]["refresh_token"]
        client = self._get_client(sample_rt)

        now = self.ctx.clock.now()
        try:
            fresh = ensure_fresh(blob, client, self.ctx.http, now)
        except ClientRejected:
            client = self._rediscover_client(sample_rt)
            try:
                fresh = ensure_fresh(blob, client, self.ctx.http, now)
            except ClientRejected as e:
                raise ApiError(
                    "agy's sign-in client changed and couldn't be re-detected.",
                    hint="Run `mswap doctor`.",
                ) from e
        except TokenDead:
            now_aware = now.replace(tzinfo=dt.UTC) if now.tzinfo is None else now.astimezone(dt.UTC)
            accounts = self.ctx.store.load()
            for idx, a in enumerate(accounts):
                if a.slot == account.slot:
                    accounts[idx] = dataclasses.replace(
                        a,
                        quarantined=Quarantine("invalid_grant", now_aware),
                    )
                    self.ctx.store.save(accounts)
                    break
            self.ctx.events.emit(
                "quarantine",
                slot=account.slot,
                email=account.email,
                reason="invalid_grant",
            )
            raise

        if fresh.updated_blob is not None:
            self.ctx.vault.write(target, fresh.updated_blob, account.email)

        if account.quarantined is not None:
            accounts = self.ctx.store.load()
            for idx, a in enumerate(accounts):
                if a.slot == account.slot:
                    accounts[idx] = dataclasses.replace(a, quarantined=None)
                    self.ctx.store.save(accounts)
                    break

        return fresh.access_token

    def fresh_for_live(self, blob: bytes) -> str:
        """Obtain a fresh access token for the live agy login in memory only."""
        token_data = validate_blob(blob)
        sample_rt = token_data["token"]["refresh_token"]
        client = self._get_client(sample_rt)

        now = self.ctx.clock.now()
        try:
            fresh = ensure_fresh(blob, client, self.ctx.http, now)
        except ClientRejected:
            client = self._rediscover_client(sample_rt)
            try:
                fresh = ensure_fresh(blob, client, self.ctx.http, now)
            except ClientRejected as e:
                raise ApiError(
                    "agy's sign-in client changed and couldn't be re-detected.",
                    hint="Run `mswap doctor`.",
                ) from e

        # In-memory only: NEVER write the live target!
        return fresh.access_token
