"""Golden integration tests comparing render_list output across terminal modes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mswap.core.models import Account, Bucket, Pool, Quarantine, QuotaSnapshot
from mswap.core.usage_cache import CacheEntry
from mswap.ui.render import AccountRow, render_list
from mswap.ui.theme import Theme

FIXED_NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def _check_golden(name: str, lines: list[str], update: bool) -> None:
    golden_dir = Path(__file__).resolve().parent.parent / "golden"
    golden_dir.mkdir(parents=True, exist_ok=True)
    golden_path = golden_dir / f"{name}.txt"
    actual_text = "\n".join(lines) + "\n"

    if update:
        golden_path.write_text(actual_text, encoding="utf-8")
    else:
        assert golden_path.exists(), (
            f"Golden file {golden_path} does not exist. Run with --update-golden."
        )
        expected_text = golden_path.read_text(encoding="utf-8")
        assert actual_text == expected_text


def _make_healthy_account() -> AccountRow:
    acc = Account(
        slot=1,
        email="alice@example.com",
        fp="fp_alice_01",
        added_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        plan="g1-pro-tier",
    )
    snap = QuotaSnapshot(
        fetched_at=FIXED_NOW,
        pools=(
            Pool(
                key="gemini",
                name="Gemini",
                buckets=(
                    Bucket(
                        window="5h",
                        remaining=0.99,
                        reset_at=FIXED_NOW + timedelta(hours=4, minutes=25),
                    ),
                    Bucket(
                        window="weekly",
                        remaining=0.99,
                        reset_at=FIXED_NOW + timedelta(days=5, hours=16),
                    ),
                ),
            ),
            Pool(
                key="3p",
                name="Claude & GPT",
                buckets=(
                    Bucket(window="5h", remaining=1.0, reset_at=None),
                    Bucket(window="weekly", remaining=1.0, reset_at=None),
                ),
            ),
        ),
    )
    entry = CacheEntry(
        fetched_at=FIXED_NOW,
        snapshot=snap,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )
    return AccountRow(account=acc, active=True, entry=entry, stale=False)


def _make_disabled_account() -> AccountRow:
    acc = Account(
        slot=2,
        email="spare2@example.com",
        fp="fp_spare2_02",
        added_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        alias="work",
        disabled=True,
    )
    entry = CacheEntry(
        fetched_at=FIXED_NOW,
        snapshot=None,
        error={
            "kind": "invalid_grant",
            "message": "token expired and refresh failed: invalid_grant",
            "hint": "sign in as it in agy, then `mswap add`",
        },
        backoff_until=None,
        backoff_seconds=None,
    )
    return AccountRow(account=acc, active=False, entry=entry, stale=False)


def _make_quarantined_stale_account() -> AccountRow:
    acc = Account(
        slot=3,
        email="bob@example.com",
        fp="fp_bob_03",
        added_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        quarantined=Quarantine(reason="invalid_grant", at=FIXED_NOW),
    )
    entry = CacheEntry(
        fetched_at=FIXED_NOW - timedelta(minutes=6),
        snapshot=None,
        error={
            "kind": "quarantined",
            "message": "saved login expired or revoked  → sign in as it in agy, then `mswap add`",
        },
        backoff_until=None,
        backoff_seconds=None,
    )
    return AccountRow(account=acc, active=False, entry=entry, stale=True)


def test_golden_single_healthy_active(pytestconfig: pytest.Config) -> None:
    update = bool(pytestconfig.getoption("--update-golden", False))
    theme = Theme(color=True, ascii=False)
    rows = [_make_healthy_account()]

    lines = render_list(rows, now=FIXED_NOW, theme=theme, width=80, tz=UTC)
    _check_golden("list_healthy_active", lines, update)


def test_golden_three_accounts(pytestconfig: pytest.Config) -> None:
    update = bool(pytestconfig.getoption("--update-golden", False))
    theme = Theme(color=True, ascii=False)
    rows = [
        _make_healthy_account(),
        _make_disabled_account(),
        _make_quarantined_stale_account(),
    ]

    lines = render_list(rows, now=FIXED_NOW, theme=theme, width=80, tz=UTC)
    _check_golden("list_three_accounts", lines, update)


def test_golden_exhausted_pools(pytestconfig: pytest.Config) -> None:
    update = bool(pytestconfig.getoption("--update-golden", False))
    theme = Theme(color=True, ascii=False)

    acc = Account(
        slot=1,
        email="alice@example.com",
        fp="fp_alice_01",
        added_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        plan="free-tier",
    )
    snap = QuotaSnapshot(
        fetched_at=FIXED_NOW,
        pools=(
            Pool(
                key="gemini",
                name="Gemini",
                buckets=(
                    Bucket(
                        window="5h",
                        remaining=0.0,
                        reset_at=FIXED_NOW + timedelta(hours=2),
                    ),
                    Bucket(
                        window="weekly",
                        remaining=0.0,
                        reset_at=FIXED_NOW + timedelta(days=3),
                    ),
                ),
            ),
            Pool(
                key="3p",
                name="Claude & GPT",
                buckets=(
                    Bucket(
                        window="5h",
                        remaining=0.0,
                        reset_at=FIXED_NOW + timedelta(hours=1),
                    ),
                    Bucket(
                        window="weekly",
                        remaining=0.0,
                        reset_at=FIXED_NOW + timedelta(days=2),
                    ),
                ),
            ),
        ),
    )
    entry = CacheEntry(
        fetched_at=FIXED_NOW,
        snapshot=snap,
        error=None,
        backoff_until=None,
        backoff_seconds=None,
    )
    rows = [AccountRow(account=acc, active=True, entry=entry, stale=False)]

    lines = render_list(rows, now=FIXED_NOW, theme=theme, width=80, tz=UTC)
    _check_golden("list_exhausted", lines, update)


def test_golden_ascii_mode(pytestconfig: pytest.Config) -> None:
    update = bool(pytestconfig.getoption("--update-golden", False))
    theme = Theme(color=False, ascii=True)
    rows = [
        _make_healthy_account(),
        _make_disabled_account(),
        _make_quarantined_stale_account(),
    ]

    lines = render_list(rows, now=FIXED_NOW, theme=theme, width=80, tz=UTC)
    _check_golden("list_ascii", lines, update)


def test_golden_narrow_width(pytestconfig: pytest.Config) -> None:
    update = bool(pytestconfig.getoption("--update-golden", False))
    theme = Theme(color=True, ascii=False)
    rows = [
        _make_healthy_account(),
        _make_disabled_account(),
        _make_quarantined_stale_account(),
    ]

    lines = render_list(rows, now=FIXED_NOW, theme=theme, width=60, tz=UTC)
    _check_golden("list_narrow", lines, update)


def test_golden_no_color(pytestconfig: pytest.Config) -> None:
    update = bool(pytestconfig.getoption("--update-golden", False))
    theme = Theme(color=False, ascii=False)
    rows = [
        _make_healthy_account(),
        _make_disabled_account(),
        _make_quarantined_stale_account(),
    ]

    lines = render_list(rows, now=FIXED_NOW, theme=theme, width=80, tz=UTC)
    actual_text = "\n".join(lines)
    assert "\033[" not in actual_text, "NO_COLOR output must contain no ANSI escape sequences"
    _check_golden("list_no_color", lines, update)
