"""Unit tests for check_privacy.py scanning engine."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.check_privacy import (
    RULE_DENY_LIST,
    RULE_LOCAL_APPS_PATH,
    RULE_PRIVATE_FILE,
    RULE_REAL_EMAIL,
    RULE_WINDOWS_USER_PATH,
    check_privacy,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@users.noreply.github.com"], cwd=path, check=True
    )


def _git_add(path: Path, rel_file: str) -> None:
    subprocess.run(["git", "add", rel_file], cwd=path, check=True, capture_output=True)


def test_allowed_emails_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    _init_repo(tmp_path)
    safe_file = tmp_path / "safe.txt"
    safe_file.write_text(
        "author: alice@example.com\ncommitter: 12345+name@users.noreply.github.com\n",
        encoding="utf-8",
    )
    _git_add(tmp_path, "safe.txt")

    # Act
    exit_code = check_privacy(tmp_path)
    out, _ = capsys.readouterr()

    # Assert
    assert exit_code == 0
    assert "privacy checks passed" in out


def test_rule_windows_user_path_fires(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    _init_repo(tmp_path)
    bad_file = tmp_path / "bad_path.txt"
    sep = chr(92)
    bad_path = "C:" + sep + "Users" + sep + "testuser"
    bad_file.write_text(f"path = {bad_path}\n", encoding="utf-8")
    _git_add(tmp_path, "bad_path.txt")

    # Act
    exit_code = check_privacy(tmp_path)
    out, _ = capsys.readouterr()

    # Assert
    assert exit_code == 1
    assert f":1: {RULE_WINDOWS_USER_PATH}" in out
    assert bad_path not in out


def test_rule_local_apps_path_fires(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    _init_repo(tmp_path)
    bad_file = tmp_path / "bad_apps.txt"
    sep = chr(92)
    bad_apps = "E:" + sep + "Apps" + sep
    bad_file.write_text(f"root = {bad_apps}\n", encoding="utf-8")
    _git_add(tmp_path, "bad_apps.txt")

    # Act
    exit_code = check_privacy(tmp_path)
    out, _ = capsys.readouterr()

    # Assert
    assert exit_code == 1
    assert f":1: {RULE_LOCAL_APPS_PATH}" in out
    assert bad_apps not in out


def test_rule_real_email_fires(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    _init_repo(tmp_path)
    bad_file = tmp_path / "bad_email.txt"
    bad_mail = "person" + "@" + "gmail.com"
    bad_file.write_text(f"contact: {bad_mail}\n", encoding="utf-8")
    _git_add(tmp_path, "bad_email.txt")

    # Act
    exit_code = check_privacy(tmp_path)
    out, _ = capsys.readouterr()

    # Assert
    assert exit_code == 1
    assert f":1: {RULE_REAL_EMAIL}" in out
    assert bad_mail not in out


def test_rule_private_file_fires(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    _init_repo(tmp_path)
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    secret_note = agent_dir / "secret.md"
    secret_note.write_text("personal notes\n", encoding="utf-8")
    _git_add(tmp_path, ".agent/secret.md")

    # Act
    exit_code = check_privacy(tmp_path)
    out, _ = capsys.readouterr()

    # Assert
    assert exit_code == 1
    assert f".agent/secret.md:1: {RULE_PRIVATE_FILE}" in out


def test_rule_deny_list_fires(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    _init_repo(tmp_path)
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    denylist = agent_dir / "privacy-denylist.txt"
    denylist.write_text("secretname\n", encoding="utf-8")

    code_file = tmp_path / "code.py"
    code_file.write_text("# contains SECRETNAME inside\n", encoding="utf-8")
    _git_add(tmp_path, "code.py")

    # Act
    exit_code = check_privacy(tmp_path)
    out, _ = capsys.readouterr()

    # Assert
    assert exit_code == 1
    assert f":1: {RULE_DENY_LIST}" in out
    assert "secretname" not in out.lower()
