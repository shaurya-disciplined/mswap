"""Unit tests for core/updates.py: version comparator, 24 h cache, failure handling."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mswap.core.updates import (
    LATEST_RELEASE_URL,
    is_newer,
    latest_version,
    parse_version,
    update_available,
    update_check_enabled,
    upgrade_command,
)
from mswap.util.clock import FrozenClock
from mswap.util.http import FakeHttp, HttpResponse, json_response

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _release(version: str) -> HttpResponse:
    return json_response({"tag_name": f"v{version}", "draft": False, "prerelease": False})


def _seed_cache(path: Path, *, age: timedelta, latest: str | None) -> None:
    path.write_text(
        json.dumps({"checked_at": (NOW - age).isoformat(), "latest": latest}), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("0.9.0rc1", "0.9.0"),
        ("0.9.0", "0.10.0"),
        ("0.9.0a1", "0.9.0b1"),
        ("0.9.0b2", "0.9.0rc1"),
        ("0.9.0rc1", "0.9.0rc2"),
        ("0.9.0rc2", "0.9.0rc10"),
        ("0.6.0", "0.6.1"),
        ("0.9.9", "1.0.0"),
        ("1.0", "1.0.1"),
        ("0.9.0rc1", "0.9.1a1"),
    ],
)
def test_is_newer_orders_versions(lower: str, higher: str) -> None:
    assert is_newer(higher, lower)
    assert not is_newer(lower, higher)


@pytest.mark.parametrize(("a", "b"), [("1.0", "1.0.0"), ("0.9.0rc1", "0.9.0rc1"), ("2.0.0", "2")])
def test_equal_versions_are_not_newer(a: str, b: str) -> None:
    assert not is_newer(a, b)
    assert not is_newer(b, a)


@pytest.mark.parametrize("bad", ["", "latest", "1.0.0.dev1", "1.0.post1", "v1.0.0", "1.0.0-rc1"])
def test_unparseable_versions_never_report_an_update(bad: str) -> None:
    assert parse_version(bad) is None
    assert not is_newer(bad, "0.1.0")
    assert not is_newer("9.9.9", bad)


def test_fetch_when_no_cache_writes_cache(tmp_path: Path, http: FakeHttp) -> None:
    cache = tmp_path / "update.json"
    http.add("GET", LATEST_RELEASE_URL, _release("0.9.0"))
    assert latest_version(http, FrozenClock(NOW), cache) == "0.9.0"
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved == {"checked_at": NOW.isoformat(), "latest": "0.9.0"}
    assert len(http.requests) == 1


def test_fresh_cache_makes_no_request(tmp_path: Path, http: FakeHttp) -> None:
    cache = tmp_path / "update.json"
    _seed_cache(cache, age=timedelta(hours=23, minutes=59), latest="0.8.0")
    assert latest_version(http, FrozenClock(NOW), cache) == "0.8.0"
    assert http.requests == []


def test_cache_exactly_24h_old_is_stale(tmp_path: Path, http: FakeHttp) -> None:
    cache = tmp_path / "update.json"
    _seed_cache(cache, age=timedelta(hours=24), latest="0.8.0")
    http.add("GET", LATEST_RELEASE_URL, _release("0.9.0"))
    assert latest_version(http, FrozenClock(NOW), cache) == "0.9.0"
    assert len(http.requests) == 1


def test_cache_from_the_future_is_stale(tmp_path: Path, http: FakeHttp) -> None:
    cache = tmp_path / "update.json"
    _seed_cache(cache, age=timedelta(hours=-3), latest="0.8.0")
    http.add("GET", LATEST_RELEASE_URL, _release("0.9.0"))
    assert latest_version(http, FrozenClock(NOW), cache) == "0.9.0"


@pytest.mark.parametrize("content", ["not json", "{}", '{"checked_at": "nope"}', "[]", ""])
def test_corrupt_cache_is_ignored(tmp_path: Path, http: FakeHttp, content: str) -> None:
    cache = tmp_path / "update.json"
    cache.write_text(content, encoding="utf-8")
    http.add("GET", LATEST_RELEASE_URL, _release("0.9.0"))
    assert latest_version(http, FrozenClock(NOW), cache) == "0.9.0"


def test_naive_cache_timestamp_is_ignored(tmp_path: Path, http: FakeHttp) -> None:
    cache = tmp_path / "update.json"
    cache.write_text(
        json.dumps({"checked_at": "2026-10-02T11:00:00", "latest": "0.1.0"}), encoding="utf-8"
    )
    http.add("GET", LATEST_RELEASE_URL, _release("0.9.0"))
    assert latest_version(http, FrozenClock(NOW), cache) == "0.9.0"


class _Offline:
    """HTTP test double that always fails and counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def request(self, *args: object, **kwargs: object) -> HttpResponse:
        self.calls += 1
        raise OSError("offline")


