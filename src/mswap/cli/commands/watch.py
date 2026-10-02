"""Command to start the live dashboard."""

from __future__ import annotations

import argparse

from mswap.cli.context import AppContext
from mswap.ui.live import run_live


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the watch command."""
    force = getattr(args, "force", False) if isinstance(args, argparse.Namespace) else False
    return run_live(ctx, force=force)
