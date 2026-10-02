"""Unit tests for MSWAP_DEMO environment, DemoVault, and __demo-seed command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mswap.cli import main
from mswap.cli.context import set_context
from mswap.cli.parser import build_parser
from mswap.core.errors import UsageError, VaultError
from mswap.vault.base import MAX_BLOB
from mswap.vault.demo import DemoVault


def test_demo_vault_crud(tmp_path: Path) -> None:
    vault_file = tmp_path / "vault.json"
    vault = DemoVault(vault_file)

    assert vault.read("test:target") is None
    assert vault.delete("test:target") is False
    assert vault.list("test:") == []

    blob = b"hello-world"
    vault.write("test:target1", blob, "user1")
    assert vault.read("test:target1") == blob
    assert vault.list("test:") == ["test:target1"]

    vault.write("test:target2", b"another", "user2")
    assert len(vault.list("test:")) == 2

    assert vault.delete("test:target1") is True
    assert vault.read("test:target1") is None
    assert vault.list("test:") == ["test:target2"]


def test_demo_vault_max_blob_limit(tmp_path: Path) -> None:
    vault = DemoVault(tmp_path / "vault.json")
    with pytest.raises(VaultError, match="exceeds maximum size"):
        vault.write("test:large", b"x" * (MAX_BLOB + 1), "user")


def test_demo_parser_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSWAP_DEMO", raising=False)
    parser_normal = build_parser()
    with pytest.raises(UsageError):
        parser_normal.parse_args(["__demo-seed"])

    monkeypatch.setenv("MSWAP_DEMO", "1")
    parser_demo = build_parser()
    ns = parser_demo.parse_args(["__demo-seed"])
    assert ns.command == "__demo-seed"


def test_demo_seed_and_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    set_context(None)
    monkeypatch.setenv("MSWAP_HOME", str(tmp_path))
    monkeypatch.setenv("MSWAP_DEMO", "1")
    monkeypatch.setenv("MSWAP_NO_NETWORK", "1")
    monkeypatch.setenv("MSWAP_DEBUG", "1")

    # 1. Seed demo data
    code = main(["__demo-seed"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Demo environment seeded successfully." in captured.out

    # Verify accounts.json and usage.json were created
    accounts_file = tmp_path / "accounts.json"
    usage_file = tmp_path / "usage.json"
    assert accounts_file.exists()
    assert usage_file.exists()

    accounts_data = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert len(accounts_data["accounts"]) == 3
    assert accounts_data["accounts"][0]["email"] == "alice@example.com"
    assert accounts_data["accounts"][1]["email"] == "bob@example.com"
    assert accounts_data["accounts"][2]["email"] == "carol@example.com"

    # 2. List accounts
    code = main(["list"])
    assert code == 0
    captured = capsys.readouterr()
    assert "alice@example.com" in captured.out
    assert "bob@example.com" in captured.out
    assert "carol@example.com" in captured.out
    assert "(active)" in captured.out

    # 3. Switch accounts (1 -> 2)
    code = main(["switch"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Switched agy to account 2: bob@example.com" in captured.out

    # 4. List again: bob is now active
    code = main(["list"])
    assert code == 0
    captured = capsys.readouterr()
    assert "▸ 2  bob@example.com (active)" in captured.out