def test_network_failure_is_silent_and_cached_for_a_day(tmp_path: Path) -> None:
    cache = tmp_path / "update.json"
    clock = FrozenClock(NOW)
    offline = _Offline()
    assert latest_version(offline, clock, cache) is None  # type: ignore[arg-type]  # test double
    assert latest_version(offline, clock, cache) is None  # type: ignore[arg-type]  # test double
    assert offline.calls == 1


def test_failed_refresh_keeps_last_known_version(tmp_path: Path, http: FakeHttp) -> None:
    cache = tmp_path / "update.json"
    _seed_cache(cache, age=timedelta(days=3), latest="0.8.0")
    http.add("GET", LATEST_RELEASE_URL, HttpResponse(status=503, body=b""))
    assert latest_version(http, FrozenClock(NOW), cache) == "0.8.0"
    assert json.loads(cache.read_text(encoding="utf-8"))["checked_at"] == NOW.isoformat()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        b'{"tag_name": 7}',
        b"[]",
        b'{"tag_name": null}',
        b'{"tag_name": "1.0.0"}',
    ],
)
def test_malformed_release_payload_is_silent(tmp_path: Path, http: FakeHttp, body: bytes) -> None:
    http.add("GET", LATEST_RELEASE_URL, HttpResponse(status=200, body=body))
    assert latest_version(http, FrozenClock(NOW), tmp_path / "update.json") is None


def test_unwritable_cache_location_is_silent(tmp_path: Path, http: FakeHttp) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    http.add("GET", LATEST_RELEASE_URL, _release("0.9.0"))
    assert latest_version(http, FrozenClock(NOW), blocker / "update.json") == "0.9.0"


def test_request_uses_three_second_timeout(tmp_path: Path) -> None:
    seen: list[float] = []

    class Spy:
        def request(self, method: str, url: str, *, timeout: float = 20.0) -> HttpResponse:
            seen.append(timeout)
            return _release("0.9.0")

    latest_version(Spy(), FrozenClock(NOW), tmp_path / "update.json")  # type: ignore[arg-type]  # test double
    assert seen == [3.0]


def test_update_available(tmp_path: Path, http: FakeHttp) -> None:
    cache = tmp_path / "update.json"
    http.add("GET", LATEST_RELEASE_URL, _release("0.9.0"))
    clock = FrozenClock(NOW)
    assert update_available(http, clock, cache, "0.6.0") == "0.9.0"
    assert update_available(http, clock, cache, "0.9.0") is None
    assert update_available(http, clock, cache, "1.0.0") is None
    assert update_available(http, clock, cache, "0.9.0rc1") == "0.9.0"


@pytest.mark.parametrize(
    ("env", "setting", "expected"),
    [
        ({}, True, True),
        ({}, False, False),
        ({"MSWAP_NO_UPDATE_CHECK": "1"}, True, False),
        ({"MSWAP_NO_NETWORK": "1"}, True, False),
        ({"MSWAP_NO_UPDATE_CHECK": "0"}, True, True),
    ],
)
def test_update_check_enabled(env: dict[str, str], setting: bool, expected: bool) -> None:
    assert update_check_enabled(env, setting) is expected


def test_upgrade_command_installs_the_release_tag() -> None:
    assert upgrade_command("1.2.0") == (
        "uv tool install --force git+https://github.com/shaurya-disciplined/mswap@v1.2.0"
    )
