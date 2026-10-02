"""Local audit event logging with rotation and secret redaction.

Owns appending structured events to events.log and rotating at 1 MB.
Must never record unredacted access/refresh tokens or client secrets.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mswap.util.fsx import append_private_text, ensure_private_dir
from mswap.util.redact import redact

MAX_LOG_SIZE = 1024 * 1024  # 1 MB


class Events:
    """Append-only structured JSONL event logger with automatic log rotation."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def emit(self, event: str, **fields: Any) -> None:
        """Append one structured event line, rotating the log file if exceeding 1 MB."""
        ensure_private_dir(self.path.parent)

        if self.path.exists() and self.path.stat().st_size >= MAX_LOG_SIZE:
            rotated_path = self.path.with_name(f"{self.path.name}.1")
            self.path.replace(rotated_path)

        now_iso = datetime.now(UTC).astimezone().isoformat()
        clean_fields: dict[str, Any] = {}
        for k, v in fields.items():
            if isinstance(v, str):
                clean_fields[k] = redact(v)
            else:
                clean_fields[k] = v

        record: dict[str, Any] = {"at": now_iso, "event": event, **clean_fields}
        line = json.dumps(record, ensure_ascii=False)
        line = redact(line)

        append_private_text(self.path, line + "\n")

    def read_recent(self, count: int = 20) -> list[dict[str, Any]]:
        """Read up to `count` recent events from this event log, newest last."""
        return read_recent_events(self.path, count=count)


def read_recent_events(log_path: Path | str, count: int = 20) -> list[dict[str, Any]]:
    """Read recent audit events across events.log and events.log.1, newest last.

    Returns up to `count` events in chronological order (oldest first, newest last).
    """
    path = Path(log_path)
    rotated_path = path.with_name(f"{path.name}.1")

    events: list[dict[str, Any]] = []

    for p in (rotated_path, path):
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if isinstance(record, dict):
                    events.append(record)
            except json.JSONDecodeError:
                continue

    if count <= 0:
        return []
    return events[-count:]
