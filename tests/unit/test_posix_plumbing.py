"""Tests for POSIX paths, agy_exe discovery, process detection, and launchd/systemd scheduling.

Covers:
- Path resolution per platform (macOS Application Support, Linux XDG_DATA_HOME, ~/.local/share)
- Mode 0700 creation on POSIX
- agy_exe resolution on POSIX (MSWAP_AGY_EXE -> which -> candidates -> AgyNotFound)
- Symlink resolution before hashing exe_sig
- ps parsing on POSIX (pid, ppid, basename(comm), spaces in comm, very long paths)
- inside_agy ancestor tree walking on POSIX
- macOS launchd plist generation, atomic write, argv for launchctl bootstrap/bootout/print
- Linux systemd service & timer generation, ExecStart quoting with spaces, systemctl argv
- Platform-routed schedule install/remove/status on macOS and Linux
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mswap.agy.paths import agy_exe, data_dir, ensure_data_dir
from mswap.agy.process import (
    inside_agy,
    parse_ps_output,
    running_agy,
)
from mswap.core.errors import AgyNotFound, UsageError
from mswap.util import schedule_posix


# ---------------------------------------------------------------------------
# Part 1: Paths and agy_exe resolution
# ---------------------------------------------------------------------------
class TestPosixPaths:
    """Tests for platform data_dir, ensure_data_dir, and agy_exe resolution."""

    def test_data_dir_darwin(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """macOS data dir resolves to ~/Library/Application Support/mswap."""
        monkeypatch.delenv("MSWAP_HOME", raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        expected = tmp_path / "Library" / "Application Support" / "mswap"
        assert data_dir() == expected

    def test_data_dir_linux_with_xdg(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Linux data dir uses XDG_DATA_HOME when set."""
        monkeypatch.delenv("MSWAP_HOME", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        xdg = tmp_path / "custom_xdg"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg))

        expected = xdg / "mswap"
        assert data_dir() == expected

    def test_data_dir_linux_without_xdg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Linux data dir falls back to ~/.local/share/mswap when XDG_DATA_HOME unset."""
        monkeypatch.delenv("MSWAP_HOME", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        expected = tmp_path / ".local" / "share" / "mswap"
        assert data_dir() == expected

    def test_ensure_data_dir_creates_with_0700_on_posix(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ensure_data_dir creates dir and applies chmod 0700 on POSIX."""
        target = tmp_path / "test_data"
        chmod_calls: list[int] = []

        monkeypatch.setattr(sys, "platform", "linux")
        orig_chmod = Path.chmod

        def fake_chmod(p: Path, mode: int) -> None:
            chmod_calls.append(mode)
            orig_chmod(p, mode)

        monkeypatch.setattr(Path, "chmod", fake_chmod)

        res = ensure_data_dir(target)
        assert res == target
        assert target.is_dir()
        if sys.platform != "win32":
            assert 0o700 in chmod_calls

    def test_agy_exe_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """MSWAP_AGY_EXE environment variable overrides all candidate resolution."""
        custom_bin = tmp_path / "my_custom_agy"
        monkeypatch.setenv("MSWAP_AGY_EXE", str(custom_bin))
        monkeypatch.setattr(sys, "platform", "linux")

        assert agy_exe() == custom_bin

    def test_agy_exe_posix_which(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """shutil.which('agy') is preferred on POSIX when in PATH."""
        monkeypatch.delenv("MSWAP_AGY_EXE", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        which_exe = tmp_path / "bin" / "agy"
        monkeypatch.setattr("shutil.which", lambda cmd: str(which_exe) if cmd == "agy" else None)

        assert agy_exe() == which_exe

    def test_agy_exe_posix_candidates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """First existing candidate is selected if which('agy') is not found."""
        monkeypatch.delenv("MSWAP_AGY_EXE", raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        # Pretend home is tmp_path
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        local_bin = tmp_path / ".local" / "bin" / "agy"
        local_bin.parent.mkdir(parents=True, exist_ok=True)
        local_bin.write_text("fake binary")

        assert agy_exe() == local_bin

    def test_agy_exe_posix_not_found_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """AgyNotFound is raised on POSIX when binary cannot be located."""
        monkeypatch.delenv("MSWAP_AGY_EXE", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with pytest.raises(AgyNotFound, match="not found"):
            agy_exe()

    def test_exe_sig_resolves_symlinks(self, tmp_path: Path) -> None:
        """_exe_sig resolves symlinks before calculating size and mtime."""
        from unittest.mock import MagicMock

        from mswap.agy.client_discovery import _exe_sig as disc_sig
        from mswap.agy.install import _exe_sig as inst_sig

        real_bin = tmp_path / "real_agy"
        real_bin.write_text("antigravity-cli-content")

        try:
            symlink_bin = tmp_path / "symlink_agy"
            symlink_bin.symlink_to(real_bin)
            sig_real = disc_sig(real_bin)
            sig_sym = disc_sig(symlink_bin)
            assert sig_real != ""
            assert sig_real == sig_sym

            inst_real = inst_sig(real_bin)
            inst_sym = inst_sig(symlink_bin)
            assert inst_real == inst_sym
        except (OSError, NotImplementedError):
            # When OS restricts symlink creation (e.g. Windows non-developer mode),
            # verify resolve() is invoked on the Path object.
            mock_path = MagicMock(spec=Path)
            mock_resolved = MagicMock(spec=Path)
            mock_path.resolve.return_value = mock_resolved
            mock_stat = MagicMock()
            mock_stat.st_size = 1234
            mock_stat.st_mtime = 5678.0
            mock_resolved.stat.return_value = mock_stat

            assert disc_sig(mock_path) == "1234:5678"
            assert inst_sig(mock_path) == "1234:5678"
            mock_path.resolve.assert_called()


# ---------------------------------------------------------------------------
# Part 2: POSIX Process Detection
# ---------------------------------------------------------------------------
class TestPosixProcessDetection:
    """Tests for ps output parsing, running_agy, and inside_agy on POSIX."""

    def test_parse_ps_output_standard(self) -> None:
        """Standard lines with pid, ppid, comm are parsed into (pid, ppid, basename)."""
        sample = "    1     0 /sbin/launchd\n  100     1 /usr/local/bin/agy\n  200   100 bash\n"
        procs = parse_ps_output(sample)
        assert procs == [
            (1, 0, "launchd"),
            (100, 1, "agy"),
            (200, 100, "bash"),
        ]

    def test_parse_ps_output_comm_with_spaces(self) -> None:
        """comm with spaces is parsed cleanly without losing remainder."""
        sample = (
            "  501     1 /Applications/Antigravity CLI/agy CLI\n"
            "  502   501 /usr/bin/my tool with spaces\n"
        )
        procs = parse_ps_output(sample)
        assert procs == [
            (501, 1, "agy CLI"),
            (502, 501, "my tool with spaces"),
        ]

    def test_parse_ps_output_comm_very_long_path(self) -> None:
        """Very long paths (>200 chars) correctly extract basename."""
        long_path = "/" + "/".join(["subfolder"] * 25) + "/special_agy_binary"
        sample = f"  999     1 {long_path}\n"
        procs = parse_ps_output(sample)
        assert procs == [(999, 1, "special_agy_binary")]

    def test_parse_ps_output_skips_malformed_and_headers(self) -> None:
        """Headers and malformed lines are skipped without error."""
        sample = (
            "  PID  PPID COMMAND\n"
            "\n"
            "  invalid line here\n"
            "  123  not_an_int  command\n"
            "  456   123\n"  # missing comm
            "  789   123 valid_comm\n"
        )
        procs = parse_ps_output(sample)
        assert procs == [(789, 123, "valid_comm")]

    def test_running_agy_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """running_agy identifies agy and sets started_at=None on POSIX."""
        monkeypatch.setattr(sys, "platform", "darwin")

        fake_snapshot = [
            (10, 1, "launchd"),
            (50, 10, "agy"),
            (60, 50, "python"),
            (70, 10, "agy.exe"),
            (80, 10, "other_agy"),  # not an exact match
        ]
        procs = running_agy(snapshot_fn=lambda: fake_snapshot)
        assert len(procs) == 2
        pids = [p.pid for p in procs]
        assert 50 in pids
        assert 70 in pids
        # started_at must be None on POSIX without explicit time_fn
        for p in procs:
            assert p.started_at is None

    def test_inside_agy_posix(self) -> None:
        """inside_agy climbs parent chain to find agy process."""
        snapshot = [
            (1, 0, "init"),
            (100, 1, "agy"),
            (200, 100, "bash"),
            (300, 200, "python"),
            (400, 1, "systemd"),
            (500, 400, "isolated_service"),
        ]

        # 300 has ancestor 100 ('agy')
        assert inside_agy(current_pid=300, snapshot_fn=lambda: snapshot) is True
        # 200 has parent 100 ('agy')
        assert inside_agy(current_pid=200, snapshot_fn=lambda: snapshot) is True
        # 500 does not have agy in ancestor chain
        assert inside_agy(current_pid=500, snapshot_fn=lambda: snapshot) is False


# ---------------------------------------------------------------------------
# Part 3: macOS launchd Scheduling
# ---------------------------------------------------------------------------
class TestSchedulePosixMac:
    """Tests for macOS launchd plist generation and launchctl argv."""

    def test_build_macos_plist_bounds(self) -> None:
        """--every must be between 1 and 60."""
        with pytest.raises(UsageError, match="between 1 and 60"):
            schedule_posix.build_macos_plist(0)
        with pytest.raises(UsageError, match="between 1 and 60"):
            schedule_posix.build_macos_plist(61)

    def test_build_macos_plist_content_roundtrip(self, tmp_path: Path) -> None:
        """plist content round-trips through plistlib and matches spec."""
        plist_bytes = schedule_posix.build_macos_plist(
            every=15,
            python_bin="/usr/local/bin/python3",
            data_dir_path=tmp_path / "mswap_data",
        )
        parsed = plistlib.loads(plist_bytes)

        assert parsed["Label"] == "dev.mswap.autopilot"
        assert parsed["ProgramArguments"] == [
            "/usr/local/bin/python3",
            "-m",
            "mswap",
            "auto",
            "--once",
            "--quiet",
        ]
        assert parsed["StartInterval"] == 15 * 60
        assert parsed["RunAtLoad"] is False
        assert parsed["StandardOutPath"] == str(tmp_path / "mswap_data" / "autopilot.out")
        assert parsed["StandardErrorPath"] == str(tmp_path / "mswap_data" / "autopilot.err")

    def test_install_macos_argv_and_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """install_macos writes plist atomically and invokes bootout + bootstrap."""
        commands: list[list[str]] = []

        def fake_runner(cmd: Any) -> subprocess.CompletedProcess[str]:
            commands.append(list(cmd))
            return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout="", stderr="")

        plist_file = tmp_path / "LaunchAgents" / "dev.mswap.autopilot.plist"
        monkeypatch.setattr(schedule_posix, "_get_uid", lambda: 502)

        msg = schedule_posix.install_macos(
            every=10,
            runner=fake_runner,
            plist_dest=plist_file,
            python_bin="/usr/bin/python3",
            data_dir_path=tmp_path / "mswap",
        )

        assert "created (every 10 min)" in msg
        assert plist_file.is_file()

        # Commands: bootout then bootstrap
        assert len(commands) == 2
        assert commands[0] == ["launchctl", "bootout", "gui/502/dev.mswap.autopilot"]
        assert commands[1] == [
            "launchctl",
            "bootstrap",
            "gui/502",
            str(plist_file),
        ]

    def test_remove_macos_argv(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """remove_macos boots out target and unlinks plist file."""
        commands: list[list[str]] = []

        def fake_runner(cmd: Any) -> subprocess.CompletedProcess[str]:
            commands.append(list(cmd))
            return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout="", stderr="")

        plist_file = tmp_path / "dev.mswap.autopilot.plist"
        plist_file.write_text("dummy")
        monkeypatch.setattr(schedule_posix, "_get_uid", lambda: 502)

        msg = schedule_posix.remove_macos(runner=fake_runner, plist_dest=plist_file)
        assert "removed" in msg
        assert not plist_file.exists()
        assert commands == [["launchctl", "bootout", "gui/502/dev.mswap.autopilot"]]

    def test_remove_macos_not_installed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """remove_macos returns 'Not installed.' when file missing and bootout fails."""
        plist_file = tmp_path / "nonexistent.plist"
        monkeypatch.setattr(schedule_posix, "_get_uid", lambda: 502)

        def failing_runner(cmd: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=list(cmd), returncode=3, stdout="", stderr="Could not find"
            )

        msg = schedule_posix.remove_macos(runner=failing_runner, plist_dest=plist_file)
        assert msg == "Not installed."

    def test_parse_launchctl_print(self) -> None:
        """parse_launchctl_print extracts state and last exit code."""
        sample_output = (
            "gui/501/dev.mswap.autopilot = {\n"
            "    active count = 0\n"
            "    state = waiting\n"
            "    program = /usr/local/bin/python3\n"
            "    last exit code = 0\n"
            "}\n"
        )
        status = schedule_posix.parse_launchctl_print(sample_output)
        assert status["installed"] is True
        assert status["status"] == "waiting"
        assert status["last_result"] == "0"
        assert status["next_run_time"] is None


# ---------------------------------------------------------------------------
# Part 4: Linux systemd Scheduling
# ---------------------------------------------------------------------------
class TestSchedulePosixLinux:
    """Tests for Linux systemd unit generation, quoting, and systemctl argv."""

    def test_build_systemd_timer_bounds(self) -> None:
        """--every must be between 1 and 60."""
        with pytest.raises(UsageError, match="between 1 and 60"):
            schedule_posix.build_systemd_timer(0)
        with pytest.raises(UsageError, match="between 1 and 60"):
            schedule_posix.build_systemd_timer(61)

    def test_build_systemd_service_text(self) -> None:
        """build_systemd_service generates oneshot service with ExecStart."""
        svc = schedule_posix.build_systemd_service(python_bin="/usr/bin/python3")
        assert "Type=oneshot" in svc
        assert "ExecStart=/usr/bin/python3 -m mswap auto --once --quiet" in svc

    def test_build_systemd_service_quotes_spaces(self) -> None:
        """ExecStart quotes python executable containing spaces."""
        svc = schedule_posix.build_systemd_service(python_bin="/home/user/my venv/bin/python")
        assert 'ExecStart="/home/user/my venv/bin/python" -m mswap auto --once --quiet' in svc

    def test_build_systemd_timer_text(self) -> None:
        """build_systemd_timer configures OnBootSec=2min and OnUnitActiveSec={every}min."""
        timer = schedule_posix.build_systemd_timer(every=7)
        assert "OnBootSec=2min" in timer
        assert "OnUnitActiveSec=7min" in timer
        assert "Unit=mswap-autopilot.service" in timer
        assert "WantedBy=timers.target" in timer

    def test_install_linux_argv_and_files(self, tmp_path: Path) -> None:
        """install_linux writes unit files atomically and runs daemon-reload + enable."""
        commands: list[list[str]] = []

        def fake_runner(cmd: Any) -> subprocess.CompletedProcess[str]:
            commands.append(list(cmd))
            return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout="", stderr="")

        user_dir = tmp_path / "systemd" / "user"
        msg = schedule_posix.install_linux(
            every=5,
            runner=fake_runner,
            user_dir=user_dir,
            python_bin="/usr/bin/python3",
        )

        assert "created (every 5 min)" in msg
        svc_path = user_dir / "mswap-autopilot.service"
        timer_path = user_dir / "mswap-autopilot.timer"
        assert svc_path.is_file()
        assert timer_path.is_file()

        assert commands == [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "mswap-autopilot.timer"],
        ]

    def test_remove_linux_argv_and_cleanup(self, tmp_path: Path) -> None:
        """remove_linux disables timer, deletes files, and reloads daemon."""
        commands: list[list[str]] = []

        def fake_runner(cmd: Any) -> subprocess.CompletedProcess[str]:
            commands.append(list(cmd))
            return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout="", stderr="")

        user_dir = tmp_path / "systemd" / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        svc_path = user_dir / "mswap-autopilot.service"
        timer_path = user_dir / "mswap-autopilot.timer"
        svc_path.write_text("svc")
        timer_path.write_text("timer")

        msg = schedule_posix.remove_linux(runner=fake_runner, user_dir=user_dir)
        assert "removed" in msg
        assert not svc_path.exists()
        assert not timer_path.exists()

        assert commands == [
            ["systemctl", "--user", "disable", "--now", "mswap-autopilot.timer"],
            ["systemctl", "--user", "daemon-reload"],
        ]

    def test_parse_systemd_timers_output(self) -> None:
        """parse_systemd_timers_output parses active timer, next run, and last run."""
        sample = (
            "NEXT LEFT LAST PASSED UNIT ACTIVATES\n"
            "Fri 2026-10-02 18:00:00 UTC 4min left Fri 2026-10-02 17:55:00 UTC 55s ago "
            "mswap-autopilot.timer mswap-autopilot.service\n"
            "\n"
            "1 timers listed.\n"
        )
        status = schedule_posix.parse_systemd_timers_output(sample)
        assert status["installed"] is True
        assert status["status"] == "active"
        assert status["next_run_time"] == "Fri 2026-10-02 18:00:00 UTC"
        assert status["last_run_time"] == "Fri 2026-10-02 17:55:00 UTC"

    def test_parse_systemd_timers_output_not_installed(self) -> None:
        """parse_systemd_timers_output reports installed=False when 0 timers listed."""
        sample = "0 timers listed.\n"
        status = schedule_posix.parse_systemd_timers_output(sample)
        assert status["installed"] is False


