"""Account identity and fingerprint matching.

Owns account lookup by cryptographic fingerprint of refresh tokens.
Must never compare or expose raw refresh token strings.
"""

from __future__ import annotations

from collections.abc import Sequence

from mswap.agy.tokens import fingerprint
from mswap.core.models import Account


def fp_or_none(blob: bytes | None) -> str | None:
    """Return the fingerprint of a credential blob, or None if missing or invalid."""
    if not blob:
        return None
    try:
        return fingerprint(blob)
    except Exception:
        return None


def find_by_fp(accounts: Sequence[Account], blob: bytes | None) -> Account | None:
    """Identify the account matching the given credential blob's fingerprint."""
    fp = fp_or_none(blob)
    if not fp:
        return None
    for a in accounts:
        if a.fp == fp:
            return a
    return None
