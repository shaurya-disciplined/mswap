"""Unit tests for settings loader and validation in core/settings.py."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from mswap.core.errors import UsageError
from mswap.core.settings import Settings, load_settings


def test_load_settings_missing_file_returns_defaults(tmp_path: Path) -> None:
    p = tmp_path / "nonexistent.toml"
    cfg = load_settings(p)
    assert cfg == Settings()
    assert cfg.autopilot.threshold == 90
    assert cfg.autopilot.margin == 10
    assert cfg.autopilot.cooldown == 300
    assert cfg.autopilot.strategy == "best"
    assert cfg.autopilot.focus == "auto"
    assert cfg.ui.ascii is False
    assert cfg.ui.color == "auto"
    assert cfg.updates.check is True


def test_load_settings_valid_toml(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(
        """
[autopilot]
threshold = 85
margin = 15
cooldown = 120
strategy = "consume-first"
focus = "gemini"

[ui]
ascii = true
color = "always"

[updates]
check = false
""",
        encoding="utf-8",
    )
    cfg = load_settings(p)
    assert cfg.autopilot.threshold == 85
    assert cfg.autopilot.margin == 15
    assert cfg.autopilot.cooldown == 120
    assert cfg.autopilot.strategy == "consume-first"
    assert cfg.autopilot.focus == "gemini"
    assert cfg.ui.ascii is True
    assert cfg.ui.color == "always"
    assert cfg.updates.check is False

    pol_settings = cfg.autopilot.to_policy_settings()
    assert pol_settings.threshold == 85
    assert pol_settings.margin == 15
    assert pol_settings.cooldown_s == 120
    assert pol_settings.strategy == "consume-first"
    assert pol_settings.focus == "gemini"


@pytest.mark.parametrize("invalid_threshold", [-1, 101, "90", True, 3.14])
def test_invalid_threshold_raises_usage_error(tmp_path: Path, invalid_threshold: object) -> None:
    p = tmp_path / "settings.toml"
    val_repr = (
        str(invalid_threshold).lower()
        if isinstance(invalid_threshold, bool)
        else repr(invalid_threshold)
    )
    p.write_text(f"[autopilot]\nthreshold = {val_repr}\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.threshold" in str(exc_info.value)


@pytest.mark.parametrize("invalid_margin", [-5, 105, "10", False])
def test_invalid_margin_raises_usage_error(tmp_path: Path, invalid_margin: object) -> None:
    p = tmp_path / "settings.toml"
    val_repr = (
        str(invalid_margin).lower() if isinstance(invalid_margin, bool) else repr(invalid_margin)
    )
    p.write_text(f"[autopilot]\nmargin = {val_repr}\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.margin" in str(exc_info.value)


@pytest.mark.parametrize("invalid_cooldown", [-1, "300", True])
def test_invalid_cooldown_raises_usage_error(tmp_path: Path, invalid_cooldown: object) -> None:
    p = tmp_path / "settings.toml"
    val_repr = (
        str(invalid_cooldown).lower()
        if isinstance(invalid_cooldown, bool)
        else repr(invalid_cooldown)
    )
    p.write_text(f"[autopilot]\ncooldown = {val_repr}\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.cooldown" in str(exc_info.value)


def test_invalid_strategy_raises_usage_error(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    p.write_text('[autopilot]\nstrategy = "random"\n', encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.strategy" in str(exc_info.value)


def test_invalid_focus_raises_usage_error(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    p.write_text('[autopilot]\nfocus = "gpt"\n', encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.focus" in str(exc_info.value)


def test_invalid_ui_ascii_raises_usage_error(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    p.write_text('[ui]\nascii = "yes"\n', encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "ui.ascii" in str(exc_info.value)


def test_invalid_ui_color_raises_usage_error(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    p.write_text('[ui]\ncolor = "blue"\n', encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "ui.color" in str(exc_info.value)


def test_invalid_updates_check_raises_usage_error(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    p.write_text("[updates]\ncheck = 123\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "updates.check" in str(exc_info.value)


def test_invalid_toml_syntax_raises_usage_error(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    p.write_text("[broken toml\nkey = ", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "Failed to parse settings file" in str(exc_info.value)


def test_unknown_keys_and_sections_warn_and_ignored(tmp_path: Path) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(
        """
[autopilot]
unknown_field = 42

[unknown_section]
foo = "bar"
""",
        encoding="utf-8",
    )
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        cfg = load_settings(p)
    assert len(recorded) >= 2
    assert cfg.autopilot.threshold == 90
