"""Integration tests verifying behavior equivalence with the personal mswap script."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mswap.agy.paths import agy_exe
from mswap.agy.tokens import fingerprint
from mswap.cli import main
from mswap.core.store import save_accounts
from mswap.util.http import FakeHttp, json_response
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob


def _seed_config(version: str = "1.2.12") -> None:
    exe = agy_exe()
    exe_sig = f"{exe.stat().st_size}:{int(exe.stat().st_mtime)}" if exe.exists() else "dummy:1"
    content = json.dumps(
        {
            "exe_sig": exe_sig,
            "client_id": ("1071006060591-faketestclient12345678901234.apps.googleusercontent.com"),
            "secrets": ["GOCSPX-FAKEsecret12345678901234"],
            "client_secret": "GOCSPX-FAKEsecret12345678901234",
            "version": version,
        }
    )
    for fname in ("config.json", "client.json"):
        config_file = Path(os.environ["MSWAP_HOME"]) / fname
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(content, encoding="utf-8")


def _load_fixture(name: str) -> dict[str, object]:
    path = Path(__file__).resolve().parent.parent / "fixtures" / "api" / name
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_cli_add_and_update(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    live = make_blob(1)
    vault.write("mswaptest:live", live, "antigravity")

    userinfo = _load_fixture("userinfo.json")
    refresh_json = _load_fixture("token_refresh.json")

    http.add("POST", "https://oauth2.googleapis.com/token", json_response(refresh_json))
    http.add("GET", "https://www.googleapis.com/oauth2/v2/userinfo", json_response(userinfo))

    # 1. First add
    rc = main(["add"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Added account 1: alice@example.com" in captured.out

    assert vault.read("mswaptest:slot1") == live
    assert vault.read("mswaptest:backup-original") == live

    accounts_file = Path(os.environ["MSWAP_HOME"]) / "accounts.json"
    assert accounts_file.exists()
    accounts_data = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert len(accounts_data["accounts"]) == 1
    assert accounts_data["accounts"][0]["slot"] == 1
    assert accounts_data["accounts"][0]["fp"] == fingerprint(live)
    assert accounts_data["accounts"][0]["email"] == "alice@example.com"

    # 2. Add again (updates)
    http.add("POST", "https://oauth2.googleapis.com/token", json_response(refresh_json))
    http.add("GET", "https://www.googleapis.com/oauth2/v2/userinfo", json_response(userinfo))
    rc_update = main(["add"])
    assert rc_update == 0
    captured_update = capsys.readouterr()
    assert "✓ Updated account 1: alice@example.com" in captured_update.out


def test_cli_add_new(
    vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    live = make_blob(1)
    vault.write("mswaptest:live", live, "antigravity")

    userinfo = _load_fixture("userinfo.json")
    refresh_json = _load_fixture("token_refresh.json")

    http.add("POST", "https://oauth2.googleapis.com/token", json_response(refresh_json))
    http.add("GET", "https://www.googleapis.com/oauth2/v2/userinfo", json_response(userinfo))

    rc = main(["add", "--new"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Added account 1: alice@example.com" in captured.out
    assert "Signed agy out on this PC only" in captured.out
    assert "Now run agy, sign in with the next Google account, then run mswap add." in captured.out

    assert vault.read("mswaptest:backup-last") == live
    assert vault.read("mswaptest:live") is None


def test_cli_switch(vault: MemoryVault, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_config()
    live1 = make_blob(1)
    live2 = make_blob(2)

    fp1 = fingerprint(live1)
    fp2 = fingerprint(live2)

    accounts = [
        {"slot": 1, "email": "alice@example.com", "fp": fp1, "added_at": "2026-10-01T00:00:00"},
        {"slot": 2, "email": "bob@example.com", "fp": fp2, "added_at": "2026-10-01T00:00:00"},
    ]
    save_accounts(accounts)

    vault.write("mswaptest:slot1", live1, "alice@example.com")
    vault.write("mswaptest:slot2", live2, "bob@example.com")
    vault.write("mswaptest:live", live1, "antigravity")

    rc = main(["switch"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "✓ Switched agy to account 2: bob@example.com" in captured.out

    assert vault.read("mswaptest:live") == live2
    assert vault.read("mswaptest:backup-last") == live1
    assert vault.read("mswaptest:slot1") == live1


def test_cli_switch_already_on_target(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    live1 = make_blob(1)
    fp1 = fingerprint(live1)

    accounts = [
        {"slot": 1, "email": "alice@example.com", "fp": fp1, "added_at": "2026-10-01T00:00:00"},
    ]
    save_accounts(accounts)

    vault.write("mswaptest:slot1", live1, "alice@example.com")
    vault.write("mswaptest:live", live1, "antigravity")

    rc = main(["switch", "1"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Already on account 1: alice@example.com" in captured.out


def test_cli_switch_with_one_account(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_config()
    live1 = make_blob(1)
    fp1 = fingerprint(live1)

    accounts = [
        {"slot": 1, "email": "alice@example.com", "fp": fp1, "added_at": "2026-10-01T00:00:00"},
    ]
    save_accounts(accounts)

    vault.write("mswaptest:slot1", live1, "alice@example.com")
    vault.write("mswaptest:live", live1, "antigravity")

    rc = main(["switch"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "✗ Only one account saved." in captured.err
    assert "→ Add another with `mswap add --new`." in captured.err


def test_cli_switch_not_matching(capsys: pytest.CaptureFixture[str]) -> None:
    _seed_config()
    live1 = make_blob(1)
    fp1 = fingerprint(live1)

    accounts = [
        {"slot": 1, "email": "alice@example.com", "fp": fp1, "added_at": "2026-10-01T00:00:00"},
    ]
    save_accounts(accounts)

    rc = main(["switch", "7"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "✗ No account matching '7'." in captured.err
    assert "→ See `mswap list`." in captured.err


def test_cli_list(vault: MemoryVault, http: FakeHttp, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_config(version="1.2.12")
    live1 = make_blob(1)
    fp1 = fingerprint(live1)

    accounts = [
        {"slot": 1, "email": "alice@example.com", "fp": fp1, "added_at": "2026-10-01T00:00:00"},
    ]
    save_accounts(accounts)

    vault.write("mswaptest:slot1", live1, "alice@example.com")
    vault.write("mswaptest:live", live1, "antigravity")

    refresh_json = _load_fixture("token_refresh.json")
    http.add("POST", "https://oauth2.googleapis.com/token", json_response(refresh_json))

    quota_json = _load_fixture("quota_summary.json")
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(quota_json),
    )

    rc = main(["list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "mswap · agy accounts" in captured.out
    assert "▸ 1  alice@example.com (active)" in captured.out
    assert "Gemini" in captured.out

    # Verify User-Agent header
    from mswap.agy.install import os_arch, user_agent

    quota_req = next(r for r in http.requests if "retrieveUserQuotaSummary" in r["url"])
    assert quota_req["headers"]["User-Agent"] == user_agent("1.2.12", os_arch())
