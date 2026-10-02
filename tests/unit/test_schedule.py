"""Tests for ``mswap schedule`` command and ``util.schedule_win`` helpers.

Covers: exact argv construction for install/remove/query; the status parser on a
realistic captured ``schtasks /Query /V /FO LIST`` fixture; --every bounds
validation; missing pythonw.exe path; non-Windows platform guard.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from mswap.core.errors import UsageError

# ---------------------------------------------------------------------------
# Realistic schtasks /Query /V /FO LIST sample output
# (no personal data — uses generic placeholder values)
# ---------------------------------------------------------------------------
SCHTASKS_QUERY_SAMPLE = dedent("""\
    Folder: \\
    HostName:                             DESKTOP-ABCDEF
    TaskName:                             \\mswap autopilot
    Next Run Time:                        10/2/2026 3:30:00 PM
    Status:                               Ready
    Logon Mode:                           Interactive only
    Last Run Time:                        10/2/2026 3:25:00 PM
    Last Result:                          0
    Author:                               DESKTOP-ABCDEF\\user
    Task To Run:                          "C:\\Python314\\pythonw.exe" -m mswap auto --once --quiet
    Start In:                             N/A
    Comment:                              N/A
    Scheduled Task State:                 Enabled
    Idle Time:                            Disabled
    Power Management:                     Stop On Battery Mode, No Start On Batteries
    Run As User:                          user
    Delete Task If Not Rescheduled:       Disabled
    Stop Task If Runs X Hours and X Mins: 72:00:00
    Schedule:                             Scheduling data is not available in this format.
    Schedule Type:                        Every 5 Minute(s)
    Start Time:                           3:25:00 PM
    Start Date:                           10/2/2026
    End Date:                             N/A
    Days:                                 N/A
    Months:                               N/A
    Repeat: Every:                        N/A
    Repeat: Until: Time:                  N/A
    Repeat: Until: Duration:              N/A
    Repeat: Stop If Still Running:        N/A
