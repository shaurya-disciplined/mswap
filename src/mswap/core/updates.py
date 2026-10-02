"""Polite update check against the PyPI JSON API.

Owns PEP 440 comparison for N.N.N[aN|bN|rcN] versions, the 24 h cache in update.json,
and the decision of whether a newer release exists. Never installs or upgrades anything,
never raises on network or cache failures, and never asks the network more than once a day.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

from mswap.util.clock import Clock
from mswap.util.fsx import write_private_text
from mswap.util.http import Http

PYPI_URL = "https://pypi.org/pypi/mswap/json"
CACHE_TTL = timedelta(hours=24)
TIMEOUT_S = 3.0

_VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?$")
_PRE_RANK = {"a": 0, "b": 1, "rc": 2}
_FINAL_RANK = 3

VersionKey = tuple[tuple[int, ...], int, int]


def parse_version(text: str) -> VersionKey | None:
    """Parse N.N.N[aN|bN|rcN] into a sortable key, or None if the text is not that shape."""
    m = _VERSION_RE.match(text.strip())
    if m is None:
        return None
    release = [int(part) for part in m.group(1).split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    if m.group(2) is None:
        return (tuple(release), _FINAL_RANK, 0)
    return (tuple(release), _PRE_RANK[m.group(2)], int(m.group(3)))


def is_newer(latest: str, current: str) -> bool:
    """Return True when ``latest`` is strictly newer than ``current`` (False if unparseable)."""
    a = parse_version(latest)
    b = parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def update_check_enabled(env: Mapping[str, str], check_setting: bool) -> bool:
    """Return False when the user opted out or the network is disabled."""
    if env.get("MSWAP_NO_UPDATE_CHECK") == "1":
        return False
    if env.get("MSWAP_NO_NETWORK") == "1":
        return False
    return check_setting


def _read_cache(path: Path) -> tuple[datetime, str | None] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(str(data["checked_at"]))
        latest = data.get("latest")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if checked_at.tzinfo is None:
        return None
    return checked_at, latest if isinstance(latest, str) else None


def _write_cache(path: Path, checked_at: datetime, latest: str | None) -> None:
    payload = {"checked_at": checked_at.isoformat(), "latest": latest}
    with contextlib.suppress(OSError):
        write_private_text(path, json.dumps(payload))


def _fetch_latest(http: Http) -> str | None:
    try:
        resp = http.request("GET", PYPI_URL, timeout=TIMEOUT_S)
        if resp.status != 200:
            return None
        version = resp.json()["info"]["version"]
    except Exception:  # an update check must never break a command
        return None
    return version if isinstance(version, str) else None


def latest_version(http: Http, clock: Clock, cache_path: Path) -> str | None:
    """Return the latest published version, using the cache while it is under 24 h old.

    A failed lookup is cached too (keeping the last known version), so a flaky or
    offline network is retried at most once a day.
    """
    now = clock.now()
    cached = _read_cache(cache_path)
    if cached is not None and timedelta(0) <= now - cached[0] < CACHE_TTL:
        return cached[1]
    fetched = _fetch_latest(http)
    latest = fetched if fetched is not None else (cached[1] if cached else None)
    _write_cache(cache_path, now, latest)
    return latest


def update_available(http: Http, clock: Clock, cache_path: Path, current: str) -> str | None:
    """Return the newer version string when an update exists, else None."""
    latest = latest_version(http, clock, cache_path)
    if latest is not None and is_newer(latest, current):
        return latest
    return None
