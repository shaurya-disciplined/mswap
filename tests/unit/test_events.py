"""Unit tests for Events logger and rotation."""

from __future__ import annotations

import json
from pathlib import Path

from mswap.core.events import Events


def test_events_emit_and_redact(tmp_path: Path) -> None:
    path = tmp_path / "events.log"
    events = Events(path)

    events.emit(
        "switch", from_slot=1, to_slot=2, token="ya29.SECRET-TOKEN", email="alice@example.com"
    )
    assert path.exists()

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "switch"
    assert data["from_slot"] == 1
    assert data["to_slot"] == 2
    assert data["email"] == "alice@example.com"
    assert data["token"] == "[REDACTED]"
    assert "at" in data


def test_events_rotation_at_1mb(tmp_path: Path) -> None:
    path = tmp_path / "events.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fill file to 1 MB
    path.write_bytes(b"x" * (1024 * 1024))

    events = Events(path)
    events.emit("test_event", foo="bar")

    rot_path = tmp_path / "events.log.1"
    assert rot_path.exists()
    assert rot_path.stat().st_size == 1024 * 1024

    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "test_event" in lines[0]
