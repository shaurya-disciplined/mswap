"""Unit tests for core/store module."""

from __future__ import annotations

from pathlib import Path

import pytest

from mswap.core.store import (
    backup_last,
    backup_original,
    data_dir,
    find_active,
    live_target,
    load_accounts,
    save_accounts,
    slot_target,
    vault_prefix,
)
from tests.conftest import make_blob


def test_data_dir_default_and_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MSWAP_HOME", raising=False)
    assert data_dir() == Path.home() / ".mswap"

    custom = tmp_path / "custom_home"
    monkeypatch.setenv("MSWAP_HOME", str(custom))
    assert data_dir() == custom


def test_live_target_safety_assertion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "gemini:antigravity")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/unit/test_store.py::test_live_target")
    with pytest.raises(AssertionError, match="Safety net triggered"):
        live_target()


def test_targets_and_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_VAULT_PREFIX", "testprefix:")
    assert vault_prefix() == "testprefix:"
    assert slot_target(1) == "testprefix:slot1"
    assert backup_last() == "testprefix:backup-last"
    assert backup_original() == "testprefix:backup-original"


def test_load_accounts_empty_when_missing() -> None:
    assert load_accounts() == []


def test_save_and_load_accounts() -> None:
    accounts = [
        {"slot": 2, "email": "bob@example.com", "fp": "fp2"},
        {"slot": 1, "email": "alice@example.com", "fp": "fp1"},
    ]
    save_accounts(accounts)

    loaded = load_accounts()
    assert len(loaded) == 2
    assert loaded[0]["slot"] == 1
    assert loaded[1]["slot"] == 2


def test_find_active() -> None:
    blob1 = make_blob(1)
    accounts = [
        {"slot": 1, "email": "alice@example.com", "fp": "wrong_fp"},
        {"slot": 2, "email": "bob@example.com", "fp": "other_fp"},
    ]
    assert find_active(accounts, None) is None
    assert find_active(accounts, blob1) is None

    # Matching blob
    blob2 = make_blob(2)
    from mswap.agy.tokens import fingerprint

    accounts[1]["fp"] = fingerprint(blob2)
    active = find_active(accounts, blob2)
    assert active is not None
    assert active["slot"] == 2