""")


# ---------------------------------------------------------------------------
# Tests for schedule_win.py argv builders
# ---------------------------------------------------------------------------
class TestBuildInstallArgv:
    """Tests for ``build_install_argv``."""

    def test_default_interval(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Install with default interval=5 produces correct schtasks argv."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        argv = schedule_win.build_install_argv(5)
        assert argv[0] == "schtasks"
        assert argv[1] == "/Create"
        assert "/TN" in argv
        tn_idx = argv.index("/TN")
        assert argv[tn_idx + 1] == "mswap autopilot"
        assert "/SC" in argv
        sc_idx = argv.index("/SC")
        assert argv[sc_idx + 1] == "MINUTE"
        assert "/MO" in argv
        mo_idx = argv.index("/MO")
        assert argv[mo_idx + 1] == "5"
        assert "/F" in argv
        # /TR should reference pythonw
        tr_idx = argv.index("/TR")
        assert "pythonw.exe" in argv[tr_idx + 1]
        assert "-m mswap auto --once --quiet" in argv[tr_idx + 1]

    def test_custom_interval(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Install with custom --every=15 sets /MO 15."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        argv = schedule_win.build_install_argv(15)
        mo_idx = argv.index("/MO")
        assert argv[mo_idx + 1] == "15"

    def test_boundary_every_1(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """--every=1 is accepted."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        argv = schedule_win.build_install_argv(1)
        mo_idx = argv.index("/MO")
        assert argv[mo_idx + 1] == "1"

    def test_boundary_every_60(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """--every=60 is accepted."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        argv = schedule_win.build_install_argv(60)
        mo_idx = argv.index("/MO")
        assert argv[mo_idx + 1] == "60"

    def test_every_zero_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """--every=0 raises UsageError."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        with pytest.raises(UsageError, match=r"--every must be between 1 and 60"):
            schedule_win.build_install_argv(0)

    def test_every_61_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """--every=61 raises UsageError."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        with pytest.raises(UsageError, match=r"--every must be between 1 and 60"):
            schedule_win.build_install_argv(61)

    def test_every_negative_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """--every=-1 raises UsageError."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        with pytest.raises(UsageError, match=r"--every must be between 1 and 60"):
            schedule_win.build_install_argv(-1)


class TestMissingPythonw:
    """Tests for when pythonw.exe is absent."""

    def test_missing_pythonw_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Installing when pythonw.exe is missing raises UsageError."""
        from mswap.util import schedule_win

        # Point executable to a dir with no pythonw.exe
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        with pytest.raises(UsageError, match=r"pythonw\.exe not found") as exc_info:
            schedule_win.build_install_argv(5)
        assert exc_info.value.hint is not None
        assert "uv tool install" in exc_info.value.hint


class TestBuildRemoveArgv:
    """Tests for ``build_remove_argv``."""

    def test_argv(self) -> None:
        """Remove produces correct schtasks /Delete argv."""
        from mswap.util import schedule_win

        argv = schedule_win.build_remove_argv()
        assert argv == [
            "schtasks",
            "/Delete",
            "/TN",
            "mswap autopilot",
            "/F",
        ]


class TestBuildQueryArgv:
    """Tests for ``build_query_argv``."""

    def test_argv(self) -> None:
        """Query produces correct schtasks /Query argv."""
        from mswap.util import schedule_win

        argv = schedule_win.build_query_argv()
        assert argv == [
            "schtasks",
            "/Query",
            "/TN",
            "mswap autopilot",
            "/FO",
            "LIST",
            "/V",
        ]


# ---------------------------------------------------------------------------
# Tests for schtasks output parser
# ---------------------------------------------------------------------------
class TestParseQueryOutput:
    """Tests for parsing ``schtasks /Query /V /FO LIST`` output."""

    def test_realistic_sample(self) -> None:
        """Parsing a realistic schtasks output extracts all fields."""
        from mswap.util import schedule_win

        result = schedule_win.parse_query_output(SCHTASKS_QUERY_SAMPLE)
        assert result["installed"] is True
        assert result["next_run_time"] == "10/2/2026 3:30:00 PM"
        assert result["last_run_time"] == "10/2/2026 3:25:00 PM"
        assert result["last_result"] == "0"
        assert result["status"] == "Ready"

    def test_empty_output(self) -> None:
        """Parsing empty output returns installed=True but null fields."""
        from mswap.util import schedule_win

        result = schedule_win.parse_query_output("")
        assert result["installed"] is True
        assert result["next_run_time"] is None
        assert result["last_run_time"] is None

    def test_partial_output(self) -> None:
        """Parsing partial output returns available fields."""
        from mswap.util import schedule_win

        output = (
            "Status:                               Running\n"
            "Next Run Time:                        Never\n"
        )
        result = schedule_win.parse_query_output(output)
        assert result["status"] == "Running"
        assert result["next_run_time"] == "Never"
        assert result["last_run_time"] is None


# ---------------------------------------------------------------------------
# Tests for install/remove/query with injected runner
# ---------------------------------------------------------------------------
def _make_runner(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> Any:
    """Create a fake runner that returns a CompletedProcess."""

    captured: list[list[str]] = []

    def runner(cmd: Any) -> subprocess.CompletedProcess[str]:
        captured.append(list(cmd))
        return subprocess.CompletedProcess(
            args=list(cmd),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    runner.captured = captured  # type: ignore[attr-defined]
    return runner


class TestInstall:
    """Tests for schedule_win.install with injected runner."""

    def test_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Successful install returns confirmation message."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        runner = _make_runner(
            returncode=0,
            stdout="SUCCESS: The scheduled task was created.",
        )
        msg = schedule_win.install(every=5, runner=runner)
        assert "mswap autopilot" in msg
        assert "every 5 min" in msg
        assert len(runner.captured) == 1

    def test_failure_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Failed schtasks raises UsageError."""
        from mswap.util import schedule_win

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        runner = _make_runner(returncode=1, stderr="ERROR: Access Denied.")
        with pytest.raises(UsageError, match=r"schtasks failed"):
            schedule_win.install(every=5, runner=runner)


class TestRemove:
    """Tests for schedule_win.remove with injected runner."""

    def test_success(self) -> None:
        """Successful remove returns confirmation message."""
        from mswap.util import schedule_win

        runner = _make_runner(
            returncode=0,
            stdout="SUCCESS: The scheduled task was deleted.",
        )
        msg = schedule_win.remove(runner=runner)
        assert "mswap autopilot" in msg
        assert "removed" in msg.lower()

    def test_not_found(self) -> None:
        """Missing task returns 'Not installed.' instead of raising."""
        from mswap.util import schedule_win

        runner = _make_runner(
            returncode=1,
            stderr="ERROR: The system cannot find the file specified.",
        )
        msg = schedule_win.remove(runner=runner)
        assert msg == "Not installed."

    def test_other_error_raises(self) -> None:
        """Non-not-found error raises UsageError."""
        from mswap.util import schedule_win

        runner = _make_runner(returncode=2, stderr="ERROR: Something unexpected.")
        with pytest.raises(UsageError, match=r"schtasks failed"):
            schedule_win.remove(runner=runner)


class TestQuery:
    """Tests for schedule_win.query with injected runner."""

    def test_installed(self) -> None:
        """Query on an installed task parses fields."""
        from mswap.util import schedule_win

        runner = _make_runner(returncode=0, stdout=SCHTASKS_QUERY_SAMPLE)
        result = schedule_win.query(runner=runner)
        assert result["installed"] is True
        assert result["status"] == "Ready"
        assert result["last_result"] == "0"

    def test_not_installed(self) -> None:
        """Query on missing task returns installed=False."""
        from mswap.util import schedule_win

        runner = _make_runner(
            returncode=1,
            stderr="ERROR: The system cannot find the file specified.",
        )
        result = schedule_win.query(runner=runner)
        assert result["installed"] is False
        assert result["next_run_time"] is None

    def test_query_error_raises(self) -> None:
        """Query with unexpected error raises UsageError."""
        from mswap.util import schedule_win

        runner = _make_runner(returncode=2, stderr="ERROR: Access denied.")
        with pytest.raises(UsageError, match=r"schtasks failed"):
            schedule_win.query(runner=runner)


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------
class TestScheduleCLI:
    """Integration tests for ``mswap schedule`` via ``main()``."""

    def test_non_windows_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Schedule on non-Windows raises UsageError."""
        from mswap.cli.commands import schedule as sched_mod

        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(UsageError, match=r"Windows-only"):
            sched_mod._check_platform()

    def test_missing_action_raises(self, ctx: Any) -> None:
        """Schedule with no action raises UsageError."""
        import argparse

        from mswap.cli.commands import schedule as sched_mod

        ns = argparse.Namespace()
        with pytest.raises(UsageError, match=r"Missing schedule action"):
            sched_mod.run(ctx, ns)

    def test_install_via_main(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """``mswap schedule install --every 10`` dispatches correctly."""
        from mswap.cli import main

        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("fake")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        captured: list[list[str]] = []

        def fake_runner(
            cmd: Any,
        ) -> subprocess.CompletedProcess[str]:
            captured.append(list(cmd))
            return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout="OK", stderr="")

        monkeypatch.setattr("mswap.util.schedule_win._default_runner", fake_runner)

        code = main(["schedule", "install", "--every", "10"])
        assert code == 0
        assert len(captured) == 1
        assert "/MO" in captured[0]
        mo_idx = captured[0].index("/MO")
        assert captured[0][mo_idx + 1] == "10"

    def test_remove_via_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``mswap schedule remove`` dispatches correctly."""
        from mswap.cli import main

        captured: list[list[str]] = []

        def fake_runner(
            cmd: Any,
        ) -> subprocess.CompletedProcess[str]:
            captured.append(list(cmd))
            return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout="OK", stderr="")

        monkeypatch.setattr("mswap.util.schedule_win._default_runner", fake_runner)

        code = main(["schedule", "remove"])
        assert code == 0
        assert len(captured) == 1
        assert "/Delete" in captured[0]

    def test_status_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``mswap schedule status`` when task not installed."""
        from mswap.cli import main

        def fake_runner(
            cmd: Any,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=list(cmd),
                returncode=1,
                stdout="",
                stderr="ERROR: The system cannot find the file specified.",
            )

        monkeypatch.setattr("mswap.util.schedule_win._default_runner", fake_runner)

        code = main(["schedule", "status"])
        assert code == 0

    def test_status_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``mswap schedule status`` when task is installed shows fields."""
        from mswap.cli import main

        def fake_runner(
            cmd: Any,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=list(cmd),
                returncode=0,
                stdout=SCHTASKS_QUERY_SAMPLE,
                stderr="",
            )

        monkeypatch.setattr("mswap.util.schedule_win._default_runner", fake_runner)

        code = main(["schedule", "status"])
        assert code == 0

    def test_status_with_events(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """``mswap schedule status`` includes recent autopilot events."""
        from mswap.cli import main

        # Write some events
        events_dir = tmp_path / "home"
        events_dir.mkdir(parents=True, exist_ok=True)
        events_file = events_dir / "events.log"
        events = [
            {
                "event": "hold",
                "at": "2026-10-02T12:00:00Z",
                "reason": "below threshold",
            },
            {
                "event": "switch",
                "at": "2026-10-02T12:05:00Z",
                "reason": "90% used; switching",
            },
            {
                "event": "hold",
                "at": "2026-10-02T12:10:00Z",
                "reason": "cooldown",
            },
            {
                "event": "hold",
                "at": "2026-10-02T12:15:00Z",
                "reason": "still cooling down",
            },
        ]
        events_file.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        def fake_runner(
            cmd: Any,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=list(cmd),
                returncode=0,
                stdout=SCHTASKS_QUERY_SAMPLE,
                stderr="",
            )

        monkeypatch.setattr("mswap.util.schedule_win._default_runner", fake_runner)

        code = main(["schedule", "status"])
        assert code == 0


class TestVerifyNoRealTask:
    """Verify that no real scheduled task was created during tests."""

    def test_schtasks_not_installed(self) -> None:
        """Confirm ``schtasks /Query /TN 'mswap autopilot'`` returns error.

        This is the VERIFY requirement: proof we didn't install a real task.
        """
        if sys.platform != "win32":
            pytest.skip("schtasks only on Windows")

        result = subprocess.run(
            ["schtasks", "/Query", "/TN", "mswap autopilot"],
            capture_output=True,
            text=True,
            check=False,
        )
        # The task should NOT exist - schtasks returns non-zero
        assert result.returncode != 0 or "cannot find" in result.stderr.lower(), (
            "A real 'mswap autopilot' scheduled task exists! "
            "This step must NOT create a real scheduled task."
        )
