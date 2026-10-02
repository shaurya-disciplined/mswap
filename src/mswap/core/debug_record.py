"""Shape-only capture of agy API responses for bug reports (`mswap debug record`).

Owns reducing a JSON response to its structure (strings become `<str:N>` unless the key is on
the allow-list), and the scan that proves a capture holds nothing secret-shaped or email-shaped.
Must never keep a string value that looks like a token or an email, under any key.
"""

from __future__ import annotations

import re
from typing import Any

# Values under these keys are enum-like labels, not user data (see 01-ground-truth G5).
KEEP_KEYS: frozenset[str] = frozenset(
    {
        "window",
        "bucketId",
        "displayName",
        "id",
        "modelProvider",
        "tokenType",
        "status",
        "reasonCode",
    }
)

_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "access_token": re.compile(r"ya29\.[\w-]+"),
    "refresh_token": re.compile(r"1//[\w-]+"),
    "client_secret": re.compile(r"GOCSPX-[\w-]+"),
    "jwt": re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"),
    "token_field": re.compile(r'"(?:access|refresh|id)_token"\s*:\s*"[^"]+"'),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
}


def scan_text(text: str) -> list[str]:
    """Return the names of every secret or email pattern that matches `text` (empty = clean)."""
    return [name for name, pattern in _SECRET_PATTERNS.items() if pattern.search(text)]


def _placeholder(value: str) -> str:
    return f"<str:{len(value)}>"


def _keep_string(value: str) -> str:
    """Keep an allow-listed string unless it is secret-shaped (defence in depth)."""
    return _placeholder(value) if scan_text(value) else value


def shape(value: Any, *, keep: bool = False) -> Any:
    """Reduce `value` to its shape: strings -> `<str:N>`, numbers/bools/null kept as they are.

    `keep` is True for the direct value (or list items) of an allow-listed key.
    """
    if isinstance(value, dict):
        return {str(k): shape(v, keep=str(k) in KEEP_KEYS) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(v, keep=keep) for v in value]
    if isinstance(value, str):
        return _keep_string(value) if keep else _placeholder(value)
    return value
