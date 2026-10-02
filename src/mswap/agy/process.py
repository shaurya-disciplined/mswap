"""Antigravity CLI process detection and ancestor tree inspection.

Owns process listing via Toolhelp32 snapshot (Windows) or ps (POSIX),
agy session counting, and ancestor detection to guard self-mutation.
Must never kill, signal, or read process memory.
"""

from __future__ import annotations

import csv
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mswap.util.systools import run_system


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


@dataclass(frozen=True, slots=True)
class AgyProcess:
    """A running Antigravity CLI process."""

    pid: int
    started_at: datetime | None


def _get_k32() -> Any:
    """Return kernel32 DLL handle on Windows, or None on non-Windows."""
    if sys.platform != "win32":
        return None
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return None
    return win_dll("kernel32", use_last_error=True)


def _get_process_started_at_win(pid: int) -> datetime | None:
    """Retrieve process start time via Win32 GetProcessTimes."""
    k32 = _get_k32()
    if k32 is None:
        return None
    try:
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_proc = k32.OpenProcess(0x1000, False, pid)
        if not h_proc:
            return None
        try:
            ct = wt.FILETIME()
            et = wt.FILETIME()
            kt = wt.FILETIME()
            ut = wt.FILETIME()
            if k32.GetProcessTimes(
                h_proc,
                ctypes.byref(ct),
                ctypes.byref(et),
                ctypes.byref(kt),
                ctypes.byref(ut),
            ):
                ft = (ct.dwHighDateTime << 32) + ct.dwLowDateTime
                if ft > 0:
                    epoch_1601 = datetime(1601, 1, 1, tzinfo=UTC)
                    return epoch_1601 + timedelta(microseconds=ft // 10)
        finally:
            k32.CloseHandle(h_proc)
    except Exception:
        return None
    return None


def _snapshot_windows() -> list[tuple[int, int, str]]:
    """Capture process table via Toolhelp32Snapshot on Windows."""
    k32 = _get_k32()
    if k32 is None:
        return _snapshot_tasklist()
    try:
        # TH32CS_SNAPPROCESS = 0x00000002
        h = k32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not h or h == -1 or h == 0xFFFFFFFF:
            return _snapshot_tasklist()
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            res = k32.Process32FirstW(h, ctypes.byref(pe))
            procs: list[tuple[int, int, str]] = []
            while res:
                procs.append(
                    (int(pe.th32ProcessID), int(pe.th32ParentProcessID), str(pe.szExeFile))
                )
                res = k32.Process32NextW(h, ctypes.byref(pe))
            if not procs:
                return _snapshot_tasklist()
            return procs
        finally:
            k32.CloseHandle(h)
    except Exception:
        return _snapshot_tasklist()


def _snapshot_tasklist() -> list[tuple[int, int, str]]:
    """Fallback process snapshot on Windows using tasklist."""
    try:
        res = run_system(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if res.returncode != 0:
            return []

        procs: list[tuple[int, int, str]] = []
        reader = csv.reader(res.stdout.splitlines())
        for row in reader:
            if len(row) >= 2:
                exe = row[0].strip()
                try:
                    pid = int(row[1].strip())
                except ValueError:
                    continue
                procs.append((pid, 0, exe))
        return procs
    except Exception:
        return []


def parse_ps_output(output: str) -> list[tuple[int, int, str]]:
    """Parse `ps -axo pid=,ppid=,comm=` output into (pid, ppid, basename(comm))."""
    procs: list[tuple[int, int, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=2)
        if len(parts) >= 3:
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except ValueError:
                continue
            comm = parts[2].strip()
            # Normalize slashes and extract basename (handles paths, spaces, and long names)
            base_comm = comm.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            procs.append((pid, ppid, base_comm))
    return procs


def _snapshot_posix(
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[tuple[int, int, str]]:
    """Capture process table via ps on POSIX systems."""
    try:
        run_fn = runner or run_system
        res = run_fn(
            ["ps", "-axo", "pid=,ppid=,comm="],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if res.returncode != 0:
            return []
        return parse_ps_output(res.stdout)
    except Exception:
        return []


def snapshot_processes() -> list[tuple[int, int, str]]:
    """Return current process table entries as (pid, ppid, exe_name)."""
    if sys.platform == "win32":
        return _snapshot_windows()
    return _snapshot_posix()


def running_agy(
    *,
    snapshot_fn: Callable[[], Sequence[tuple[int, int, str]]] | None = None,
    time_fn: Callable[[int], datetime | None] | None = None,
) -> list[AgyProcess]:
    """Return all running Antigravity CLI processes."""
    snapshot = snapshot_fn() if snapshot_fn is not None else snapshot_processes()
    get_time = time_fn if time_fn is not None else _get_process_started_at_win

    procs: list[AgyProcess] = []
    for pid, _, exe in snapshot:
        exe_lower = exe.lower()
        exe_name = Path(exe).name.lower()
        if exe_lower in ("agy.exe", "agy") or exe_name in ("agy.exe", "agy"):
            # On POSIX, started_at is None unless time_fn is explicitly provided
            started_at = get_time(pid) if (time_fn is not None or sys.platform == "win32") else None
            procs.append(AgyProcess(pid=pid, started_at=started_at))
    return procs


def agy_running(
    *,
    procs_fn: Callable[[], Sequence[AgyProcess]] | None = None,
) -> bool:
    """Return True if any Antigravity CLI process is currently running."""
    procs = procs_fn() if procs_fn is not None else running_agy()
    return len(procs) > 0


def inside_agy(
    *,
    current_pid: int | None = None,
    snapshot_fn: Callable[[], Sequence[tuple[int, int, str]]] | None = None,
) -> bool:
    """Return True if any ancestor of current process is agy.exe (capped at 64 hops)."""
    cur_pid = current_pid if current_pid is not None else os.getpid()
    snapshot = snapshot_fn() if snapshot_fn is not None else snapshot_processes()

    proc_map: dict[int, tuple[int, str]] = {pid: (ppid, exe) for pid, ppid, exe in snapshot}

    visited: set[int] = set()
    for _ in range(64):
        if cur_pid in visited or cur_pid not in proc_map:
            break
        visited.add(cur_pid)
        ppid, _ = proc_map[cur_pid]
        if ppid == 0 or ppid == cur_pid or ppid not in proc_map:
            break
        _, parent_exe = proc_map[ppid]
        parent_name = Path(parent_exe).name.lower()
        if parent_name in ("agy.exe", "agy"):
            return True
        cur_pid = ppid

    return False
