"""Integration tests for `mswap shim install`."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mswap.cli import main
from mswap.cli.commands.shim import SHIM_NAME, dir_on_path, install_shim, shim_text
from mswap.core.errors import UsageError


def _set_active_env(key: str, value: str | None) -> None:
    from mswap.cli import context as context_mod

    active = context_mod._ACTIVE_CONTEXT
    assert active is not None
    env = dict(active.env)
    if value is None:
        env.pop(key, None)
    else:
        env[key] = value
    active.env = env


@pytest.fixture(autouse=True)
def _force_windows() -> None:
    _set_active_env("MSWAP_TEST_FORCE_WINDOWS", "1")


def test_shim_text_is_crlf_and_uses_module_launch() -> None:
    assert shim_text("C:\\py\\python.exe") == '@"C:\\py\\python.exe" -m mswap %*\r\n'


def test_install_writes_shim_with_crlf_bytes(tmp_path: Path) -> None:
    target, backup = install_shim(tmp_path / "bin", "py.exe", force=False)
    assert backup is None
    assert target == tmp_path / "bin" / SHIM_NAME
    assert target.read_bytes() == b'@"py.exe" -m mswap %*\r\n'


def test_install_overwrites_existing_mswap_shim_without_force(tmp_path: Path) -> None:
    install_shim(tmp_path, "old.exe", force=False)
    target, backup = install_shim(tmp_path, "new.exe", force=False)
    assert backup is None
    assert b'"new.exe"' in target.read_bytes()
    assert not (tmp_path / "mswap.cmd.bak").exists()


def test_install_refuses_foreign_file_without_force(tmp_path: Path) -> None:
    foreign = tmp_path / SHIM_NAME
    foreign.write_bytes(b"@echo hello\r\n")
    with pytest.raises(UsageError) as exc:
        install_shim(tmp_path, "py.exe", force=False)
    assert "--force" in (exc.value.hint or "")
    assert foreign.read_bytes() == b"@echo hello\r\n"


def test_install_force_backs_up_foreign_file(tmp_path: Path) -> None:
    foreign = tmp_path / SHIM_NAME
    foreign.write_bytes(b"@echo hello\r\n")
    target, backup = install_shim(tmp_path, "py.exe", force=True)
    assert backup == tmp_path / "mswap.cmd.bak"
    assert backup.read_bytes() == b"@echo hello\r\n"
    assert b"-m mswap" in target.read_bytes()


def test_install_force_replaces_stale_backup(tmp_path: Path) -> None:
    (tmp_path / "mswap.cmd.bak").write_text("older", encoding="utf-8")
    (tmp_path / SHIM_NAME).write_text("@echo new-foreign", encoding="utf-8")
    _, backup = install_shim(tmp_path, "py.exe", force=True)
    assert backup is not None
    assert backup.read_text(encoding="utf-8") == "@echo new-foreign"


def test_dir_on_path_matching(tmp_path: Path) -> None:
    d = tmp_path / "bin"
    path_env = os.pathsep.join(["", str(tmp_path / "other"), str(d) + os.sep])
    assert dir_on_path(d, path_env)
    assert not dir_on_path(d, str(tmp_path / "elsewhere"))
    assert not dir_on_path(d, "")


def test_cli_install_human_output_not_on_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = tmp_path / "bin"
    _set_active_env("PATH", str(tmp_path / "elsewhere"))
    rc = main(["shim", "install", "--dir", str(d)])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"Wrote {d / SHIM_NAME}" in out
    assert "is not on your PATH" in out
    assert (d / SHIM_NAME).read_bytes() == shim_text(sys.executable).encode()


def test_cli_install_reports_dir_on_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = tmp_path / "bin"
    _set_active_env("PATH", os.pathsep.join([str(tmp_path / "x"), str(d)]))
    rc = main(["shim", "install", "--dir", str(d)])
    assert rc == 0
    assert "is on your PATH." in capsys.readouterr().out


def test_cli_install_default_dir_uses_userprofile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / "profile"
    _set_active_env("USERPROFILE", str(profile))
    rc = main(["shim", "install", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert Path(data["dir"]) == profile / ".local" / "bin"
    assert (profile / ".local" / "bin" / SHIM_NAME).exists()
    assert data["backup"] is None


def test_cli_refusal_exit_code_and_json_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / SHIM_NAME).write_text("@echo mine", encoding="utf-8")
    rc = main(["shim", "install", "--dir", str(tmp_path), "--json"])
    assert rc == 64
    err = json.loads(capsys.readouterr().out)
    assert err["ok"] is False
    assert err["error"]["kind"] == "UsageError"
    assert (tmp_path / SHIM_NAME).read_text(encoding="utf-8") == "@echo mine"


def test_cli_force_backup_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / SHIM_NAME).write_text("@echo mine", encoding="utf-8")
    rc = main(["shim", "install", "--dir", str(tmp_path), "--force"])
    assert rc == 0
    assert "Backed up the existing file" in capsys.readouterr().out
    assert (tmp_path / "mswap.cmd.bak").read_text(encoding="utf-8") == "@echo mine"


def test_cli_missing_action_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["shim"]) == 64
    assert "mswap shim install" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows refusal")
def test_cli_refuses_on_non_windows(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _set_active_env("MSWAP_TEST_FORCE_WINDOWS", None)
    rc = main(["shim", "install", "--dir", str(tmp_path)])
    assert rc == 64
    assert not (tmp_path / SHIM_NAME).exists()
    assert "only needed on Windows" in capsys.readouterr().err
