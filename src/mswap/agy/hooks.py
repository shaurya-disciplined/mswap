"""Manage agy Stop-hook sets for mswap autopilot integration.

Handles installation, removal and status reporting of the mswap-autopilot
hook set in agy's hooks.json.  Other hook sets (e.g. stop-notification,
permission-notification) are preserved byte-for-byte.

Safety: Never modifies or removes existing hook sets. The ``install`` function
adds only the ``mswap-autopilot`` key. A first-time backup is kept as
``hooks.json.mswap-bak``.  ``remove`` deletes only the ``mswap-autopilot``
key.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from mswap.core.errors import CorruptState
from mswap.util.shellquote import RUN_MODULE, quote_command_path

HOOK_SET = "mswap-autopilot"


def hooks_path() -> Path:
    """Return path to agy's hooks.json, honoring MSWAP_AGY_STATE."""
    state = os.environ.get("MSWAP_AGY_STATE")
    if state:
        return Path(state) / "hooks.json"
    return Path.home() / ".gemini" / "antigravity-cli" / "hooks.json"


def _default_command() -> str:
    """Build the default hook command string."""
    exe = quote_command_path(sys.executable, windows=sys.platform == "win32")
    return f"{exe} {RUN_MODULE} auto --once --quiet --from-hook"


def _load_hooks(path: Path) -> dict[str, Any]:
    """Load hooks.json, returning {} if the file does not exist."""
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CorruptState(
            f"hooks.json contains invalid JSON: {e}",
            hint="Check your agy hooks configuration.",
        ) from e
    except OSError as e:
        raise CorruptState(
            f"Failed to read hooks.json: {e}",
            hint="Check file permissions on your agy state directory.",
        ) from e
    if not isinstance(data, dict):
        raise CorruptState(
            f"hooks.json is not a JSON object: {path}",
            hint="Check your agy hooks configuration.",
        )
    return data


def _write_hooks(path: Path, data: dict[str, Any]) -> None:
    """Atomically write hooks.json with indent=2."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        # hooks.json belongs to agy: keep its permissions rather than loosening them to our umask.
        with contextlib.suppress(OSError):
            shutil.copymode(path, tmp)
    tmp.replace(path)


def install(command: str | None = None) -> bool:
    """Install the mswap-autopilot hook set.

    Returns True if hooks.json was changed (new install or command updated).
    Preserves every other key and its order.  Writes atomically with indent=2.
    Keeps a backup ``hooks.json.mswap-bak`` (only the first time).
    """
    cmd = command or _default_command()
    path = hooks_path()
    data = _load_hooks(path)

    new_set: dict[str, Any] = {"Stop": [{"type": "command", "command": cmd}]}

    # Idempotent: no change if already installed with the same command
    existing = data.get(HOOK_SET)
    if existing == new_set:
        return False

    # Backup only if this is the first time (backup doesn't already exist)
    bak_path = path.with_name("hooks.json.mswap-bak")
    if path.exists() and not bak_path.exists():
        bak_path.write_bytes(path.read_bytes())

    data[HOOK_SET] = new_set
    _write_hooks(path, data)
    return True


def remove() -> bool:
    """Remove the mswap-autopilot hook set.

    Returns True if the set was removed.  False if not installed.
    """
    path = hooks_path()
    if not path.exists():
        return False

    data = _load_hooks(path)

    if HOOK_SET not in data:
        return False

    del data[HOOK_SET]
    _write_hooks(path, data)
    return True


def status() -> dict[str, Any]:
    """Report mswap-autopilot hook installation status."""
    path = hooks_path()
    bak_path = path.with_name("hooks.json.mswap-bak")
    result: dict[str, Any] = {
        "installed": False,
        "command": None,
        "hooks_path": str(path),
        "backup_exists": bak_path.exists(),
    }

    if not path.exists():
        return result

    try:
        data = _load_hooks(path)
    except CorruptState:
        return result

    hook_set = data.get(HOOK_SET)
    if isinstance(hook_set, dict):
        stop_hooks = hook_set.get("Stop", [])
        if isinstance(stop_hooks, list) and stop_hooks:
            first = stop_hooks[0]
            if isinstance(first, dict):
                result["installed"] = True
                result["command"] = first.get("command")

    return result
