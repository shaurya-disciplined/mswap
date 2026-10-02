"""Hidden dynamic-completion helper behind `mswap __complete`.

Owns `mswap __complete selectors`: slots, emails and aliases from accounts.json, one per line.
Must never touch the vault, the network or any file other than accounts.json, and must never fail.
"""

from __future__ import annotations

import json
from typing import Any

from mswap.cli.context import AppContext
from mswap.core.errors import UsageError

QUERIES = ("selectors",)


def _read_accounts(ctx: AppContext) -> list[Any]:
    try:
        data = json.loads((ctx.store.root / "accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    accounts = data.get("accounts") if isinstance(data, dict) else None
    return accounts if isinstance(accounts, list) else []


def selectors(ctx: AppContext) -> list[str]:
    """Return every selector for every saved account: slot, email and alias, deduplicated."""
    entries = [a for a in _read_accounts(ctx) if isinstance(a, dict)]
    entries.sort(key=lambda a: a["slot"] if isinstance(a.get("slot"), int) else 0)
    seen: dict[str, None] = {}
    for entry in entries:
        slot = entry.get("slot")
        if isinstance(slot, int) and not isinstance(slot, bool):
            seen.setdefault(str(slot))
        for field in ("email", "alias"):
            value = entry.get(field)
            if isinstance(value, str) and value and not any(c.isspace() for c in value):
                seen.setdefault(value)
    return list(seen)


def run(ctx: AppContext, rest: list[str]) -> int:
    """Answer one completion query, printing one candidate per line."""
    if rest != ["selectors"]:
        raise UsageError(
            "Unknown completion query.",
            hint="`mswap __complete` is internal; see `mswap completions --help`.",
        )
    for line in selectors(ctx):
        print(line, file=ctx.out)
    return 0
