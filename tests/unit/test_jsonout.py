"""Unit tests for ui/jsonout module."""

from __future__ import annotations

import json

from mswap.core.errors import UsageError
from mswap.ui.jsonout import SCHEMA_VERSION, err, ok


def test_schema_version() -> None:
    assert SCHEMA_VERSION == 1


def test_ok_shape() -> None:
    res = ok("list", {"active_slot": 1, "note": "All ✓"})
    parsed = json.loads(res)
    assert parsed == {
        "schema": 1,
        "ok": True,
        "command": "list",
        "data": {"active_slot": 1, "note": "All ✓"},
    }
    # ensure_ascii False keeps literal unicode
    assert "✓" in res


def test_err_shape() -> None:
    exc = UsageError("No account matching '7'.", hint="See `mswap list`.")
    res = err("switch", exc)
    parsed = json.loads(res)
    assert parsed == {
        "schema": 1,
        "ok": False,
        "command": "switch",
        "error": {
            "code": 64,
            "kind": "UsageError",
            "message": "No account matching '7'.",
            "hint": "See `mswap list`.",
        },
    }


def test_err_shape_without_hint() -> None:
    exc = UsageError("Plain error without hint")
    res = err("test", exc)
    parsed = json.loads(res)
    assert parsed["error"]["hint"] is None
    assert parsed["error"]["code"] == 64
    assert parsed["error"]["kind"] == "UsageError"


def test_err_redacts_tokens() -> None:
    exc = UsageError("Failed with ya29.SECRETTOKEN", hint="Use 1//REFRESHSECRET")
    res = err("switch", exc)
    parsed = json.loads(res)
    assert "ya29." not in res
    assert "1//" not in res
    assert parsed["error"]["message"] == "Failed with [REDACTED]"
    assert parsed["error"]["hint"] == "Use [REDACTED]"
