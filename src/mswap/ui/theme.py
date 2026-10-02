"""Terminal styling, ANSI colors, progress bars, and glyphs.

Owns visual themes and terminal-adaptive glyph formatting.
Must never print directly or perform unbuffered terminal I/O.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

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


class _ColorMethod(str):
    """String glyph that can also be called as a formatting method."""

    _code: str
    _color_enabled: bool

    def __new__(cls, glyph: str, code: str, color_enabled: bool) -> _ColorMethod:
        obj = super().__new__(cls, glyph)
        obj._code = code
        obj._color_enabled = color_enabled
        return obj

    def __call__(self, s: str | None = None) -> str:
        if s is None:
            return str(self)
        if self._color_enabled:
            return f"\033[{self._code}m{s}\033[0m"
        return s


class Theme:
    """Terminal styling configuration and glyph collection."""

    def __init__(self, color: bool = True, ascii: bool = False) -> None:
        self.color = color
        self.ascii = ascii

        # Glyphs per §A18 with ASCII fallbacks
        self.active: str = ">" if ascii else "▸"
        self.bar_full: str = "=" if ascii else "━"
        self.bar_empty: str = "-" if ascii else "─"

        glyph_ok_str = "+" if ascii else "✓"
        glyph_err_str = "x" if ascii else "✗"

        self.glyph_ok: str = glyph_ok_str
        self.glyph_err: str = glyph_err_str
        self.ok_glyph: str = glyph_ok_str
        self.err_glyph: str = glyph_err_str
        self.glyph_active: str = self.active
        self.glyph_bar_full: str = self.bar_full
        self.glyph_bar_empty: str = self.bar_empty

        # Methods ok(s) and err(s) that also act as the glyph strings
        self.ok = _ColorMethod(glyph_ok_str, "32", self.color)
        self.err = _ColorMethod(glyph_err_str, "31", self.color)

    def _format(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def warn(self, s: str) -> str:
        """Format text in warning yellow."""
        return self._format("33", s)

    def green(self, s: str) -> str:
        """Format text in green."""
        return self._format("32", s)

    def yellow(self, s: str) -> str:
        """Format text in yellow."""
        return self._format("33", s)

    def red(self, s: str) -> str:
        """Format text in red."""
        return self._format("31", s)

    def accent(self, s: str) -> str:
        """Format text in cyan accent."""
        return self._format("36", s)

    def dim(self, s: str) -> str:
        """Format text in dimmed mode."""
        return self._format("2", s)

    def bold(self, s: str) -> str:
        """Format text in bold mode."""
        return self._format("1", s)


def theme_from(
    env: Mapping[str, str] | None = None,
    *,
    no_color_flag: bool = False,
    ascii_flag: bool = False,
    isatty: bool = True,
) -> Theme:
    """Construct a Theme instance based on environment and CLI flags."""
    env_map = os.environ if env is None else env
    no_color = "NO_COLOR" in env_map
    color = bool(isatty and (not no_color) and (not no_color_flag))
    ascii_mode = bool(ascii_flag or env_map.get("MSWAP_ASCII") == "1")
    return Theme(color=color, ascii=ascii_mode)
