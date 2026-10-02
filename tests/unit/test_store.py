"""Unit tests for core/store module and v1 -> v2 schema migration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from mswap.core.errors import CorruptState
from mswap.core.models import Account
from mswap.core.store import (
    AccountStore,
    backup_last,
    backup_original,
    data_dir,
    find_active,
    legacy_dir,
    live_target,
    load_accounts,
    save_accounts,
    slot_target,
    vault_prefix,
)
from tests.conftest import make_blob


def test_data_dir_default_and_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Override via MSWAP_HOME
    custom = tmp_path / "custom_home"
    monkeypatch.setenv("MSWAP_HOME", str(custom))
    assert data_dir() == custom
    assert not custom.exists()  # mkdir on first write only

    # Windows fallback
    monkeypatch.delenv("MSWAP_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    expected_win = tmp_path / "localappdata" / "mswap"
    assert data_dir() == expected_win
    assert not expected_win.exists()

    # macOS fallback
    monkeypatch.setattr("sys.platform", "darwin")
    expected_mac = Path.home() / "Library" / "Application Support" / "mswap"
    assert data_dir() == expected_mac

    # Linux fallback
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert data_dir() == tmp_path / "xdg" / "mswap"


def test_legacy_dir_default_and_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    legacy_custom = tmp_path / "custom_legacy"
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy_custom))
    assert legacy_dir() == legacy_custom

    monkeypatch.delenv("MSWAP_LEGACY_HOME", raising=False)
    assert legacy_dir() == Path.home() / ".mswap"


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


def test_store_load_empty_when_missing(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "empty_store")
    assert store.load() == []


def test_store_save_and_load_schema_2(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = AccountStore(root)

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    accounts = [
        Account(slot=2, email="bob@example.com", fp="fp2", added_at=now, updated_at=now),
        Account(slot=1, email="alice@example.com", fp="fp1", added_at=now, updated_at=now),
    ]
    store.save(accounts)

    accounts_file = root / "accounts.json"
    assert accounts_file.exists()
    raw = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert raw["schema"] == 2
    assert len(raw["accounts"]) == 2
    # Saved sorted by slot
    assert raw["accounts"][0]["slot"] == 1
    assert raw["accounts"][1]["slot"] == 2

    loaded = store.load()
    assert len(loaded) == 2
    assert loaded[0].slot == 1
    assert loaded[0].email == "alice@example.com"
    assert loaded[1].slot == 2
    assert loaded[1].email == "bob@example.com"


def test_store_unknown_field_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = AccountStore(root)

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    acc = Account(
        slot=1,
        email="alice@example.com",
        fp="fp1",
        added_at=now,
        updated_at=now,
        extra={"color": "red"},
    )
    store.save([acc])

    # Verify on disk
    raw = json.loads((root / "accounts.json").read_text(encoding="utf-8"))
    assert raw["accounts"][0]["color"] == "red"

    # Verify loaded
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].extra.get("color") == "red"


def test_store_atomic_save(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = AccountStore(root)

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    initial_acc = Account(slot=1, email="alice@example.com", fp="fp1", added_at=now, updated_at=now)
    store.save([initial_acc])

    target_file = root / "accounts.json"
    original_content = target_file.read_text(encoding="utf-8")

    # Simulate atomic replace raising
    new_acc = Account(slot=2, email="bob@example.com", fp="fp2", added_at=now, updated_at=now)
    with (
        patch.object(Path, "replace", side_effect=OSError("simulated disk crash")),
        pytest.raises(OSError, match="simulated disk crash"),
    ):
        store.save([new_acc])

    # Assert original file is intact
    assert target_file.read_text(encoding="utf-8") == original_content


def test_store_load_schema_greater_than_2_raises_corrupt_state(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir(parents=True, exist_ok=True)
    accounts_file = root / "accounts.json"
    accounts_file.write_text(
        json.dumps({"schema": 3, "accounts": []}),
        encoding="utf-8",
    )

    store = AccountStore(root)
    with pytest.raises(CorruptState) as exc_info:
        store.load()
    assert exc_info.value.message == "This data was written by a newer mswap."
    assert exc_info.value.hint == "Upgrade mswap."


def test_store_load_corrupt_root_json_raises_corrupt_state(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir(parents=True, exist_ok=True)
    accounts_file = root / "accounts.json"
    accounts_file.write_text("{not valid json", encoding="utf-8")

    store = AccountStore(root)
    with pytest.raises(CorruptState):
        store.load()


def test_v1_to_v2_migration_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    # Meteor's exact v1 accounts shape
    v1_accounts = {
        "accounts": [
            {
                "slot": 1,
                "email": "alice@example.com",
                "fp": "5c48cb6c4dc2e6e1",
                "added_at": "2026-10-02T01:23:28",
            }
        ]
    }
    legacy_accounts_file = legacy / "accounts.json"
    legacy_accounts_file.write_text(json.dumps(v1_accounts), encoding="utf-8")

    # Legacy config.json
    legacy_config_file = legacy / "config.json"
    legacy_config_file.write_text(
        json.dumps({"client_id": "test-client-id", "version": "1.2.12"}),
        encoding="utf-8",
    )

    root = tmp_path / "root"
    store = AccountStore(root)

    # Act
    migrated = store.migrate_if_needed()
    assert migrated is True

    # Assert root/accounts.json exists and is schema 2
    root_accounts_file = root / "accounts.json"
    assert root_accounts_file.exists()
    root_data = json.loads(root_accounts_file.read_text(encoding="utf-8"))
    assert root_data["schema"] == 2
    assert len(root_data["accounts"]) == 1

    acc_data = root_data["accounts"][0]
    assert acc_data["slot"] == 1
    assert acc_data["email"] == "alice@example.com"
    assert acc_data["fp"] == "5c48cb6c4dc2e6e1"
    # added_at without offset was treated as local time and stored with an offset
    assert "+" in acc_data["added_at"] or "-" in acc_data["added_at"] or "Z" in acc_data["added_at"]
    assert acc_data["updated_at"] == acc_data["added_at"]
    assert acc_data["alias"] is None
    assert acc_data["disabled"] is False
    assert acc_data["quarantined"] is None
    assert acc_data["plan"] is None

    # Legacy accounts.json renamed to accounts.v1.bak (never deleted)
    assert not legacy_accounts_file.exists()
    assert (legacy / "accounts.v1.bak").exists()

    # Legacy config.json copied to root/client.json and renamed to config.v1.bak
    root_client_file = root / "client.json"
    assert root_client_file.exists()
    client_data = json.loads(root_client_file.read_text(encoding="utf-8"))
    assert client_data["client_id"] == "test-client-id"
    assert not legacy_config_file.exists()
    assert (legacy / "config.v1.bak").exists()

    # Running again does nothing (returns False)
    assert store.migrate_if_needed() is False


def test_v1_migration_corrupt_legacy_raises_corrupt_state_and_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    legacy_accounts_file = legacy / "accounts.json"
    bad_bytes = b'{\n  "accounts": [corrupt json here\n'
    legacy_accounts_file.write_bytes(bad_bytes)

    root = tmp_path / "root"
    store = AccountStore(root)

    with pytest.raises(CorruptState) as exc_info:
        store.migrate_if_needed()

    assert f"Couldn't read the old mswap data at {legacy_accounts_file}." in exc_info.value.message
    assert exc_info.value.hint == "Fix or move that file, then run mswap again."

    # Legacy file untouched (bytes identical)
    assert legacy_accounts_file.read_bytes() == bad_bytes
    assert not (legacy / "accounts.v1.bak").exists()
    assert not (root / "accounts.json").exists()


def test_v1_migration_invalid_structure_raises_corrupt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    legacy_accounts_file = legacy / "accounts.json"
    legacy_accounts_file.write_text(json.dumps(["not a dict"]), encoding="utf-8")

    root = tmp_path / "root"
    store = AccountStore(root)

    with pytest.raises(CorruptState):
        store.migrate_if_needed()


def test_v1_migration_skipped_when_root_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    legacy_accounts_file = legacy / "accounts.json"
    legacy_accounts_file.write_text(json.dumps({"accounts": []}), encoding="utf-8")

    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    (root / "accounts.json").write_text(json.dumps({"schema": 2, "accounts": []}), encoding="utf-8")

    store = AccountStore(root)
    assert store.migrate_if_needed() is False
    assert legacy_accounts_file.exists()


def test_v1_migration_skipped_when_schema_present_in_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    legacy_accounts_file = legacy / "accounts.json"
    legacy_accounts_file.write_text(json.dumps({"schema": 2, "accounts": []}), encoding="utf-8")

    root = tmp_path / "root"
    store = AccountStore(root)
    assert store.migrate_if_needed() is False


def test_v1_migration_without_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    v1_accounts = {
        "accounts": [
            {
                "slot": 1,
                "email": "alice@example.com",
                "fp": "5c48cb6c4dc2e6e1",
                "added_at": "2026-10-02T01:23:28",
            }
        ]
    }
    (legacy / "accounts.json").write_text(json.dumps(v1_accounts), encoding="utf-8")

    root = tmp_path / "root"
    store = AccountStore(root)
    assert store.migrate_if_needed() is True
    assert (root / "accounts.json").exists()
    assert not (root / "client.json").exists()


def test_v1_migration_item_not_dict_raises_corrupt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    (legacy / "accounts.json").write_text(
        json.dumps({"accounts": ["not a dict"]}),
        encoding="utf-8",
    )
    store = AccountStore(tmp_path / "root")
    with pytest.raises(CorruptState):
        store.migrate_if_needed()


def test_v1_migration_missing_or_invalid_added_at_raises_corrupt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    # Missing added_at
    (legacy / "accounts.json").write_text(
        json.dumps({"accounts": [{"slot": 1, "email": "alice@example.com", "fp": "fp"}]}),
        encoding="utf-8",
    )
    store = AccountStore(tmp_path / "root")
    with pytest.raises(CorruptState):
        store.migrate_if_needed()

    # Invalid datetime
    (legacy / "accounts.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "slot": 1,
                        "email": "alice@example.com",
                        "fp": "fp",
                        "added_at": "not-a-date",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorruptState):
        store.migrate_if_needed()


def test_v1_migration_preserves_unknown_account_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MSWAP_LEGACY_HOME", str(legacy))

    v1_accounts = {
        "accounts": [
            {
                "slot": 1,
                "email": "alice@example.com",
                "fp": "5c48cb6c4dc2e6e1",
                "added_at": "2026-10-02T01:23:28+00:00",
                "theme": "dark",
                "custom_id": 99,
            }
        ]
    }
    (legacy / "accounts.json").write_text(json.dumps(v1_accounts), encoding="utf-8")

    root = tmp_path / "root"
    store = AccountStore(root)
    assert store.migrate_if_needed() is True

    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].extra.get("theme") == "dark"
    assert loaded[0].extra.get("custom_id") == 99


def test_store_load_root_not_dict_or_accounts_not_list(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    store = AccountStore(root)

    # Not a dict
    (root / "accounts.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(CorruptState):
        store.load()

    # Accounts not a list
    (root / "accounts.json").write_text(
        json.dumps({"schema": 2, "accounts": "bad"}),
        encoding="utf-8",
    )
    with pytest.raises(CorruptState):
        store.load()


def test_find_active() -> None:
    blob1 = make_blob(1)
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    accounts = [
        Account(slot=1, email="alice@example.com", fp="wrong_fp", added_at=now, updated_at=now),
        Account(slot=2, email="bob@example.com", fp="other_fp", added_at=now, updated_at=now),
    ]
    assert find_active(accounts, None) is None
    assert find_active(accounts, blob1) is None
    assert find_active(accounts, b"corrupt json blob") is None

    # Matching blob with dict accounts
    blob2 = make_blob(2)
    from mswap.agy.tokens import fingerprint

    matched_dict_accounts = [
        {"slot": 1, "email": "alice@example.com", "fp": "wrong_fp"},
        {"slot": 2, "email": "bob@example.com", "fp": fingerprint(blob2)},
    ]
    active_dict = find_active(matched_dict_accounts, blob2)
    assert active_dict is not None
    assert active_dict["slot"] == 2

    matched_accounts = [
        accounts[0],
        Account(
            slot=2,
            email="bob@example.com",
            fp=fingerprint(blob2),
            added_at=now,
            updated_at=now,
        ),
    ]
    active = find_active(matched_accounts, blob2)
    assert active is not None
    assert active.slot == 2


def test_save_and_load_accounts_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir = tmp_path / "helper_store"
    monkeypatch.setenv("MSWAP_HOME", str(store_dir))

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    accounts = [
        Account(slot=2, email="bob@example.com", fp="fp2", added_at=now, updated_at=now),
        {"slot": 1, "email": "alice@example.com", "fp": "fp1", "added_at": now.isoformat()},
    ]
    save_accounts(accounts)

    loaded = load_accounts()
    assert len(loaded) == 2
    assert loaded[0].slot == 1
    assert loaded[1].slot == 2
