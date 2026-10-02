"""Integration tests for mswap v0.2 command suite and --json support everywhere."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from mswap.agy.paths import agy_exe
from mswap.agy.tokens import fingerprint
from mswap.cli import main
from mswap.core.models import Account, Quarantine
from mswap.core.store import AccountStore, live_target, slot_target
from mswap.util.http import FakeHttp, HttpResponse, json_response
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob


def _seed_config(version: str = "1.2.12") -> None:
    exe = agy_exe()
    exe_sig = f"{exe.stat().st_size}:{int(exe.stat().st_mtime)}" if exe.exists() else "dummy:1"
    content = json.dumps(
        {
            "exe_sig": exe_sig,
            "client_id": "1071006060591-faketestclient12345678901234.apps.googleusercontent.com",
            "secrets": ["GOCSPX-FAKEsecret12345678901234"],
            "client_secret": "GOCSPX-FAKEsecret12345678901234",
            "version": version,
        }
    )
    for fname in ("config.json", "client.json"):
        cfg = Path(os.environ["MSWAP_HOME"]) / fname
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(content, encoding="utf-8")


def _load_fixture(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "fixtures" / "api" / name
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _check_json_envelope(obj: dict[str, Any], command: str) -> dict[str, Any]:
    assert obj.get("schema") == 1
    assert obj.get("ok") is True
    assert obj.get("command") == command
    assert isinstance(obj.get("data"), dict)
    return obj["data"]


def _check_add_data(data: dict[str, Any]) -> None:
    assert data["status"] in ("added", "updated")
    assert isinstance(data["slot"], int)
    assert isinstance(data["email"], str)
    assert data["alias"] is None or isinstance(data["alias"], str)
    assert isinstance(data["signed_out"], bool)


def _check_remove_data(data: dict[str, Any]) -> None:
    assert isinstance(data["removed_slot"], int)
    assert isinstance(data["email"], str)


def _check_alias_data(data: dict[str, Any]) -> None:
    assert isinstance(data["slot"], int)
    assert data["alias"] is None or isinstance(data["alias"], str)


def _check_toggle_data(data: dict[str, Any]) -> None:
    assert isinstance(data["slot"], int)
    assert isinstance(data["disabled"], bool)


def _check_current_data(data: dict[str, Any]) -> None:
    assert isinstance(data["live_present"], bool)
    if data["active"] is not None:
        assert isinstance(data["active"]["slot"], int)
        assert isinstance(data["active"]["email"], str)
        assert "alias" in data["active"]
        assert "disabled" in data["active"]


def _check_list_data(data: dict[str, Any]) -> None:
    assert data["active_slot"] is None or isinstance(data["active_slot"], int)
    assert isinstance(data["accounts"], list)
    for acc in data["accounts"]:
        assert isinstance(acc["slot"], int)
        assert isinstance(acc["email"], str)
        assert isinstance(acc["active"], bool)
        assert isinstance(acc["disabled"], bool)
        assert isinstance(acc["usage"], dict)
        assert isinstance(acc["usage"]["stale"], bool)
        assert isinstance(acc["usage"]["pools"], list)
        for pool in acc["usage"]["pools"]:
            assert isinstance(pool["key"], str)
            assert isinstance(pool["name"], str)
            assert isinstance(pool["buckets"], list)
            for b in pool["buckets"]:
                assert isinstance(b["window"], str)
                assert isinstance(b["remaining"], (int, float))
                assert b["reset_at"] is None or isinstance(b["reset_at"], str)


# ---------------------------------------------------------------------------
# 1. ADD COMMAND TESTS
# ---------------------------------------------------------------------------


def test_add_new_account_human_and_alias(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    live = make_blob(1)
    vault.write("mswaptest:live", live, "antigravity")

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response(_load_fixture("userinfo.json")),
    )

    rc = main(["add", "--alias", "work"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Added account 1: alice@example.com" in captured.out
    assert "To add another: `mswap add --new`" in captured.out

    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    accs = store.load()
    assert len(accs) == 1
    assert accs[0].slot == 1
    assert accs[0].alias == "work"
    assert accs[0].email == "alice@example.com"


def test_add_existing_account_clears_quarantine_human(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    live = make_blob(1)
    vault.write("mswaptest:live", live, "antigravity")

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response(_load_fixture("userinfo.json")),
    )

    # First add
    assert main(["add"]) == 0
    capsys.readouterr()

    # Quarantine the account
    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    accs = store.load()
    accs[0] = accs[0].__class__(
        slot=1,
        email="alice@example.com",
        fp="fp1",
        added_at=accs[0].added_at,
        updated_at=accs[0].updated_at,
        quarantined=Quarantine(reason="invalid_grant", at=accs[0].added_at),
    )
    store.save(accs)

    # Add again (updates and clears quarantine)
    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response(_load_fixture("userinfo.json")),
    )
    rc = main(["add"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Updated account 1: alice@example.com" in captured.out

    updated = store.load()
    assert updated[0].quarantined is None


def test_add_new_flag_human(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    live = make_blob(1)
    vault.write("mswaptest:live", live, "antigravity")

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response(_load_fixture("userinfo.json")),
    )

    rc = main(["add", "--new"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Signed agy out on this PC only. The saved copy stays valid." in captured.out
    assert "Now run agy, sign in with the next Google account, then run mswap add." in captured.out
    assert vault.read("mswaptest:live") is None
    assert vault.read("mswaptest:backup-last") == live


def test_add_json_output(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    live = make_blob(1)
    vault.write("mswaptest:live", live, "antigravity")

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response(_load_fixture("userinfo.json")),
    )

    rc = main(["add", "--alias", "personal", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    data = _check_json_envelope(json.loads(captured.out), "add")
    _check_add_data(data)
    assert data["status"] == "added"
    assert data["slot"] == 1
    assert data["email"] == "alice@example.com"
    assert data["alias"] == "personal"
    assert data["signed_out"] is False


def test_add_not_signed_in_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["add"])
    assert rc == 4
    captured = capsys.readouterr()
    assert "agy isn't signed in." in captured.err


def test_add_invalid_alias_error(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    live = make_blob(1)
    vault.write("mswaptest:live", live, "antigravity")
    rc = main(["add", "--alias", "1bad"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "Invalid alias name." in captured.err


def test_add_duplicate_alias_error(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    from datetime import UTC, datetime

    t0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    store.save(
        [
            Account(
                slot=1,
                email="other@example.com",
                fp="fp0",
                added_at=t0,
                updated_at=t0,
                alias="work",
            )
        ]
    )

    live = make_blob(2)
    vault.write("mswaptest:live", live, "antigravity")

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response(_load_fixture("userinfo.json")),
    )

    rc = main(["add", "--alias", "work"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "Alias 'work' is already in use by account 1." in captured.err


# ---------------------------------------------------------------------------
# 2. REMOVE COMMAND TESTS
# ---------------------------------------------------------------------------


def _seed_two_accounts(vault: MemoryVault) -> None:
    from datetime import UTC, datetime

    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    t0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    b1 = make_blob(1)
    b2 = make_blob(2)
    vault.write(slot_target(1), b1, "alice@example.com")
    vault.write(slot_target(2), b2, "bob@example.com")
    vault.write(live_target(), b1, "antigravity")
    store.save(
        [
            Account(
                slot=1,
                email="alice@example.com",
                fp=fingerprint(b1),
                added_at=t0,
                updated_at=t0,
                alias="work",
            ),
            Account(
                slot=2,
                email="bob@example.com",
                fp=fingerprint(b2),
                added_at=t0,
                updated_at=t0,
            ),
        ]
    )


def test_remove_with_yes_human(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)
    rc = main(["remove", "2", "--yes"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Removed account 2: bob@example.com" in captured.out

    assert vault.read(slot_target(2)) is None
    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    accs = store.load()
    assert len(accs) == 1
    assert accs[0].slot == 1


def test_remove_json_output(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)
    rc = main(["remove", "2", "--yes", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    data = _check_json_envelope(json.loads(captured.out), "remove")
    _check_remove_data(data)
    assert data["removed_slot"] == 2
    assert data["email"] == "bob@example.com"


def test_remove_non_tty_without_yes_error(
    vault: MemoryVault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    rc = main(["remove", "2"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "Refusing to remove without confirmation." in captured.err
    assert "Add --yes." in captured.err


def test_remove_tty_confirmation_yes(
    vault: MemoryVault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    fake_stdin = io.StringIO("y\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    rc = main(["remove", "2"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Remove account 2 (bob@example.com)?" in captured.out
    assert "✓ Removed account 2: bob@example.com" in captured.out


def test_remove_tty_confirmation_no(
    vault: MemoryVault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    fake_stdin = io.StringIO("n\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    rc = main(["remove", "2"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Removed account" not in captured.out

    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    assert len(store.load()) == 2


def test_remove_active_account_leaves_live_untouched(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    live_before = vault.read(live_target())
    assert live_before is not None

    # Account 1 is active
    rc = main(["remove", "1", "--yes"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Removed account 1: alice@example.com" in captured.out
    assert "agy is still signed in to it; mswap just won't switch to it anymore." in captured.out

    # Live entry still intact!
    assert vault.read(live_target()) == live_before
    # Slot 1 credential deleted
    assert vault.read(slot_target(1)) is None


def test_remove_no_accounts_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["remove", "1", "--yes"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "No accounts saved yet." in captured.err


def test_remove_missing_account_error(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    rc = main(["remove", "99", "--yes"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "No account matching '99'." in captured.err


# ---------------------------------------------------------------------------
# 3. ALIAS COMMAND TESTS
# ---------------------------------------------------------------------------


def test_alias_set_and_clear_human(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)

    # Set alias
    rc_set = main(["alias", "2", "personal"])
    assert rc_set == 0
    captured = capsys.readouterr()
    assert "✓ Set alias for account 2 to 'personal'." in captured.out

    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    assert store.load()[1].alias == "personal"

    # Clear alias
    rc_clear = main(["alias", "2", "--clear"])
    assert rc_clear == 0
    captured_clear = capsys.readouterr()
    assert "✓ Cleared alias for account 2." in captured_clear.out
    assert store.load()[1].alias is None


def test_alias_json_output(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)
    rc = main(["alias", "2", "alt", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    data = _check_json_envelope(json.loads(captured.out), "alias")
    _check_alias_data(data)
    assert data["slot"] == 2
    assert data["alias"] == "alt"


def test_alias_invalid_name_error(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)
    rc = main(["alias", "2", "Invalid-Name"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "Invalid alias name." in captured.err


def test_alias_duplicate_name_error(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)  # account 1 already has alias 'work'
    rc = main(["alias", "2", "work"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "Alias 'work' is already in use by account 1." in captured.err


def test_alias_both_name_and_clear_error(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    rc = main(["alias", "2", "work2", "--clear"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "Cannot specify both an alias name and --clear." in captured.err


def test_alias_neither_name_nor_clear_error(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    rc = main(["alias", "2"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "Must specify an alias name or --clear." in captured.err


# ---------------------------------------------------------------------------
# 4. DISABLE / ENABLE COMMAND TESTS
# ---------------------------------------------------------------------------


def test_disable_and_enable_human(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)

    # Disable account 2
    rc_dis = main(["disable", "2"])
    assert rc_dis == 0
    captured_dis = capsys.readouterr()
    assert "✓ Account 2 disabled. Rotation and autopilot will skip it." in captured_dis.out

    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    assert store.load()[1].disabled is True

    # Enable account 2
    rc_en = main(["enable", "2"])
    assert rc_en == 0
    captured_en = capsys.readouterr()
    assert "✓ Account 2 enabled." in captured_en.out
    assert store.load()[1].disabled is False


def test_disable_then_rotate_skips(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    from datetime import UTC, datetime

    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    t0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    b1 = make_blob(1)
    b2 = make_blob(2)
    b3 = make_blob(3)

    vault.write(slot_target(1), b1, "u1@example.com")
    vault.write(slot_target(2), b2, "u2@example.com")
    vault.write(slot_target(3), b3, "u3@example.com")
    vault.write(live_target(), b1, "antigravity")

    # Account 2 is disabled
    store.save(
        [
            Account(
                slot=1,
                email="u1@example.com",
                fp=fingerprint(b1),
                added_at=t0,
                updated_at=t0,
            ),
            Account(
                slot=2,
                email="u2@example.com",
                fp=fingerprint(b2),
                added_at=t0,
                updated_at=t0,
                disabled=True,
            ),
            Account(
                slot=3,
                email="u3@example.com",
                fp=fingerprint(b3),
                added_at=t0,
                updated_at=t0,
            ),
        ]
    )

    # Switch without selector rotates: should skip account 2 and land on account 3!
    rc = main(["switch"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Switched agy to account 3: u3@example.com" in captured.out


def test_explicit_switch_to_disabled_account(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    main(["disable", "2"])
    capsys.readouterr()

    # Explicit switch to 2 is allowed
    rc = main(["switch", "2"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Switched agy to account 2: bob@example.com" in captured.out


def test_disable_and_enable_json(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)

    rc_dis = main(["disable", "2", "--json"])
    assert rc_dis == 0
    captured_dis = capsys.readouterr()
    data_dis = _check_json_envelope(json.loads(captured_dis.out), "disable")
    _check_toggle_data(data_dis)
    assert data_dis["slot"] == 2
    assert data_dis["disabled"] is True

    rc_en = main(["enable", "2", "--json"])
    assert rc_en == 0
    captured_en = capsys.readouterr()
    data_en = _check_json_envelope(json.loads(captured_en.out), "enable")
    _check_toggle_data(data_en)
    assert data_en["slot"] == 2
    assert data_en["disabled"] is False


# ---------------------------------------------------------------------------
# 5. CURRENT COMMAND TESTS
# ---------------------------------------------------------------------------


def test_current_active_human(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)
    rc = main(["current"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "1  alice@example.com  · alias work" in captured.out


def test_current_unknown_live_human(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)
    unknown_blob = make_blob(99)
    vault.write(live_target(), unknown_blob, "antigravity")

    rc = main(["current"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "agy is signed in to an account mswap hasn't saved. Run `mswap add`." in captured.out


def test_current_not_signed_in_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["current"])
    assert rc == 4
    captured = capsys.readouterr()
    assert "agy isn't signed in." in captured.err


def test_current_json_active(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)
    rc = main(["current", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    data = _check_json_envelope(json.loads(captured.out), "current")
    _check_current_data(data)
    assert data["live_present"] is True
    assert data["active"]["slot"] == 1
    assert data["active"]["email"] == "alice@example.com"
    assert data["active"]["alias"] == "work"


def test_current_json_unknown_live(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_two_accounts(vault)
    unknown_blob = make_blob(99)
    vault.write(live_target(), unknown_blob, "antigravity")

    rc = main(["current", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    data = _check_json_envelope(json.loads(captured.out), "current")
    _check_current_data(data)
    assert data["live_present"] is True
    assert data["active"] is None


def test_current_json_not_signed_in(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["current", "--json"])
    assert rc == 4
    captured = capsys.readouterr()
    assert captured.err == ""
    err_obj = json.loads(captured.out)
    assert err_obj["schema"] == 1
    assert err_obj["ok"] is False
    assert err_obj["command"] == "current"
    assert err_obj["error"]["code"] == 4
    assert err_obj["error"]["kind"] == "NotSignedIn"


# ---------------------------------------------------------------------------
# 6. LIST COMMAND TESTS (--json & human formatting)
# ---------------------------------------------------------------------------


def test_list_json_shape_and_quarantine(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    _seed_two_accounts(vault)

    # Set account 2 to quarantined
    store = AccountStore(Path(os.environ["MSWAP_HOME"]))
    accs = store.load()
    accs[1] = accs[1].__class__(
        slot=2,
        email="bob@example.com",
        fp=accs[1].fp,
        added_at=accs[1].added_at,
        updated_at=accs[1].updated_at,
        quarantined=Quarantine(reason="invalid_grant", at=accs[1].added_at),
    )
    store.save(accs)

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )

    rc = main(["list", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    data = _check_json_envelope(json.loads(captured.out), "list")
    _check_list_data(data)
    assert data["active_slot"] == 1
    assert len(data["accounts"]) == 2

    acc1 = data["accounts"][0]
    assert acc1["slot"] == 1
    assert acc1["active"] is True
    assert acc1["usage"]["error"] is None
    assert len(acc1["usage"]["pools"]) >= 1

    acc2 = data["accounts"][1]
    assert acc2["slot"] == 2
    assert acc2["active"] is False
    assert acc2["quarantined"]["reason"] == "invalid_grant"
    assert "saved login expired or revoked" in acc2["usage"]["error"]
    assert acc2["usage"]["pools"] == []


def test_list_json_empty_store(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["list", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    data = _check_json_envelope(json.loads(captured.out), "list")
    assert data["active_slot"] is None
    assert data["accounts"] == []


def test_list_human_output_tags(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    _seed_two_accounts(vault)

    # Disable account 2
    main(["disable", "2"])
    capsys.readouterr()

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )

    rc = main(["list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "mswap · agy accounts" in captured.out
    assert "1  alice@example.com (active)  · alias work" in captured.out
    assert "2  bob@example.com  · disabled" in captured.out


def test_list_models_fallback_human_note(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    _seed_two_accounts(vault)

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    # Summary returns 404
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        HttpResponse(status=404, body=b"Not Found", headers={}),
    )
    # Fallback to models
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
        json_response(_load_fixture("fetch_available_models.json")),
    )

    rc = main(["list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "(per-model view: summary unavailable)" in captured.out
    assert "model" in captured.out


# ---------------------------------------------------------------------------
# 7. NO ARGS TESTS
# ---------------------------------------------------------------------------


def test_no_args_with_zero_accounts_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc == 0
    captured = capsys.readouterr()
    assert "mswap: switch Google accounts in agy" in captured.out


def test_no_args_with_accounts_runs_list(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    _seed_two_accounts(vault)

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )

    rc = main([])
    assert rc == 0
    captured = capsys.readouterr()
    assert "mswap · agy accounts" in captured.out
    assert "alice@example.com" in captured.out


def test_alias_no_accounts_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["alias", "1", "work"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "No accounts saved yet." in captured.err


def test_disable_no_accounts_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["disable", "1"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "No accounts saved yet." in captured.err


def test_add_with_recovery_message(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    from mswap.core.journal import Journal

    _seed_config()
    live = make_blob(1)
    vault.write("mswaptest:live", live, "antigravity")

    # Set up interrupted switch journal that will be recovered
    journal = Journal(Path(os.environ["MSWAP_HOME"]) / "journal.json")
    journal.begin("switch", from_fp="fp0", to_fp=fingerprint(live), live_fp="fp0")

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response(_load_fixture("userinfo.json")),
    )

    rc = main(["add"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Recovered from interrupted switch" in captured.err
    assert "✓ Added account 1: alice@example.com" in captured.out


def test_remove_with_recovery_message(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    from mswap.core.journal import Journal

    _seed_two_accounts(vault)
    live = vault.read("mswaptest:live")
    assert live is not None

    journal = Journal(Path(os.environ["MSWAP_HOME"]) / "journal.json")
    journal.begin("switch", from_fp="fp0", to_fp=fingerprint(live), live_fp="fp0")

    rc = main(["remove", "2", "--yes"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Recovered from interrupted switch" in captured.err
    assert "✓ Removed account 2: bob@example.com" in captured.out
