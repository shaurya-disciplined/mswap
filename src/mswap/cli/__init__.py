"""CLI entry point for mswap.

Owns argv parsing and dispatch for mswap commands.
Must never perform business logic directly; delegates to core.
"""

from __future__ import annotations

import sys

from mswap import __version__


def main(argv: list[str] | None = None) -> int:
    """Run mswap CLI with the provided argument list."""
    args = sys.argv[1:] if argv is None else argv
    if args in (["--version"], ["-V"]):
        print(f"mswap {__version__} · not affiliated with Google")
        return 0
    sys.stderr.write("mswap: bootstrap build, commands arrive in W0.S3\n")
    return 64
