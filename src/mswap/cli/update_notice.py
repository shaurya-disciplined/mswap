"""Dim "update available" footer shown after human `list` and `doctor` output.

Never prints with --json or --quiet, never runs in status/hook/auto/schedule, and stays
silent on every failure. Opt out with settings updates.check=false or MSWAP_NO_UPDATE_CHECK=1.
"""

from __future__ import annotations

from mswap import __version__
from mswap.cli.context import AppContext
from mswap.core.errors import MswapError
from mswap.core.settings import load_settings
from mswap.core.updates import update_available, update_check_enabled


def maybe_print_update_notice(ctx: AppContext) -> None:
    """Print the one-line upgrade hint when a newer mswap is published."""
    if ctx.json or ctx.quiet:
        return
    try:
        check_setting = load_settings().updates.check
    except MswapError:
        return
    if not update_check_enabled(ctx.env, check_setting):
        return
    latest = update_available(ctx.http, ctx.clock, ctx.store.root / "update.json", __version__)
    if latest is None:
        return
    print(
        ctx.theme.dim(
            f"  mswap {latest} is available (you have {__version__}): uv tool upgrade mswap"
        ),
        file=ctx.out,
    )
