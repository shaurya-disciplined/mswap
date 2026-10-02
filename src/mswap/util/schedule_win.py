"""Windows Task Scheduler helpers for mswap autopilot background scheduling.

Provides install/remove/status operations for a per-user scheduled task that
runs ``mswap auto --once --quiet`` silently every N minutes via ``pythonw.exe``.

Uses ``schtasks.exe`` through an injectable runner; never uses ``shell=True``.
Only imports ``core.errors``; no other mswap imports (util layer rule).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypedDict

from mswap.core.errors import UsageError

TASK_NAME = "mswap autopilot"

# schtasks exit codes
_ERROR_FILE_NOT_FOUND = 1


class TaskStatus(TypedDict):
    """Parsed fields from ``schtasks /Query /V /FO LIST``."""

    installed: bool
    next_run_time: str | None
    last_run_time: str | None
    last_result: str | None
    status: str | None


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _default_runner(
    cmd: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, capturing stdout/stderr as text."""
    return subprocess.run(  # noqa: S603 - schtasks with validated static args
        list(cmd),
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_runner(runner: Runner | None) -> Runner:
    """Resolve the runner, looking up ``_default_runner`` at call time.

    This indirection lets tests monkeypatch ``_default_runner`` on the module
    and have the patched version take effect even though Python binds default
    argument values at function-definition time.
    """
    if runner is not None:
        return runner
    return _default_runner


def _find_pythonw() -> Path:
    """Locate ``pythonw.exe`` next to the current Python interpreter."""
    exe = Path(sys.executable)
    pythonw = exe.parent / "pythonw.exe"
    if not pythonw.is_file():
        raise UsageError(
            "pythonw.exe not found next to this Python.",
            hint="Reinstall with `uv tool install mswap`.",
        )
    return pythonw


def build_install_argv(every: int) -> list[str]:
    """Build the ``schtasks /Create`` argument list.

    Args:
        every: Interval in minutes (1-60).

    Returns:
        The full argv list for schtasks.

    Raises:
        UsageError: If ``every`` is out of range or pythonw.exe is missing.
    """
    if not (1 <= every <= 60):
        raise UsageError(
            f"--every must be between 1 and 60 (got {every}).",
            hint="Choose an interval between 1 and 60 minutes.",
        )
    pythonw = _find_pythonw()
    tr = f'"{pythonw}" -m mswap auto --once --quiet'
    return [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/SC",
        "MINUTE",
        "/MO",
        str(every),
        "/TR",
        tr,
        "/F",
    ]


def build_remove_argv() -> list[str]:
    """Build the ``schtasks /Delete`` argument list."""
    return [
        "schtasks",
        "/Delete",
        "/TN",
        TASK_NAME,
        "/F",
    ]


def build_query_argv() -> list[str]:
    """Build the ``schtasks /Query`` argument list for verbose LIST output."""
    return [
        "schtasks",
        "/Query",
        "/TN",
        TASK_NAME,
        "/FO",
        "LIST",
        "/V",
    ]


def parse_query_output(output: str) -> TaskStatus:
    """Parse ``schtasks /Query /V /FO LIST`` output into a `TaskStatus`.

    Extracts "Next Run Time", "Last Run Time", "Last Result", and "Status"
    from the colon-delimited key/value lines.
    """
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        # Keys like "Next Run Time:" - split on first colon only
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # schtasks prints "TaskName:", "Next Run Time:", etc.
        if key in (
            "Next Run Time",
            "Last Run Time",
            "Last Result",
            "Status",
        ):
            fields[key] = value

    return TaskStatus(
        installed=True,
        next_run_time=fields.get("Next Run Time"),
        last_run_time=fields.get("Last Run Time"),
        last_result=fields.get("Last Result"),
        status=fields.get("Status"),
    )


def install(
    every: int = 5,
    *,
    runner: Runner | None = None,
) -> str:
    """Create the scheduled task.

    Returns:
        A human-readable success message.

    Raises:
        UsageError: If schtasks fails.
    """
    run = _resolve_runner(runner)
    argv = build_install_argv(every)
    result = run(argv)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise UsageError(
            f"schtasks failed (exit {result.returncode}): {detail}",
            hint="Ensure you have permission to create scheduled tasks.",
        )
    return f'Scheduled task "{TASK_NAME}" created (every {every} min).'


def remove(
    *,
    runner: Runner | None = None,
) -> str:
    """Delete the scheduled task.

    Returns:
        A human-readable result message.
    """
    run = _resolve_runner(runner)
    argv = build_remove_argv()
    result = run(argv)
    if result.returncode != 0:
        stderr_lower = (result.stderr or result.stdout).strip().lower()
        # "cannot find the file specified" -> the task doesn't exist
        if "cannot find" in stderr_lower or result.returncode == _ERROR_FILE_NOT_FOUND:
            return "Not installed."
        detail = (result.stderr or result.stdout).strip()
        raise UsageError(
            f"schtasks failed (exit {result.returncode}): {detail}",
            hint="Ensure you have permission to modify scheduled tasks.",
        )
    return f'Scheduled task "{TASK_NAME}" removed.'


def query(
    *,
    runner: Runner | None = None,
) -> TaskStatus:
    """Query the scheduled task status.

    Returns:
        Parsed task status.
    """
    run = _resolve_runner(runner)
    argv = build_query_argv()
    result = run(argv)
    if result.returncode != 0:
        stderr_lower = (result.stderr or result.stdout).strip().lower()
        if "cannot find" in stderr_lower or result.returncode == _ERROR_FILE_NOT_FOUND:
            return TaskStatus(
                installed=False,
                next_run_time=None,
                last_run_time=None,
                last_result=None,
                status=None,
            )
        detail = (result.stderr or result.stdout).strip()
        raise UsageError(
            f"schtasks failed (exit {result.returncode}): {detail}",
        )
    return parse_query_output(result.stdout)