# ---------------------------------------------------------------------------
# Part 5: Schedule Platform Routing and CLI Integration
# ---------------------------------------------------------------------------
class TestSchedulePlatformRouting:
    """Tests for platform dispatching in mswap schedule command."""

    def test_cli_install_macos(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """mswap schedule install on macOS routes to schedule_posix install_macos."""
        from mswap.cli import main

        monkeypatch.setattr(sys, "platform", "darwin")

        def fake_install(every: int = 5, **kwargs: Any) -> str:
            return f"Scheduled task 'mswap autopilot' created (every {every} min)."

        monkeypatch.setattr(schedule_posix, "install_macos", fake_install)

        code = main(["schedule", "install", "--every", "15"])
        assert code == 0
        out = capsys.readouterr().out
        assert "created (every 15 min)" in out

    def test_cli_install_linux(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """mswap schedule install on Linux routes to schedule_posix install_linux."""
        from mswap.cli import main

        monkeypatch.setattr(sys, "platform", "linux")

        def fake_install(every: int = 5, **kwargs: Any) -> str:
            return f"Scheduled task 'mswap autopilot' created (every {every} min)."

        monkeypatch.setattr(schedule_posix, "install_linux", fake_install)

        code = main(["schedule", "install", "--every", "20"])
        assert code == 0
        out = capsys.readouterr().out
        assert "created (every 20 min)" in out

    def test_cli_status_macos(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """mswap schedule status on macOS prints launchd status."""
        from mswap.cli import main

        monkeypatch.setattr(sys, "platform", "darwin")

        def fake_query(**kwargs: Any) -> schedule_posix.TaskStatus:
            return schedule_posix.TaskStatus(
                installed=True,
                next_run_time=None,
                last_run_time=None,
                last_result="0",
                status="waiting",
            )

        monkeypatch.setattr(schedule_posix, "query_macos", fake_query)

        code = main(["schedule", "status"])
        assert code == 0
        out = capsys.readouterr().out
        assert 'Scheduled task "mswap autopilot":' in out
        assert "Status:        waiting" in out
        assert "Last result:   0" in out
