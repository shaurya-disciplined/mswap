"""Usage refresh orchestration, parallel fetching, and cache integration."""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from mswap.agy.api import AgyApi
from mswap.agy.install import agy_version, os_arch, user_agent
from mswap.agy.paths import agy_exe
from mswap.agy.tokens import TokenService
from mswap.core.errors import ApiError, MswapError, TokenDead
from mswap.core.models import Account
from mswap.core.poll_policy import is_stale
from mswap.core.store import AccountStore, find_active, live_target, slot_target
from mswap.core.usage_cache import CacheEntry, UsageCache
from mswap.util.clock import Clock
from mswap.util.http import Http
from mswap.vault.base import Vault


class UsageContext(Protocol):
    """Protocol defining dependencies needed for usage fetching."""

    @property
    def vault(self) -> Vault: ...

    @property
    def http(self) -> Http: ...

    @property
    def clock(self) -> Clock: ...

    @property
    def store(self) -> AccountStore: ...

    @property
    def events(self) -> Any: ...


def refresh_usage(
    ctx: UsageContext,
    accounts: Sequence[Account],
    *,
    force: bool = False,
) -> dict[int, CacheEntry]:
    """Refresh quota usage for accounts in parallel using UsageCache and adaptive polling.

    Respects active backoff even when force=True.
    Preserves input order of accounts in the returned mapping.
    """
    if not accounts:
        return {}

    cache_path = ctx.store.root / "usage.json"
    cache = UsageCache(cache_path)
    cache.prune([a.fp for a in accounts])

    live = ctx.vault.read(live_target())
    active: Account | None = find_active(accounts, live)

    client_cache_file = ctx.store.root / "client.json"
    if not client_cache_file.exists():
        legacy_cfg = ctx.store.root / "config.json"
        if legacy_cfg.exists():
            client_cache_file = legacy_cfg
    client_cache: dict[str, Any] = {}
    if client_cache_file.exists():
        with contextlib.suppress(Exception):
            client_cache = json.loads(client_cache_file.read_text(encoding="utf-8"))

    version = agy_version(agy_exe(), client_cache)
    ua = user_agent(version, os_arch())
    api = AgyApi(ctx.http, ua, clock=ctx.clock)
    token_service = TokenService(ctx)

    vault_lock = threading.Lock()

    def fetch_one(acc: Account) -> tuple[int, CacheEntry]:
        now = ctx.clock.now()
        is_active = active is not None and acc.slot == active.slot
        cached = cache.get(acc.fp)

        # Quarantined accounts: never fetched (entry error kind "quarantined")
        if acc.quarantined is not None:
            err_dict = {
                "kind": "quarantined",
                "message": (
                    "saved login expired or revoked  → sign in as it in agy, then `mswap add`"
                ),
                "at": now.isoformat(),
            }
            entry = CacheEntry(
                fetched_at=cached.fetched_at if (cached and cached.snapshot) else now,
                snapshot=cached.snapshot if cached else None,
                error=err_dict,
                backoff_until=None,
                backoff_seconds=None,
            )
            return acc.slot, entry

        # In backoff? Backoff is strictly respected even with force=True (--refresh).
        in_backoff = (
            cached is not None and cached.backoff_until is not None and now < cached.backoff_until
        )
        if in_backoff:
            assert cached is not None
            return acc.slot, cached

        # Check staleness
        stale = is_stale(cached, now, active=is_active)
        if not force and not stale:
            assert cached is not None
            return acc.slot, cached

        # Network fetch required
        with vault_lock:
            blob = live if is_active else ctx.vault.read(slot_target(acc.slot))
        if not blob:
            err_msg = "saved login missing (run `mswap add` while signed in as it)"
            cache.put_error(acc.fp, "missing_blob", err_msg, now)
            updated = cache.get(acc.fp)
            if updated is not None:
                return acc.slot, updated
            return acc.slot, CacheEntry(
                fetched_at=now,
                snapshot=cached.snapshot if cached else None,
                error={"kind": "missing_blob", "message": err_msg, "at": now.isoformat()},
                backoff_until=None,
                backoff_seconds=None,
            )

        try:
            target = blob if is_active else acc
            snapshot = token_service.call_with_retry(target, api.quota_summary)
            cache.put_snapshot(acc.fp, snapshot, now)
            updated = cache.get(acc.fp)
            if updated is not None:
                return acc.slot, updated
            return acc.slot, CacheEntry(
                fetched_at=now,
                snapshot=snapshot,
                error=None,
                backoff_until=None,
                backoff_seconds=None,
            )
        except TokenDead:
            err_msg = "saved login expired or revoked  → sign in as it in agy, then `mswap add`"
            cache.put_error(acc.fp, "invalid_grant", err_msg, now)
            updated = cache.get(acc.fp)
            if updated is not None:
                return acc.slot, updated
            return acc.slot, CacheEntry(
                fetched_at=cached.fetched_at if (cached and cached.snapshot) else now,
                snapshot=cached.snapshot if cached else None,
                error={"kind": "invalid_grant", "message": err_msg, "at": now.isoformat()},
                backoff_until=None,
                backoff_seconds=None,
            )
        except ApiError as e:
            kind = getattr(e, "kind", "api_error")
            err_msg = getattr(e, "message", str(e))
            cache.put_error(acc.fp, kind, err_msg, now)
            updated = cache.get(acc.fp)
            if updated is not None:
                return acc.slot, updated
            return acc.slot, CacheEntry(
                fetched_at=cached.fetched_at if (cached and cached.snapshot) else now,
                snapshot=cached.snapshot if cached else None,
                error={"kind": kind, "message": err_msg, "at": now.isoformat()},
                backoff_until=None,
                backoff_seconds=None,
            )
        except MswapError as e:
            kind = getattr(e, "kind", "mswap_error")
            err_msg = str(e)
            cache.put_error(acc.fp, kind, err_msg, now)
            updated = cache.get(acc.fp)
            if updated is not None:
                return acc.slot, updated
            return acc.slot, CacheEntry(
                fetched_at=cached.fetched_at if (cached and cached.snapshot) else now,
                snapshot=cached.snapshot if cached else None,
                error={"kind": kind, "message": err_msg, "at": now.isoformat()},
                backoff_until=None,
                backoff_seconds=None,
            )

    max_workers = min(6, len(accounts))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        mapped = list(pool.map(fetch_one, accounts))

    return {slot: entry for slot, entry in mapped}
