"""Usage cache storage, serialization, and atomic locking for quota snapshots."""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mswap.agy.api import AgyApi
from mswap.core.errors import LockTimeout
from mswap.core.locking import FileLock
from mswap.core.models import QuotaSnapshot
from mswap.core.poll_policy import next_backoff


@dataclass(slots=True)
class CacheEntry:
    """Cached quota snapshot, error state, and rate-limit backoff for an account."""

    fetched_at: datetime
    snapshot: QuotaSnapshot | None
    error: dict[str, str] | None
    backoff_until: datetime | None
    backoff_seconds: int | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize entry to dictionary for usage.json."""
        return {
            "fetched_at": self.fetched_at.isoformat(),
            "snapshot": self.snapshot.to_json() if self.snapshot is not None else None,
            "error": self.error,
            "backoff_until": (
                self.backoff_until.isoformat() if self.backoff_until is not None else None
            ),
            "backoff_seconds": self.backoff_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CacheEntry:
        """Deserialize entry from dictionary in usage.json."""
        raw_fetched = d.get("fetched_at")
        if isinstance(raw_fetched, datetime):
            fetched_at = raw_fetched
        elif raw_fetched:
            fetched_at = datetime.fromisoformat(str(raw_fetched))
        else:
            fetched_at = datetime.now(UTC)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)

        raw_snap = d.get("snapshot")
        snapshot: QuotaSnapshot | None = None
        if isinstance(raw_snap, dict):
            snapshot = QuotaSnapshot.from_json(raw_snap)
        elif isinstance(raw_snap, QuotaSnapshot):
            snapshot = raw_snap
        elif "groups" in d and isinstance(d["groups"], list):
            # Compatibility with demo seed groups shape
            snapshot = AgyApi._parse_summary({"groups": d["groups"]}, fetched_at)

        raw_err = d.get("error")
        error: dict[str, str] | None = None
        if isinstance(raw_err, dict):
            error = {str(k): str(v) for k, v in raw_err.items()}
        elif isinstance(raw_err, str):
            error = {"kind": "error", "message": raw_err, "at": fetched_at.isoformat()}

        raw_backoff_until = d.get("backoff_until")
        backoff_until: datetime | None = None
        if isinstance(raw_backoff_until, datetime):
            backoff_until = raw_backoff_until
        elif raw_backoff_until:
            backoff_until = datetime.fromisoformat(str(raw_backoff_until))
        if backoff_until is not None and backoff_until.tzinfo is None:
            backoff_until = backoff_until.replace(tzinfo=UTC)

        raw_backoff_sec = d.get("backoff_seconds")
        backoff_seconds = int(raw_backoff_sec) if raw_backoff_sec is not None else None

        return cls(
            fetched_at=fetched_at,
            snapshot=snapshot,
            error=error,
            backoff_until=backoff_until,
            backoff_seconds=backoff_seconds,
        )


class UsageCache:
    """Thread-safe and process-safe cache for account quota snapshots."""

    def __init__(self, path: Path | str, *, lock_timeout: float = 2.0) -> None:
        self.path = Path(path)
        self.lock_path = self.path.parent / "usage.lock"
        self.lock_timeout = lock_timeout
        self._thread_lock = threading.Lock()

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": 1, "accounts": {}}
        with contextlib.suppress(Exception):
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "accounts" in raw and isinstance(raw["accounts"], dict):
                return raw
        return {"schema": 1, "accounts": {}}

    def _write_raw(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(self.path)
        except OSError:
            with contextlib.suppress(OSError):
                if tmp_path.exists():
                    tmp_path.unlink()

    def get(self, fp: str) -> CacheEntry | None:
        """Retrieve cached entry for an account fingerprint, or None if not found."""
        with self._thread_lock:
            data = self._read_raw()
            accounts = data.get("accounts", {})
            if fp in accounts and isinstance(accounts[fp], dict):
                return CacheEntry.from_dict(accounts[fp])
            return None

    def put_snapshot(self, fp: str, snap: QuotaSnapshot, now: datetime) -> None:
        """Save a new successful quota snapshot, clearing any previous error and backoff."""
        entry = CacheEntry(
            fetched_at=now,
            snapshot=snap,
            error=None,
            backoff_until=None,
            backoff_seconds=None,
        )
        with self._thread_lock:
            try:
                with FileLock(self.lock_path, timeout=self.lock_timeout):
                    data = self._read_raw()
                    accounts = data.setdefault("accounts", {})
                    accounts[fp] = entry.to_dict()
                    self._write_raw(data)
            except LockTimeout:
                return

    def put_error(self, fp: str, kind: str, message: str, now: datetime) -> None:
        """Record an error for an account.

        Updates backoff if rate-limited and preserves older snapshots.
        """
        with self._thread_lock:
            try:
                with FileLock(self.lock_path, timeout=self.lock_timeout):
                    data = self._read_raw()
                    accounts = data.setdefault("accounts", {})
                    prev: CacheEntry | None = None
                    if fp in accounts and isinstance(accounts[fp], dict):
                        prev = CacheEntry.from_dict(accounts[fp])

                    if kind == "rate_limited":
                        prev_backoff = prev.backoff_seconds if prev else None
                        backoff_seconds = next_backoff(prev_backoff)
                        backoff_until = now + timedelta(seconds=backoff_seconds)
                        retry_time = backoff_until.astimezone().strftime("%H:%M")
                        err_msg = f"rate-limited, retrying after {retry_time}"
                    else:
                        backoff_seconds = None
                        backoff_until = None
                        err_msg = message

                    error_dict = {
                        "kind": kind,
                        "message": err_msg,
                        "at": now.isoformat(),
                    }
                    snapshot = prev.snapshot if prev else None
                    fetched_at = prev.fetched_at if (prev and prev.snapshot) else now

                    entry = CacheEntry(
                        fetched_at=fetched_at,
                        snapshot=snapshot,
                        error=error_dict,
                        backoff_until=backoff_until,
                        backoff_seconds=backoff_seconds,
                    )
                    accounts[fp] = entry.to_dict()
                    self._write_raw(data)
            except LockTimeout:
                return

    def prune(self, fps_to_keep: Sequence[str] | set[str]) -> None:
        """Prune any cached entries for accounts that no longer exist."""
        keep_set = set(fps_to_keep)
        with self._thread_lock:
            try:
                with FileLock(self.lock_path, timeout=self.lock_timeout):
                    data = self._read_raw()
                    accounts = data.setdefault("accounts", {})
                    to_remove = [fp for fp in accounts if fp not in keep_set]
                    if not to_remove:
                        return
                    for fp in to_remove:
                        del accounts[fp]
                    self._write_raw(data)
            except LockTimeout:
                return
