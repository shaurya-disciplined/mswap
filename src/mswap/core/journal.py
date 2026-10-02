"""Switch transaction journal and state machine.

Owns crash-safe recording of switch transactions.
Must never store tokens, client secrets, or user credentials.

State Table:
  Current State | Method Call / Event | Next State | Condition / Rules
  --------------+---------------------+------------+--------------------------------------
  none          | begin()             | begun      | Initial entry
  committed     | begin()             | begun      | New switch transaction starts
  failed        | begin()             | begun      | New switch transaction starts
  begun         | commit()            | committed  | Transaction completed successfully
  begun         | fail()              | failed     | Transaction rolled back or aborted
  none          | clear()             | none       | No file to remove
  committed     | clear()             | none       | Remove completed journal file
  failed        | clear()             | none       | Remove failed journal file

Recovery Transitions (evaluated during crash recovery):
  Current State | Observed System State                | Recovery Action | Next State
  --------------+--------------------------------------+-----------------+------------
  none          | No journal present                   | No-op           | none
  committed     | Previous switch completed cleanly    | No-op           | committed
  failed        | Previous switch failed cleanly       | No-op           | failed
  begun         | live_fp == to_fp                     | commit()        | committed
  begun         | live_fp == saved live_fp             | fail()          | failed
  begun         | backup_last_fp == saved live_fp      | restore + fail()| failed
  begun         | Unrecoverable / ambiguous state      | Raise error     | CorruptState

Invalid Transitions:
  Any transition not explicitly listed in the state table raises CorruptState.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mswap.core.errors import CorruptState
from mswap.util.fsx import write_private_text

VALID_STATES = {"begun", "committed", "failed"}


class Journal:
    """Crash-safe file journal tracking in-flight switch operations."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def state(self) -> dict[str, Any] | None:
        """Return the current parsed journal state dict, or None if no journal exists."""
        if not self.path.exists():
            return None
        try:
            content = self.path.read_text(encoding="utf-8")
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("Journal content must be a JSON object.")
            if "state" not in data or data["state"] not in VALID_STATES:
                raise ValueError(f"Invalid journal state: {data.get('state')}")
            if "op" not in data or not isinstance(data["op"], str):
                raise ValueError("Missing or invalid operation field.")
            return data
        except Exception as e:
            raise CorruptState(
                "Interrupted switch transaction in journal is corrupt.",
                hint="Run `mswap doctor --repair`.",
            ) from e

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        write_private_text(self.path, json.dumps(payload, indent=2, ensure_ascii=False))

    def begin(
        self,
        op: str,
        from_fp: str | None,
        to_fp: str | None,
        live_fp: str | None,
    ) -> None:
        """Begin a new journaled operation, raising CorruptState if one is already begun."""
        curr = self.state()
        if curr is not None and curr.get("state") == "begun":
            raise CorruptState(
                "Interrupted switch transaction in journal.",
                hint="Run `mswap doctor --repair`.",
            )
        now_iso = datetime.now(UTC).astimezone().isoformat()
        payload = {
            "op": op,
            "state": "begun",
            "from_fp": from_fp,
            "to_fp": to_fp,
            "live_fp": live_fp,
            "at": now_iso,
        }
        self._write_atomic(payload)

    def commit(self) -> None:
        """Transition the journal from begun to committed."""
        curr = self.state()
        if curr is None or curr.get("state") != "begun":
            state_name = curr.get("state") if curr else "none"
            raise CorruptState(
                f"Cannot commit journal when state is '{state_name}'.",
                hint="Run `mswap doctor --repair`.",
            )
        curr["state"] = "committed"
        curr["at"] = datetime.now(UTC).astimezone().isoformat()
        self._write_atomic(curr)

    def fail(self) -> None:
        """Transition the journal from begun to failed."""
        curr = self.state()
        if curr is None or curr.get("state") != "begun":
            state_name = curr.get("state") if curr else "none"
            raise CorruptState(
                f"Cannot fail journal when state is '{state_name}'.",
                hint="Run `mswap doctor --repair`.",
            )
        curr["state"] = "failed"
        curr["at"] = datetime.now(UTC).astimezone().isoformat()
        self._write_atomic(curr)

    def clear(self) -> None:
        """Remove the journal file, raising CorruptState if an operation is in-flight."""
        curr = self.state()
        if curr is not None and curr.get("state") == "begun":
            raise CorruptState(
                "Cannot clear an in-flight switch transaction.",
                hint="Run `mswap doctor --repair`.",
            )
        if self.path.exists():
            self.path.unlink()
