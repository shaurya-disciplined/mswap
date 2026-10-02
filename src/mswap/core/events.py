"""Local audit event logging with rotation and secret redaction.

Owns appending structured events to events.log and rotating at 1 MB.
Must never record unredacted access/refresh tokens or client secrets.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mswap.util.redact import redact

MAX_LOG_SIZE = 1024 * 1024  # 1 MB


class Events:
    """Append-only structured JSONL event logger with automatic log rotation."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def emit(self, event: str, **fields: Any) -> None:
        """Append one structured event line, rotating the log file if exceeding 1 MB."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

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

        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
