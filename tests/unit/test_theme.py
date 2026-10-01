"""Unit tests for ui/theme module."""

from __future__ import annotations

import pytest

from mswap.ui.theme import _bar, bold, cyan, dim, green, is_color_enabled, red, yellow


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
