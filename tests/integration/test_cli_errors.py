"""Integration tests for CLI error taxonomy, exit codes, and redaction."""

from __future__ import annotations

import json
from typing import Any

import pytest

from mswap.cli import commands, main
from mswap.core.errors import MswapError
from mswap.core.store import save_accounts
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob


def _seed_two_accounts(vault: MemoryVault) -> None:
    live1 = make_blob(1)
    live2 = make_blob(2)
    accounts = [
        {"slot": 1, "email": "alice@example.com", "fp": "fp1", "added_at": "2026-10-01T00:00:00"},
        {"slot": 2, "email": "bob@example.com", "fp": "fp2", "added_at": "2026-10-01T00:00:00"},
    ]
    save_accounts(accounts)
    vault.write("mswaptest:slot1", live1, "alice@example.com")
    vault.write("mswaptest:slot2", live2, "bob@example.com")
    vault.write("mswaptest:live", live1, "antigravity")


def test_switch_unknown_account_stderr(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    rc = main(["switch", "7"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "✗ No account matching '7'." in captured.err
    assert "→ See `mswap list`." in captured.err
    assert captured.out == ""


def test_switch_unknown_account_json(
    vault: MemoryVault, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_two_accounts(vault)
    rc = main(["switch", "7", "--json"])
    assert rc == 64
    captured = capsys.readouterr()
    assert captured.err == ""
    parsed = json.loads(captured.out)
    assert parsed["schema"] == 1
    assert parsed["ok"] is False
    assert parsed["command"] == "switch"
    assert parsed["error"]["code"] == 64
    assert parsed["error"]["kind"] == "UsageError"
    assert parsed["error"]["message"] == "No account matching '7'."
    assert parsed["error"]["hint"] == "See `mswap list`."


def test_bogus_command_exit_64(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["bogus"])
    assert rc == 64
    captured = capsys.readouterr()
    assert "Run `mswap --help`." in captured.err


def test_internal_error_exit_70_no_traceback_without_debug(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("Crash unexpected")

    monkeypatch.setattr(commands.switch, "run", fake_run)
    monkeypatch.delenv("MSWAP_DEBUG", raising=False)

    rc = main(["switch"])
    assert rc == 70
    captured = capsys.readouterr()
    assert (
        "✗ Internal error (this is a bug). Please report it: "
        "https://github.com/shaurya-disciplined/mswap/issues"
    ) in captured.err
    assert "Traceback" not in captured.err


def test_internal_error_exit_70_with_debug_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("Secret leaked: ya29.SECRETSECRET")

    monkeypatch.setattr(commands.switch, "run", fake_run)
    monkeypatch.setenv("MSWAP_DEBUG", "1")

    rc = main(["switch"])
    assert rc == 70
    captured = capsys.readouterr()
    assert "ya29.SECRETSECRET" not in captured.err
    assert "ya29." not in captured.err
    assert "[REDACTED]" in captured.err
    assert "Traceback (most recent call last):" in captured.err


def test_mswap_error_redacts_tokens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> int:
        raise MswapError("Failed token ya29.SECRETSECRET", hint="Use 1//REFRESHSECRET")

    monkeypatch.setattr(commands.switch, "run", fake_run)

    # Human stderr mode
    rc = main(["switch"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "ya29." not in captured.err
    assert "1//" not in captured.err
    assert "Failed token [REDACTED]" in captured.err
    assert "Use [REDACTED]" in captured.err

    # JSON stdout mode
    rc_json = main(["switch", "--json"])
    assert rc_json == 1
    captured_json = capsys.readouterr()
    assert "ya29." not in captured_json.out
    assert "1//" not in captured_json.out
    parsed = json.loads(captured_json.out)
    assert parsed["error"]["message"] == "Failed token [REDACTED]"
    assert parsed["error"]["hint"] == "Use [REDACTED]"


def test_keyboard_interrupt_returns_130(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(commands.switch, "run", fake_run)
    rc = main(["switch"])
    assert rc == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_flags_only_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--ascii"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "mswap: switch Google accounts in agy" in captured.out


def test_help_command_and_flag(capsys: pytest.CaptureFixture[str]) -> None:
    rc1 = main(["help"])
    assert rc1 == 0
    captured1 = capsys.readouterr()
    assert "mswap: switch Google accounts in agy" in captured1.out

    rc2 = main(["--help"])
    assert rc2 == 0
    captured2 = capsys.readouterr()
    assert "mswap: switch Google accounts in agy" in captured2.out
