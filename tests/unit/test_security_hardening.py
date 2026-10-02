"""Security regression tests from the W7.S1 threat model (docs/security/threat-model.md).

Covers: no secret in any subprocess argv, private data-dir permissions, safe quoting of the
executable path in hooks / schedules / the shim, and redaction of debug tracebacks.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mswap.agy import hooks
from mswap.agy.client_discovery import _save_json
from mswap.cli import main
from mswap.cli.commands import switch as switch_command
from mswap.cli.commands.shim import shim_text
from mswap.core.autopilot import AutopilotStateStore
from mswap.core.errors import UsageError
from mswap.core.events import Events
from mswap.core.journal import Journal
from mswap.core.locking import FileLock
from mswap.core.models import Account
from mswap.core.settings import set_setting
from mswap.core.store import AccountStore
from mswap.core.usage_cache import UsageCache
from mswap.util import schedule_posix, schedule_win
from mswap.util.fsx import append_private_text, ensure_private_dir, write_private_text
from mswap.util.redact import redact
from mswap.util.shellquote import (
    quote_cmd_batch,
    quote_command_path,
    quote_posix,
    quote_windows,
)
from mswap.vault.linux import SecretToolVault
from mswap.vault.macos import MacKeychainVault
from tests.conftest import assert_no_secret_in_argv, make_blob

NASTY_POSIX_PATHS = [
    "/usr/bin/python3",
    "/home/a b/.venv/bin/python",
    "/home/it's/bin/python",
    '/home/say "hi"/python',
    "/home/$HOME/`id`/python",
    "/home/a;rm -rf x/python",
    "/home/line\nbreak/python",
]

# ---------------------------------------------------------------------------
# No secret in argv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "ya29.FAKE-access-1",
        "1//FAKE-refresh-1",
        "GOCSPX-FAKEFAKEFAKEFAKEFAKEFAKEFAKE",
        "eyJhbGciOiJ.eyJzdWIiOiJ.sig-FAKE",
        '{"refresh_token": "x"}',
    ],
)
def test_argv_guard_trips_on_every_secret_shape(secret: str) -> None:
    """The autouse conftest guard stops a real subprocess before it starts."""
    with pytest.raises(AssertionError, match="subprocess argument contains"):
        subprocess.run([sys.executable, "-c", "pass", secret], check=False)
    with pytest.raises(AssertionError, match="subprocess argument contains"):
        subprocess.Popen([sys.executable, "-c", "pass", f"--opt={secret}"])


def test_argv_guard_message_never_echoes_the_value() -> None:
    with pytest.raises(AssertionError) as info:
        assert_no_secret_in_argv(["tool", "ya29.FAKE-access-9"])
    assert "FAKE-access-9" not in str(info.value)


def test_argv_guard_allows_ordinary_arguments() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "print('ok')", "--note", "https://example.com/a//b"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "ok"


def _real_process_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    """Run the vault command line through real Popen (the guard sees it) against a stub exe."""
    stub = [sys.executable, "-c", "import sys; sys.stdin.buffer.read()", *cmd[1:]]
    return subprocess.run(stub, input=kwargs.get("input"), capture_output=True, check=False)


def test_linux_vault_real_argv_carries_no_secret() -> None:
    vault = SecretToolVault(runner=_real_process_runner)
    vault.write("mswaptest:slot1", make_blob(1), "alice@example.com")
    vault.delete("mswaptest:slot1")


def test_macos_vault_real_argv_carries_no_secret() -> None:
    vault = MacKeychainVault(runner=_real_process_runner)
    vault.write("mswaptest:slot1", make_blob(1), "alice@example.com")
    vault.delete("mswaptest:slot1")


# ---------------------------------------------------------------------------
# Private files
# ---------------------------------------------------------------------------


def test_write_private_text_replaces_atomically_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "data" / "x.json"
    write_private_text(target, "one")
    write_private_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in target.parent.iterdir()] == ["x.json"]


def test_write_private_text_cleans_up_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "x.json"

    def boom(self: Path, *_a: Any, **_k: Any) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_private_text(target, "data")
    assert list(tmp_path.iterdir()) == []


def test_append_private_text_appends(tmp_path: Path) -> None:
    target = tmp_path / "d" / "log"
    append_private_text(target, "a\n")
    append_private_text(target, "b\n")
    assert target.read_text(encoding="utf-8").splitlines() == ["a", "b"]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_ensure_private_dir_tightens_an_existing_loose_dir(tmp_path: Path) -> None:
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    ensure_private_dir(loose)
    assert _mode(loose) == 0o700


def _account() -> Account:
    from datetime import UTC, datetime

    now = datetime(2026, 10, 2, tzinfo=UTC)
    return Account(slot=1, email="alice@example.com", fp="0" * 16, added_at=now, updated_at=now)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
@pytest.mark.parametrize(
    "writer", ["events", "journal", "store", "usage", "autopilot", "settings", "client", "lock"]
)
def test_first_writer_creates_a_private_data_dir_and_files(
    writer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Whichever mswap component touches the data dir first, it ends up 0700 with 0600 files."""
    previous_umask = os.umask(0o022)
    request.addfinalizer(lambda: os.umask(previous_umask))
    home = tmp_path / "fresh-home"
    monkeypatch.setenv("MSWAP_HOME", str(home))
    if writer == "events":
        Events(home / "events.log").emit("switch", from_slot=1)
    elif writer == "journal":
        Journal(home / "journal.json").begin("switch", "a" * 16, "b" * 16, None)
    elif writer == "store":
        AccountStore(home).save([_account()])
    elif writer == "usage":
        UsageCache(home / "usage.json")._write_raw({"schema": 1, "accounts": {}})
    elif writer == "autopilot":
        AutopilotStateStore(home / "autopilot.json").save(
            AutopilotStateStore(home / "autopilot.json").load()
        )
    elif writer == "settings":
        set_setting("ui.ascii", "true")
    elif writer == "client":
        _save_json(home / "client.json", {"client_id": "x"})
    else:
        with FileLock(home / "mswap.lock", timeout=1.0):
            pass
    assert _mode(home) == 0o700
    files = [p for p in home.iterdir() if p.is_file()]
    assert files
    for f in files:
        assert _mode(f) == 0o600, f.name


