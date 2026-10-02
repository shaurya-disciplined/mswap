"""Integration tests for mswap doctor command, checks, --online, and --repair."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mswap.agy.tokens import fingerprint
from mswap.cli import main
from mswap.core.journal import Journal
from mswap.core.models import Account
from mswap.core.store import AccountStore, live_target, slot_target, vault_prefix
from mswap.util.http import FakeHttp, json_response
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob


def _seed_config(home: Path, exe: Path, version: str = "1.2.12") -> None:
    exe_sig = f"{exe.stat().st_size}:{int(exe.stat().st_mtime)}" if exe.exists() else "dummy:1"
    content = json.dumps(
        {
            "exe_sig": exe_sig,
            "client_id": "1071006060591-faketestclient12345678901234.apps.googleusercontent.com",
            "secrets": ["GOCSPX-FAKEsecret12345678901234"],
            "client_secret": "GOCSPX-FAKEsecret12345678901234",
            "agy_version": version,
            "version": version,
        }
    )
    for fname in ("config.json", "client.json"):
        cfg = home / fname
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(content, encoding="utf-8")


def _setup_healthy_environment(
    tmp_path: Path, vault: MemoryVault, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)

    fake_exe = tmp_path / "bin" / "agy.exe"
    fake_exe.parent.mkdir(parents=True, exist_ok=True)
    fake_exe.write_bytes(b"dummy-agy-binary-bytes")

    monkeypatch.setenv("MSWAP_AGY_EXE", str(fake_exe))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("MSWAP_TOOLS_BIN", str(tmp_path / "tools_bin"))
    _seed_config(home, fake_exe)

    future_expiry = "2026-10-02T20:00:00+05:30"
    blob1 = make_blob(1, expiry=future_expiry)
    blob2 = make_blob(2, expiry=future_expiry)
    fp1 = fingerprint(blob1)
    fp2 = fingerprint(blob2)

    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    accounts = [
        Account(slot=1, email="alice@example.com", fp=fp1, added_at=now, updated_at=now),
        Account(slot=2, email="bob@example.com", fp=fp2, added_at=now, updated_at=now),
    ]
    store = AccountStore(home)
    store.save(accounts)

    vault.write(slot_target(1), blob1, "alice@example.com")
    vault.write(slot_target(2), blob2, "bob@example.com")
    vault.write(live_target(), blob1, "antigravity")

    return home, fake_exe


def test_doctor_all_ok(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)

    rc = main(["doctor"])
    assert rc == 0
    captured = capsys.readouterr()
    lines = [line.strip() for line in captured.out.strip().splitlines() if line.strip()]

    assert any("agy 1.2.12 at" in line for line in lines)
    assert any("agy is signed in" in line for line in lines)
    assert any("OAuth client configuration valid" in line for line in lines)
    assert any("2 accounts" in line for line in lines)
    assert any("all accounts have saved logins" in line for line in lines)
    assert any("no orphan saved logins" in line for line in lines)
    assert any("Active: 1 alice@example.com" in line for line in lines)
    assert any("switch journal clean" in line for line in lines)
    assert any("data directory is writable" in line for line in lines)
    assert any("no blocked launchers" in line for line in lines)
    assert lines[-1] == "All good."


def test_doctor_agy_installed_fail(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    non_existent = tmp_path / "does_not_exist" / "agy.exe"
    monkeypatch.setenv("MSWAP_AGY_EXE", str(non_existent))

    rc = main(["doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    assert f"agy not found at {non_existent}" in captured.out
    assert "Install agy, or set MSWAP_AGY_EXE." in captured.out
    assert "problem" in captured.out


def test_doctor_agy_login_signed_out(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    vault.delete(live_target())

    rc = main(["doctor"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "! agy is signed out" in captured.out
    assert "problem" in captured.out


def test_doctor_agy_login_damaged(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    vault.write(live_target(), b"corrupted-invalid-blob", "antigravity")

    rc = main(["doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "agy's login entry is damaged" in captured.out


def test_doctor_agy_client_fail(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, _ = _setup_healthy_environment(tmp_path, vault, monkeypatch)
    for name in ("client.json", "config.json"):
        f = home / name
        if f.exists():
            f.unlink()

    rc = main(["doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "OAuth client details missing or unreadable" in captured.out
    assert "agy may have changed" in captured.out


def test_doctor_store_readable_fail(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, _ = _setup_healthy_environment(tmp_path, vault, monkeypatch)
    (home / "accounts.json").write_text("{ corrupt json", encoding="utf-8")

    rc = main(["doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "store.readable" in captured.out or "accounts.json" in captured.out


def test_doctor_store_slots_missing(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    vault.delete(slot_target(2))

    rc = main(["doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Account 2's saved login is missing" in captured.out
    assert "Sign in as it in agy and run `mswap add`." in captured.out


def test_doctor_store_orphans_warn(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    prefix = vault_prefix()
    vault.write(f"{prefix}slot99", make_blob(99), "orphan@example.com")

    rc = main(["doctor"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Found saved logins mswap no longer tracks: slot99" in captured.out
    assert "Run `mswap doctor --repair` to remove them." in captured.out
    assert "1 problem found." in captured.out


def test_doctor_store_active_unsaved(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    unsaved_blob = make_blob(42)
    vault.write(live_target(), unsaved_blob, "antigravity")

    rc = main(["doctor"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "! agy is signed in to an unsaved account" in captured.out
    assert "`mswap add`" in captured.out


def test_doctor_journal_interrupted_switch(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, _ = _setup_healthy_environment(tmp_path, vault, monkeypatch)
    j = Journal(home / "journal.json")
    j.begin(op="switch", from_fp="fp1", to_fp="fp2", live_fp="fp1")

    rc = main(["doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "An interrupted switch was found" in captured.out
    assert "Run `mswap doctor --repair`." in captured.out


def test_doctor_data_writable_fail(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)

    def failing_write_text(self: Any, data: str, encoding: str | None = None) -> int:
        if ".mswap_doctor_tmp" in str(self):
            raise PermissionError("Access denied")
        return len(data)

    monkeypatch.setattr(Path, "write_text", failing_write_text)

    rc = main(["doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "data directory not writable" in captured.out


def test_doctor_win_launchers_sac_blocked(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    monkeypatch.setenv("MSWAP_TEST_FORCE_WINDOWS", "1")

    bin_dir = tmp_path / "launcher_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "mswap.cmd").write_text("@echo off", encoding="utf-8")
    (bin_dir / "mswap.cmd.sac-blocked").write_text("blocked", encoding="utf-8")

    monkeypatch.setenv("PATH", str(bin_dir))

    rc = main(["doctor"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Smart App Control blocked a launcher" in captured.out
    assert "Run `mswap shim install`." in captured.out


def test_doctor_online_all_ok(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    http: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)

    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response({"groups": []}),
    )

    rc = main(["doctor", "--online"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "all accounts refreshed or fresh" in captured.out
    assert "quota API reachable" in captured.out
    assert "All good." in captured.out


def test_doctor_online_refresh_token_dead(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    http: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)

    blob_expired = make_blob(1, expiry="2020-01-01T00:00:00Z")
    vault.write(slot_target(1), blob_expired, "alice@example.com")

    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(
            {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
            status=400,
        ),
    )

    rc = main(["doctor", "--online"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Account 1 (alice@example.com)'s saved login is dead" in captured.out


def test_doctor_online_quota_api_fail(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    http: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)

    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response({"error": {"message": "Internal quota service error"}}, status=500),
    )

    rc = main(["doctor", "--online"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "quota API error" in captured.out or "500" in captured.out


def test_doctor_json_shape(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)

    rc = main(["doctor", "--json"])
    assert rc == 0
    captured = capsys.readouterr()

    payload = json.loads(captured.out)
    assert payload.get("schema") == 1
    assert payload.get("ok") is True
    assert payload.get("command") == "doctor"
    assert isinstance(payload.get("checks"), list)
    assert isinstance(payload.get("data"), dict)

    data_checks = payload["data"]["checks"]
    assert len(data_checks) == 12
    for chk in data_checks:
        assert "id" in chk
        assert chk["status"] in ("ok", "warn", "fail")
        assert "message" in chk
        assert "hint" in chk


def test_doctor_repair_removes_orphans_and_fixes_begun_journal(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, _ = _setup_healthy_environment(tmp_path, vault, monkeypatch)
    prefix = vault_prefix()

    orphan_target = f"{prefix}slot99"
    vault.write(orphan_target, make_blob(99), "orphan@example.com")
    assert vault.read(orphan_target) is not None

    blob2 = vault.read(slot_target(2))
    assert blob2 is not None
    vault.write(live_target(), blob2, "antigravity")
    fp2 = fingerprint(blob2)

    j = Journal(home / "journal.json")
    j.begin(op="switch", from_fp="fp1", to_fp=fp2, live_fp="fp1")

    rc_before = main(["doctor"])
    assert rc_before == 1

    rc_repair = main(["doctor", "--repair"])
    assert rc_repair == 0
    captured = capsys.readouterr()

    assert "Recovered from interrupted switch: switch had completed." in captured.out
    assert "Deleted orphan saved login: slot99" in captured.out

    assert vault.read(orphan_target) is None

    st = j.state()
    assert st is not None
    assert st.get("state") == "committed"

    assert "All good." in captured.out


def test_doctor_repair_json(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    prefix = vault_prefix()
    orphan_target = f"{prefix}slot99"
    vault.write(orphan_target, make_blob(99), "orphan@example.com")

    rc = main(["doctor", "--repair", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["ok"] is True
    assert "repaired" in payload["data"]
    assert any("Deleted orphan saved login: slot99" in r for r in payload["data"]["repaired"])
