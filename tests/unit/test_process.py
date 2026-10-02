"""Unit tests for Antigravity process detection, snapshot parsing, and ancestor walks."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

import pytest

from mswap.agy.process import (
    AgyProcess,
    agy_running,
    inside_agy,
    running_agy,
)


def test_running_agy_with_fake_snapshot() -> None:
    fake_snapshot: list[tuple[int, int, str]] = [
        (100, 1, "code.exe"),
        (101, 1, "AGY.EXE"),
        (102, 1, "python.exe"),
        (103, 1, "agy.exe"),
        (104, 1, "agy"),
    ]
    t0 = datetime(2026, 10, 2, 10, 0, 0, tzinfo=UTC)

    def fake_time(pid: int) -> datetime | None:
        if pid == 101:
            return t0
        return None

    procs = running_agy(snapshot_fn=lambda: fake_snapshot, time_fn=fake_time)
    assert len(procs) == 3
    assert procs[0] == AgyProcess(pid=101, started_at=t0)
    assert procs[1] == AgyProcess(pid=103, started_at=None)
    assert procs[2] == AgyProcess(pid=104, started_at=None)


def test_agy_running_helper() -> None:
    assert agy_running(procs_fn=lambda: []) is False
    assert agy_running(procs_fn=lambda: [AgyProcess(pid=123, started_at=None)]) is True


def test_inside_agy_direct_parent() -> None:
    fake_snapshot: list[tuple[int, int, str]] = [
        (500, 400, "mswap.exe"),
        (400, 100, "agy.exe"),
        (100, 1, "explorer.exe"),
    ]
    assert inside_agy(current_pid=500, snapshot_fn=lambda: fake_snapshot) is True


def test_inside_agy_distant_ancestor() -> None:
    fake_snapshot: list[tuple[int, int, str]] = [
        (503, 502, "python.exe"),
        (502, 501, "powershell.exe"),
        (501, 500, "cmd.exe"),
        (500, 100, "agy.exe"),
        (100, 1, "explorer.exe"),
    ]
    assert inside_agy(current_pid=503, snapshot_fn=lambda: fake_snapshot) is True


def test_inside_agy_not_inside() -> None:
    fake_snapshot: list[tuple[int, int, str]] = [
        (503, 502, "python.exe"),
        (502, 501, "powershell.exe"),
        (501, 1, "explorer.exe"),
    ]
    assert inside_agy(current_pid=503, snapshot_fn=lambda: fake_snapshot) is False


def test_inside_agy_cycle_handling() -> None:
    # 501 -> 502 -> 501 loop, neither is agy.exe
    fake_snapshot: list[tuple[int, int, str]] = [
        (503, 502, "python.exe"),
        (502, 501, "cmd.exe"),
        (501, 502, "powershell.exe"),
    ]
    assert inside_agy(current_pid=503, snapshot_fn=lambda: fake_snapshot) is False


def test_inside_agy_self_loop() -> None:
    fake_snapshot: list[tuple[int, int, str]] = [
        (500, 500, "system.exe"),
    ]
    assert inside_agy(current_pid=500, snapshot_fn=lambda: fake_snapshot) is False


def test_inside_agy_missing_parent_pid() -> None:
    fake_snapshot: list[tuple[int, int, str]] = [
        (500, 999, "python.exe"),  # 999 not in snapshot
    ]
    assert inside_agy(current_pid=500, snapshot_fn=lambda: fake_snapshot) is False


def test_inside_agy_64_hop_cap() -> None:
    # Build a linear chain: 1 -> 2 -> 3 -> ... -> 70
    # agy at hop 50 should be found (<= 64)
    # agy at hop 66 should NOT be found (> 64)
    chain_50: list[tuple[int, int, str]] = []
    for i in range(1, 70):
        exe = "agy.exe" if i == 50 else f"proc_{i}.exe"
        chain_50.append((i, i + 1, exe))
    chain_50.append((70, 0, "root.exe"))

    assert inside_agy(current_pid=1, snapshot_fn=lambda: chain_50) is True

    chain_66: list[tuple[int, int, str]] = []
    for i in range(1, 70):
        exe = "agy.exe" if i == 66 else f"proc_{i}.exe"
        chain_66.append((i, i + 1, exe))
    chain_66.append((70, 0, "root.exe"))

    assert inside_agy(current_pid=1, snapshot_fn=lambda: chain_66) is False


def test_snapshot_tasklist_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from mswap.agy.process import _snapshot_tasklist

    sample_output = (
        '"Image Name","PID","Session Name","Session#","Mem Usage"\n'
        '"System Idle Process","0","Services","0","8 K"\n'
        '"agy.exe","12020","Console","2","1,60,960 K"\n'
        '"invalid","not_a_num","Console","2","1 K"\n'
    )

    class FakeCompleted:
        returncode = 0
        stdout = sample_output

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeCompleted())
    procs = _snapshot_tasklist()
    assert (0, 0, "System Idle Process") in procs
    assert (12020, 0, "agy.exe") in procs
    assert len(procs) == 2


def test_snapshot_posix_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from mswap.agy.process import _snapshot_posix

    sample_output = "  123   100  python\n  456   123  agy\n  not_num  123 bad\n"

    class FakeCompleted:
        returncode = 0
        stdout = sample_output

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeCompleted())
    procs = _snapshot_posix()
    assert len(procs) == 2
    assert procs[0] == (123, 100, "python")
    assert procs[1] == (456, 123, "agy")


def test_snapshot_tasklist_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from mswap.agy.process import _snapshot_tasklist

    class FakeFailed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeFailed())
    assert _snapshot_tasklist() == []

    def fail_run(*args: Any, **kwargs: Any) -> Any:
        raise OSError("tasklist missing")

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert _snapshot_tasklist() == []


def test_snapshot_posix_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from mswap.agy.process import _snapshot_posix

    class FakeFailed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeFailed())
    assert _snapshot_posix() == []

    def fail_run(*args: Any, **kwargs: Any) -> Any:
        raise OSError("ps missing")

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert _snapshot_posix() == []


def test_snapshot_windows_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import mswap.agy.process as proc_mod

    monkeypatch.setattr(proc_mod, "_snapshot_tasklist", lambda: [(999, 0, "agy.exe")])
    monkeypatch.setattr(proc_mod, "_get_k32", lambda: None)
    res = proc_mod._snapshot_windows()
    assert res == [(999, 0, "agy.exe")]

    # Also test when CreateToolhelp32Snapshot fails or returns -1
    class FakeK32:
        def CreateToolhelp32Snapshot(self, *args: Any) -> int:
            return -1

    monkeypatch.setattr(proc_mod, "_get_k32", lambda: FakeK32())
    res2 = proc_mod._snapshot_windows()
    assert res2 == [(999, 0, "agy.exe")]


def test_get_process_started_at_win_invalid_pid() -> None:
    from mswap.agy.process import _get_process_started_at_win

    # Non-existent PID
    assert _get_process_started_at_win(999999999) is None


def test_snapshot_processes_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    import mswap.agy.process as proc_mod

    monkeypatch.setattr(proc_mod.sys, "platform", "linux")
    monkeypatch.setattr(proc_mod, "_snapshot_posix", lambda: [(10, 1, "agy")])
    assert proc_mod.snapshot_processes() == [(10, 1, "agy")]


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_real_running_agy_windows() -> None:
    """Windows-only test calling real running_agy() via Toolhelp32."""
    procs = running_agy()
    assert isinstance(procs, list)
    for p in procs:
        assert isinstance(p.pid, int)
        assert p.pid > 0
        if p.started_at is not None:
            assert isinstance(p.started_at, datetime)
