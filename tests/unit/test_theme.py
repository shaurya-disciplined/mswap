"""Unit tests for ui/theme module."""

from __future__ import annotations

import pytest

from mswap.ui.theme import (
    Theme,
    _bar,
    bold,
    cyan,
    dim,
    green,
    is_color_enabled,
    red,
    theme_from,
    yellow,
)


def test_theme_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert not is_color_enabled()
    assert bold("text") == "text"
    assert dim("text") == "text"
    assert green("text") == "text"
    assert yellow("text") == "text"
    assert red("text") == "text"
    assert cyan("text") == "text"


def test_bar_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    # 100% full
    assert _bar(1.0, width=12) == "━━━━━━━━━━━━"
    # 0% full
    assert _bar(0.0, width=12) == "────────────"
    # 50% full (6 filled, 6 empty)
    assert _bar(0.5, width=12) == "━━━━━━──────"


def test_theme_unicode_glyphs() -> None:
    t = Theme(color=False, ascii=False)
    assert t.active == "▸"
    assert t.ok == "✓"
    assert t.err == "✗"
    assert t.bar_full == "━"
    assert t.bar_empty == "─"
    assert t.glyph_ok == "✓"
    assert t.glyph_err == "✗"


def test_theme_ascii_glyphs() -> None:
    t = Theme(color=False, ascii=True)
    assert t.active == ">"
    assert t.ok == "+"
    assert t.err == "x"
    assert t.bar_full == "="
    assert t.bar_empty == "-"
    assert t.glyph_ok == "+"
    assert t.glyph_err == "x"


def test_theme_color_enabled_methods() -> None:
    t = Theme(color=True, ascii=False)
    assert t.ok("done") == "\033[32mdone\033[0m"
    assert t.err("failed") == "\033[31mfailed\033[0m"
    assert t.warn("warning") == "\033[33mwarning\033[0m"
    assert t.accent("cyan") == "\033[36mcyan\033[0m"
    assert t.dim("dimmed") == "\033[2mdimmed\033[0m"
    assert t.bold("bolded") == "\033[1mbolded\033[0m"


def test_theme_color_disabled_methods() -> None:
    t = Theme(color=False, ascii=False)
    assert t.ok("done") == "done"
    assert t.err("failed") == "failed"
    assert t.warn("warning") == "warning"
    assert t.accent("cyan") == "cyan"
    assert t.dim("dimmed") == "dimmed"
    assert t.bold("bolded") == "bolded"


def test_theme_from_conditions() -> None:
    # 1. NO_COLOR disables colour even if isatty is True
    t1 = theme_from({"NO_COLOR": "1"}, no_color_flag=False, ascii_flag=False, isatty=True)
    assert not t1.color

    # 2. no_color_flag disables colour
    t2 = theme_from({}, no_color_flag=True, ascii_flag=False, isatty=True)
    assert not t2.color

    # 3. Not a TTY -> no colour (no ANSI codes)
    t3 = theme_from({}, no_color_flag=False, ascii_flag=False, isatty=False)
    assert not t3.color
    assert t3.ok("done") == "done"
    assert "\033[" not in t3.ok("done")

    # 4. isatty=True, no NO_COLOR, no flag -> colour enabled
    t4 = theme_from({}, no_color_flag=False, ascii_flag=False, isatty=True)
    assert t4.color
    assert "\033[32m" in t4.ok("done")

    # 5. ascii_flag=True gives ASCII glyphs
    t5 = theme_from({}, no_color_flag=False, ascii_flag=True, isatty=True)
    assert t5.ascii
    assert t5.active == ">"
    assert t5.ok == "+"
    assert t5.err == "x"

    # 6. MSWAP_ASCII="1" gives ASCII glyphs
    t6 = theme_from({"MSWAP_ASCII": "1"}, no_color_flag=False, ascii_flag=False, isatty=True)
    assert t6.ascii
    assert t6.active == ">"
    assert t6.bar_full == "="
