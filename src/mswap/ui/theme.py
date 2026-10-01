"""Terminal styling, colors, and progress bars."""

from __future__ import annotations

import os
import sys

# Enable ANSI colours on Windows console if possible
if sys.platform == "win32":
    os.system("")  # noqa: S605, S607 - Windows console virtual terminal processing init


def is_color_enabled() -> bool:
    """Return True if terminal color output is supported and not disabled."""
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    """Wrap string with ANSI escape code if color is enabled."""
    return f"\033[{code}m{s}\033[0m" if is_color_enabled() else s


def bold(s: str) -> str:
    """Bold text."""
    return _c("1", s)


def dim(s: str) -> str:
    """Dim text."""
    return _c("2", s)


def green(s: str) -> str:
    """Green text."""
    return _c("32", s)


def yellow(s: str) -> str:
    """Yellow text."""
    return _c("33", s)


def red(s: str) -> str:
    """Red text."""
    return _c("31", s)


def cyan(s: str) -> str:
    """Cyan text."""
    return _c("36", s)


def _bar(frac: float, width: int = 12) -> str:
    """Render a horizontal progress bar using box-drawing characters."""
    filled = int(frac * width)
    colour = green if frac > 0.5 else yellow if frac > 0.15 else red
    return colour("━" * filled) + dim("─" * (width - filled))