# ---------------------------------------------------------------------------
# Quoting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", NASTY_POSIX_PATHS)
def test_posix_quote_round_trips_to_one_argument(path: str) -> None:
    assert shlex.split(f"{quote_posix(path)} -m mswap") == [path, "-m", "mswap"]


def test_windows_quote_wraps_paths_with_spaces() -> None:
    assert quote_windows(r"C:\Program Files\Python\pythonw.exe") == (
        '"C:\\Program Files\\Python\\pythonw.exe"'
    )


@pytest.mark.parametrize("path", ['C:\\a"b\\python.exe', "C:\\a\nb\\python.exe", "C:\\a\rb"])
def test_windows_quote_refuses_quotes_and_line_breaks(path: str) -> None:
    with pytest.raises(UsageError):
        quote_windows(path)


def test_batch_quote_doubles_percent_signs() -> None:
    assert quote_cmd_batch(r"C:\Tools\100%\py.exe") == '"C:\\Tools\\100%%\\py.exe"'


def test_command_path_picks_platform_rules() -> None:
    assert quote_command_path("/a b/py", windows=False) == "'/a b/py'"
    assert quote_command_path("C:\\a b\\py.exe", windows=True) == '"C:\\a b\\py.exe"'


def test_shim_text_quotes_python_path_and_escapes_percent() -> None:
    text = shim_text(r"C:\Program Files\100%\python.exe")
    assert text == '@"C:\\Program Files\\100%%\\python.exe" -P -m mswap %*\r\n'


def test_shim_text_refuses_a_quote_in_the_path() -> None:
    with pytest.raises(UsageError):
        shim_text('C:\\bad"path\\python.exe')


