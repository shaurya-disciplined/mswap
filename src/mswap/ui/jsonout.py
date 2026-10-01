"""JSON output formatter for CLI commands.

Owns JSON envelope serialization for machine-readable output.
Must never leak unredacted credentials or unescaped secrets.
"""

from __future__ import annotations

import json
from typing import Any

from mswap.core.errors import MswapError
from mswap.util.redact import redact

SCHEMA_VERSION: int = 1


def ok(command: str, data: dict[str, Any]) -> str:
    """Format a successful command result as a schema-1 JSON string."""
    payload = {
        "schema": SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "data": data,
    }
    return json.dumps(payload, ensure_ascii=False)


def err(command: str, e: MswapError) -> str:
    """Format an error as a schema-1 JSON string with redacted message and hint."""
    payload = {
        "schema": SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "error": {
            "code": e.code,
            "kind": e.kind,
            "message": redact(e.message),
            "hint": redact(e.hint) if e.hint is not None else None,
        },
    }
    return json.dumps(payload, ensure_ascii=False)
