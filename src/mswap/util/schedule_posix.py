"""macOS launchd and Linux systemd user scheduling helpers for mswap autopilot.

Provides install/remove/status operations for:
- macOS: launchd agent via ``~/Library/LaunchAgents/dev.mswap.autopilot.plist``
  and ``launchctl bootstrap/bootout/print``.
- Linux: systemd user units via ``~/.config/systemd/user/mswap-autopilot.{service,timer}``
  and ``systemctl --user daemon-reload/enable/disable/list-timers``.

Never uses ``shell=True``. All file writes are atomic.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from mswap.core.errors import UsageError

TASK_NAME = "mswap autopilot"
MACOS_LABEL = "dev.mswap.autopilot"
SYSTEMD_SERVICE_NAME = "mswap-autopilot.service"
SYSTEMD_TIMER_NAME = "mswap-autopilot.timer"


class TaskStatus(TypedDict):
    """Parsed status fields for scheduled background task."""

    installed: bool
    next_run_time: str | None
    last_run_time: str | None
    last_result: str | None
    status: str | None


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, capturing stdout/stderr as text."""
    return subprocess.run(  # noqa: S603 - static validated commands
        list(cmd),
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_runner(runner: Runner | None) -> Runner:
    """Resolve runner, looking up module-level ``_default_runner`` at call time."""
    if runner is not None:
        return runner
    return _default_runner


def _get_uid() -> int:
    """Return user UID for launchd gui/$UID domain."""
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return int(getuid())
    uid_str = os.environ.get("UID")
    if uid_str:
        try:
            return int(uid_str)
        except ValueError:
            pass
    return 501


def _atomic_write(path: Path, content: bytes | str) -> None:
    """Write content to path atomically via temporary sibling file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f".tmp.{uuid4().hex[:8]}")
    if isinstance(content, bytes):
        tmp_path.write_bytes(content)
    else:
        tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


# ---------------------------------------------------------------------------
# macOS launchd helpers
# ---------------------------------------------------------------------------


def _default_data_dir() -> Path:
    """Return platform data directory path for schedule logs."""
    if "MSWAP_HOME" in os.environ:
        return Path(os.environ["MSWAP_HOME"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mswap"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "mswap"
    return Path.home() / ".local" / "share" / "mswap"


def macos_plist_path(home: Path | None = None) -> Path:
    """Return path to launchd plist file in ~/Library/LaunchAgents."""
    base = home or Path.home()
    return base / "Library" / "LaunchAgents" / f"{MACOS_LABEL}.plist"


def build_macos_plist(
    every: int,
    *,
    python_bin: Path | str | None = None,
    data_dir_path: Path | None = None,
) -> bytes:
    """Build launchd plist XML content as bytes.

    Args:
        every: Interval in minutes (1-60).
        python_bin: Optional Python interpreter path override.
        data_dir_path: Optional mswap data directory override.

    Raises:
        UsageError: If every is out of range (1-60).
    """
    if not (1 <= every <= 60):
        raise UsageError(
            f"--every must be between 1 and 60 (got {every}).",
            hint="Choose an interval between 1 and 60 minutes.",
        )

    py_exe = str(python_bin or sys.executable)
    d_dir = data_dir_path or _default_data_dir()

    payload = {
        "Label": MACOS_LABEL,
        "ProgramArguments": [
            py_exe,
            "-m",
            "mswap",
            "auto",
            "--once",
            "--quiet",
        ],
        "StartInterval": every * 60,
        "RunAtLoad": False,
        "StandardOutPath": str(d_dir / "autopilot.out"),
        "StandardErrorPath": str(d_dir / "autopilot.err"),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def parse_launchctl_print(output: str) -> TaskStatus:
    """Parse output from ``launchctl print gui/$UID/dev.mswap.autopilot``."""
    state: str | None = None
    last_exit: str | None = None

    for line in output.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("state ="):
            state = trimmed.split("=", 1)[1].strip()
        elif trimmed.startswith("last exit code ="):
            last_exit = trimmed.split("=", 1)[1].strip()

    return TaskStatus(
        installed=True,
        next_run_time=None,
        last_run_time=None,
        last_result=last_exit,
        status=state,
    )


def install_macos(
    every: int = 5,
    *,
    runner: Runner | None = None,
    plist_dest: Path | None = None,
    python_bin: Path | str | None = None,
    data_dir_path: Path | None = None,
) -> str:
    """Install launchd agent for macOS background autopilot."""
    plist_bytes = build_macos_plist(every, python_bin=python_bin, data_dir_path=data_dir_path)
    dest = plist_dest or macos_plist_path()
    _atomic_write(dest, plist_bytes)

    run = _resolve_runner(runner)
    uid = _get_uid()
    target = f"gui/{uid}/{MACOS_LABEL}"

    # Bootout existing instance if loaded so bootstrap succeeds cleanly
    run(["launchctl", "bootout", target])
    res = run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)])
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        raise UsageError(
            f"launchctl bootstrap failed (exit {res.returncode}): {detail}",
            hint="Ensure you have permission to manage launchd agents.",
        )
    return f'Scheduled task "{TASK_NAME}" created (every {every} min).'


def remove_macos(
    *,
    runner: Runner | None = None,
    plist_dest: Path | None = None,
) -> str:
    """Remove launchd agent for macOS background autopilot."""
    dest = plist_dest or macos_plist_path()
    run = _resolve_runner(runner)
    uid = _get_uid()
    target = f"gui/{uid}/{MACOS_LABEL}"

    bootout_res = run(["launchctl", "bootout", target])
    existed = dest.is_file()
    dest.unlink(missing_ok=True)

    if not existed and bootout_res.returncode != 0:
        return "Not installed."
    return f'Scheduled task "{TASK_NAME}" removed.'


def query_macos(
    *,
    runner: Runner | None = None,
    plist_dest: Path | None = None,
) -> TaskStatus:
    """Query status of macOS launchd autopilot agent."""
    dest = plist_dest or macos_plist_path()
    run = _resolve_runner(runner)
    uid = _get_uid()
    target = f"gui/{uid}/{MACOS_LABEL}"

    res = run(["launchctl", "print", target])
    if res.returncode != 0:
        if not dest.is_file():
            return TaskStatus(
                installed=False,
                next_run_time=None,
                last_run_time=None,
                last_result=None,
                status=None,
            )
        return TaskStatus(
            installed=True,
            next_run_time=None,
            last_run_time=None,
            last_result=None,
            status="not loaded",
        )
    return parse_launchctl_print(res.stdout)


# ---------------------------------------------------------------------------
# Linux systemd helpers
# ---------------------------------------------------------------------------


def systemd_user_dir(home: Path | None = None) -> Path:
    """Return path to user systemd unit directory (~/.config/systemd/user)."""
    base = home or Path.home()
    return base / ".config" / "systemd" / "user"


def systemd_service_path(home: Path | None = None) -> Path:
    """Return path to mswap-autopilot.service file."""
    return systemd_user_dir(home) / SYSTEMD_SERVICE_NAME


def systemd_timer_path(home: Path | None = None) -> Path:
    """Return path to mswap-autopilot.timer file."""
    return systemd_user_dir(home) / SYSTEMD_TIMER_NAME


def quote_systemd_arg(arg: str) -> str:
    """Quote an argument for systemd unit file ExecStart if it contains spaces."""
    if " " in arg or "\t" in arg:
        return f'"{arg}"'
    return arg


def build_systemd_service(*, python_bin: Path | str | None = None) -> str:
    """Generate systemd service unit file content for mswap autopilot."""
    py_exe = quote_systemd_arg(str(python_bin or sys.executable))
    return (
        "[Unit]\n"
        "Description=mswap autopilot periodic check\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={py_exe} -m mswap auto --once --quiet\n"
    )


def build_systemd_timer(every: int) -> str:
    """Generate systemd timer unit file content.

    Args:
        every: Interval in minutes (1-60).

    Raises:
        UsageError: If every is out of range (1-60).
    """
    if not (1 <= every <= 60):
        raise UsageError(
            f"--every must be between 1 and 60 (got {every}).",
            hint="Choose an interval between 1 and 60 minutes.",
        )

    return (
        "[Unit]\n"
        "Description=mswap autopilot timer\n\n"
        "[Timer]\n"
        "OnBootSec=2min\n"
        f"OnUnitActiveSec={every}min\n"
        f"Unit={SYSTEMD_SERVICE_NAME}\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


_TIMESTAMP_RE = re.compile(
    r"\b(?:\w{3}\s+)?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\s+[A-Za-z0-9_+-]+)?\b"
)


def parse_systemd_timers_output(output: str) -> TaskStatus:
    """Parse output from ``systemctl --user list-timers mswap-autopilot.timer --no-pager``."""
    if "0 timers listed" in output or SYSTEMD_TIMER_NAME not in output:
        return TaskStatus(
            installed=False,
            next_run_time=None,
            last_run_time=None,
            last_result=None,
            status=None,
        )

    next_run: str | None = None
    last_run: str | None = None

    for line in output.splitlines():
        if SYSTEMD_TIMER_NAME in line:
            prefix = line[: line.index(SYSTEMD_TIMER_NAME)].strip()
            dates = _TIMESTAMP_RE.findall(prefix)
            if len(dates) >= 2:
                next_run = dates[0]
                last_run = dates[1]
            elif len(dates) == 1:
                # If prefix starts with n/a or - then NEXT is empty, date is LAST
                if prefix.startswith(("n/a", "-")):
                    last_run = dates[0]
                else:
                    next_run = dates[0]
            break

    return TaskStatus(
        installed=True,
        next_run_time=next_run,
        last_run_time=last_run,
        last_result=None,
        status="active",
    )


def install_linux(
    every: int = 5,
    *,
    runner: Runner | None = None,
    user_dir: Path | None = None,
    python_bin: Path | str | None = None,
) -> str:
    """Install systemd user service and timer for Linux background autopilot."""
    svc_text = build_systemd_service(python_bin=python_bin)
    timer_text = build_systemd_timer(every)

    dest_dir = user_dir or systemd_user_dir()
    svc_path = dest_dir / SYSTEMD_SERVICE_NAME
    timer_path = dest_dir / SYSTEMD_TIMER_NAME

    _atomic_write(svc_path, svc_text)
    _atomic_write(timer_path, timer_text)

    run = _resolve_runner(runner)
    run(["systemctl", "--user", "daemon-reload"])
    res = run(["systemctl", "--user", "enable", "--now", SYSTEMD_TIMER_NAME])
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        raise UsageError(
            f"systemctl enable failed (exit {res.returncode}): {detail}",
            hint="Ensure the systemd user daemon is running.",
        )
    return f'Scheduled task "{TASK_NAME}" created (every {every} min).'


def remove_linux(
    *,
    runner: Runner | None = None,
    user_dir: Path | None = None,
) -> str:
    """Remove systemd user service and timer for Linux background autopilot."""
    dest_dir = user_dir or systemd_user_dir()
    svc_path = dest_dir / SYSTEMD_SERVICE_NAME
    timer_path = dest_dir / SYSTEMD_TIMER_NAME

    run = _resolve_runner(runner)
    disable_res = run(["systemctl", "--user", "disable", "--now", SYSTEMD_TIMER_NAME])

    existed = svc_path.is_file() or timer_path.is_file()
    svc_path.unlink(missing_ok=True)
    timer_path.unlink(missing_ok=True)
    run(["systemctl", "--user", "daemon-reload"])

    if not existed and disable_res.returncode != 0:
        return "Not installed."
    return f'Scheduled task "{TASK_NAME}" removed.'


def query_linux(
    *,
    runner: Runner | None = None,
    user_dir: Path | None = None,
) -> TaskStatus:
    """Query status of Linux systemd user timer for background autopilot."""
    dest_dir = user_dir or systemd_user_dir()
    timer_path = dest_dir / SYSTEMD_TIMER_NAME

    run = _resolve_runner(runner)
    res = run(["systemctl", "--user", "list-timers", SYSTEMD_TIMER_NAME, "--no-pager"])
    if res.returncode != 0:
        if not timer_path.is_file():
            return TaskStatus(
                installed=False,
                next_run_time=None,
                last_run_time=None,
                last_result=None,
                status=None,
            )
        return TaskStatus(
            installed=True,
            next_run_time=None,
            last_run_time=None,
            last_result=None,
            status="inactive",
        )

    parsed = parse_systemd_timers_output(res.stdout)
    if not parsed["installed"] and timer_path.is_file():
        return TaskStatus(
            installed=True,
            next_run_time=None,
            last_run_time=None,
            last_result=None,
            status="inactive",
        )
    return parsed


# ---------------------------------------------------------------------------
# Platform dispatchers
# ---------------------------------------------------------------------------


def install(
    every: int = 5,
    *,
    runner: Runner | None = None,
    platform: str | None = None,
    **kwargs: Any,
) -> str:
    """Install background autopilot scheduler on macOS or Linux."""
    plat = platform or sys.platform
    if plat == "darwin":
        return install_macos(every=every, runner=runner, **kwargs)
    if plat.startswith("linux") or plat != "win32":
        return install_linux(every=every, runner=runner, **kwargs)
    raise UsageError(
        f"Unsupported platform for POSIX scheduler: {plat}",
        hint="Windows uses Task Scheduler (schtasks).",
    )


def remove(
    *,
    runner: Runner | None = None,
    platform: str | None = None,
    **kwargs: Any,
) -> str:
    """Remove background autopilot scheduler on macOS or Linux."""
    plat = platform or sys.platform
    if plat == "darwin":
        return remove_macos(runner=runner, **kwargs)
    if plat.startswith("linux") or plat != "win32":
        return remove_linux(runner=runner, **kwargs)
    raise UsageError(
        f"Unsupported platform for POSIX scheduler: {plat}",
        hint="Windows uses Task Scheduler (schtasks).",
    )


def query(
    *,
    runner: Runner | None = None,
    platform: str | None = None,
    **kwargs: Any,
) -> TaskStatus:
    """Query background autopilot scheduler status on macOS or Linux."""
    plat = platform or sys.platform
    if plat == "darwin":
        return query_macos(runner=runner, **kwargs)
    if plat.startswith("linux") or plat != "win32":
        return query_linux(runner=runner, **kwargs)
    raise UsageError(
        f"Unsupported platform for POSIX scheduler: {plat}",
        hint="Windows uses Task Scheduler (schtasks).",
    )