@pytest.mark.parametrize("path", NASTY_POSIX_PATHS)
def test_hook_command_keeps_the_interpreter_one_argument(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX shell quoting")
    monkeypatch.setattr(sys, "executable", path)
    argv = shlex.split(hooks._default_command())
    assert argv == [path, "-P", "-m", "mswap", "auto", "--once", "--quiet", "--from-hook"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows quoting")
def test_hook_command_quotes_a_windows_path_with_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Python 3\python.exe")
    assert hooks._default_command().startswith(
        '"C:\\Program Files\\Python 3\\python.exe" -P -m mswap'
    )


def test_schtasks_run_command_quotes_pythonw_with_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pythonw = tmp_path / "Program Files" / "pythonw.exe"
    monkeypatch.setattr(schedule_win, "_find_pythonw", lambda: pythonw)
    argv = schedule_win.build_install_argv(5)
    assert argv[argv.index("/TR") + 1] == f'"{pythonw}" -P -m mswap auto --once --quiet'


def test_schtasks_refuses_a_quote_in_pythonw_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schedule_win, "_find_pythonw", lambda: tmp_path / 'we"ird' / "pythonw.exe")
    with pytest.raises(UsageError):
        schedule_win.build_install_argv(5)


@pytest.mark.parametrize("path", NASTY_POSIX_PATHS)
def test_systemd_service_has_exactly_one_exec_line_with_the_path_intact(path: str) -> None:
    unit = schedule_posix.build_systemd_service(python_bin=path)
    exec_lines = [ln for ln in unit.splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_lines) == 1
    assert (
        len([ln for ln in unit.splitlines() if "=" in ln and not ln.startswith("ExecStart=")]) == 2
    )
    rest = exec_lines[0].removeprefix("ExecStart=")
    assert "%" not in rest.replace("%%", "")
    assert rest.endswith(" -P -m mswap auto --once --quiet")


def test_systemd_quote_escapes_specifiers_variables_and_quotes() -> None:
    quote = schedule_posix.quote_systemd_arg
    assert quote("/usr/bin/python3") == "/usr/bin/python3"
    assert quote("/a b/py") == '"/a b/py"'
    assert quote("/a/%h/py") == '"/a/%%h/py"'
    assert quote("/a/$HOME/py") == '"/a/$$HOME/py"'
    assert quote('/a/"q"/py') == '"/a/\\"q\\"/py"'
    assert quote("/a\\b") == '"/a\\\\b"'
    assert quote("a\nb") == '"a\\nb"'
    assert quote("") == '""'


@pytest.mark.parametrize("path", NASTY_POSIX_PATHS)
def test_launchd_plist_keeps_the_interpreter_one_argument(path: str, tmp_path: Path) -> None:
    payload = plistlib.loads(
        schedule_posix.build_macos_plist(5, python_bin=path, data_dir_path=tmp_path)
    )
    assert payload["ProgramArguments"][0] == path
    assert payload["ProgramArguments"][1:] == ["-P", "-m", "mswap", "auto", "--once", "--quiet"]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_python_repr_and_form_bodies() -> None:
    text = (
        "{'refresh_token': 'plain-value', 'access_token': 'other'} "
        "grant_type=refresh_token&refresh_token=plainvalue&client_secret=hunter2"
    )
    cleaned = redact(text)
    assert "plain-value" not in cleaned
    assert "other" not in cleaned
    assert "plainvalue" not in cleaned
    assert "hunter2" not in cleaned
    assert "grant_type=refresh_token&" in cleaned


def test_redact_leaves_lookalike_words_alone() -> None:
    assert redact("my_refresh_token_count=3") == "my_refresh_token_count=3"


def test_debug_traceback_redacts_chained_exceptions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> int:
        try:
            raise ValueError({"refresh_token": "1//REFRESHSECRET", "n": 1})
        except ValueError as inner:
            raise RuntimeError("wrapped ya29.ACCESSSECRET") from inner

    monkeypatch.setattr(switch_command, "run", fake_run)
    monkeypatch.setenv("MSWAP_DEBUG", "1")
    assert main(["switch"]) == 70
    err = capsys.readouterr().err
    assert "Traceback (most recent call last):" in err
    assert "direct cause" in err
    for leaked in ("REFRESHSECRET", "ACCESSSECRET", "ya29.", "1//"):
        assert leaked not in err


def test_json_error_output_is_valid_and_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from mswap.core.errors import MswapError

    def fake_run(*_args: Any, **_kwargs: Any) -> int:
        raise MswapError(
            "bad ya29.SECRET", hint="client_secret=GOCSPX-abcdefghijklmnopqrstuvwxyz12"
        )

    monkeypatch.setattr(switch_command, "run", fake_run)
    assert main(["switch", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "SECRET" not in json.dumps(payload)


def test_every_background_launcher_keeps_the_current_directory_off_sys_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m mswap` imports ./mswap.py or ./mswap/ from the cwd unless `-P` is given.

    agy fires the hook with a project directory as cwd (possibly a fresh clone), the scheduler
    and the shim run from anywhere, so every launcher must carry `-P`.
    """
    pythonw = tmp_path / "pythonw.exe"
    monkeypatch.setattr(schedule_win, "_find_pythonw", lambda: pythonw)
    commands = [
        hooks._default_command(),
        schedule_win.build_install_argv(5)[schedule_win.build_install_argv(5).index("/TR") + 1],
        schedule_posix.build_systemd_service(python_bin="/usr/bin/python3"),
        shim_text("py.exe"),
        " ".join(
            plistlib.loads(
                schedule_posix.build_macos_plist(5, python_bin="py", data_dir_path=tmp_path)
            )["ProgramArguments"]
        ),
    ]
    for command in commands:
        assert " -P -m mswap" in command, command


def test_python_p_flag_really_blocks_a_module_planted_in_the_cwd(tmp_path: Path) -> None:
    (tmp_path / "mswap.py").write_text("print('PLANTED')\n", encoding="utf-8")
    plain = subprocess.run(
        [sys.executable, "-c", "import mswap; print(mswap.__file__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    safe = subprocess.run(
        [sys.executable, "-P", "-c", "import mswap; print(mswap.__file__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "PLANTED" in plain.stdout
    assert "PLANTED" not in safe.stdout
    assert str(tmp_path) not in safe.stdout


def test_system_tool_prefers_the_system_directory_over_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mswap.util.systools import system_tool

    name = "schtasks" if sys.platform == "win32" else "sh"
    fake = tmp_path / (name + (".exe" if sys.platform == "win32" else ""))
    fake.write_text("planted", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    resolved = Path(system_tool(name))
    assert resolved != fake
    assert resolved.is_absolute()
    assert resolved.parent != tmp_path


def test_system_tool_falls_back_to_the_bare_name_when_missing() -> None:
    from mswap.util.systools import system_tool

    assert system_tool("definitely-not-a-real-tool") == "definitely-not-a-real-tool"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_hooks_install_keeps_the_existing_file_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text("{}", encoding="utf-8")
    hooks_file.chmod(0o600)
    hooks.install(command="echo test")
    assert _mode(hooks_file) == 0o600
