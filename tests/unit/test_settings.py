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


@pytest.mark.parametrize("action", ["notify", "switch"])
def test_valid_hook_action(tmp_path: Path, action: str) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(f'[autopilot]\nhook_action = "{action}"\n', encoding="utf-8")
    cfg = load_settings(p)
    assert cfg.autopilot.hook_action == action


@pytest.mark.parametrize("invalid_action", ["random", "alert", 123, True])
def test_invalid_hook_action_raises_usage_error(tmp_path: Path, invalid_action: object) -> None:
    p = tmp_path / "settings.toml"
    val_repr = (
        str(invalid_action).lower() if isinstance(invalid_action, bool) else repr(invalid_action)
    )
    p.write_text(f"[autopilot]\nhook_action = {val_repr}\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.hook_action" in str(exc_info.value)


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


@pytest.mark.parametrize("valid_threshold", [50, 75, 99])
def test_valid_threshold_boundary(tmp_path: Path, valid_threshold: int) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(f"[autopilot]\nthreshold = {valid_threshold}\n", encoding="utf-8")
    cfg = load_settings(p)
    assert cfg.autopilot.threshold == valid_threshold


@pytest.mark.parametrize("invalid_threshold", [49, 100])
def test_threshold_out_of_range_raises_usage_error(tmp_path: Path, invalid_threshold: int) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(f"[autopilot]\nthreshold = {invalid_threshold}\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.threshold" in str(exc_info.value)
    assert "between 50 and 99" in str(exc_info.value)


@pytest.mark.parametrize("valid_margin", [0, 25, 50])
def test_valid_margin_boundary(tmp_path: Path, valid_margin: int) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(f"[autopilot]\nmargin = {valid_margin}\n", encoding="utf-8")
    cfg = load_settings(p)
    assert cfg.autopilot.margin == valid_margin


@pytest.mark.parametrize("invalid_margin", [-1, 51])
def test_margin_out_of_range_raises_usage_error(tmp_path: Path, invalid_margin: int) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(f"[autopilot]\nmargin = {invalid_margin}\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.margin" in str(exc_info.value)
    assert "between 0 and 50" in str(exc_info.value)


@pytest.mark.parametrize("valid_cooldown", [60, 300, 86400])
def test_valid_cooldown_boundary(tmp_path: Path, valid_cooldown: int) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(f"[autopilot]\ncooldown = {valid_cooldown}\n", encoding="utf-8")
    cfg = load_settings(p)
    assert cfg.autopilot.cooldown == valid_cooldown


@pytest.mark.parametrize("invalid_cooldown", [59, 86401])
def test_cooldown_out_of_range_raises_usage_error(tmp_path: Path, invalid_cooldown: int) -> None:
    p = tmp_path / "settings.toml"
    p.write_text(f"[autopilot]\ncooldown = {invalid_cooldown}\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_settings(p)
    assert "autopilot.cooldown" in str(exc_info.value)
    assert "between 60 and 86400" in str(exc_info.value)


def test_dump_toml_flat_tables_and_types() -> None:
    from mswap.core.settings import dump_toml

    data = {
        "autopilot": {
            "threshold": 90,
            "strategy": "best",
            "extra_str": "hello\nworld",
        },
        "ui": {
            "ascii": False,
            "color": "auto",
        },
        "updates": {
            "check": True,
        },
        "custom": {
            "ratio": 3.14,
            "items": ["a", "b"],
        },
    }
    rendered = dump_toml(data)
    assert "[autopilot]" in rendered
    assert "[ui]" in rendered
    assert "[updates]" in rendered
    assert "[custom]" in rendered
    assert "threshold = 90" in rendered
    assert 'strategy = "best"' in rendered
    assert "ascii = false" in rendered
    assert "check = true" in rendered
    assert 'items = ["a", "b"]' in rendered

    # Roundtrip through tomllib
    import tomllib

    parsed = tomllib.loads(rendered)
    assert parsed["autopilot"]["threshold"] == 90
    assert parsed["autopilot"]["strategy"] == "best"
    assert parsed["ui"]["ascii"] is False
    assert parsed["updates"]["check"] is True
    assert parsed["custom"]["ratio"] == 3.14
    assert parsed["custom"]["items"] == ["a", "b"]


def test_settings_set_and_unset_cleanups(tmp_path: Path) -> None:
    from mswap.core.settings import (
        get_setting,
        list_settings,
        set_setting,
        unset_setting,
    )

    p = tmp_path / "settings.toml"
    # Setting in fresh file
    set_setting("autopilot.threshold", 85, path=p)
    val, is_def = get_setting("autopilot.threshold", path=p)
    assert val == 85
    assert is_def is False

    # Setting another section
    set_setting("ui.ascii", True, path=p)
    val_ui, is_def_ui = get_setting("ui.ascii", path=p)
    assert val_ui is True
    assert is_def_ui is False

    # List
    items = list_settings(path=p)
    items_dict = {k: (v, d) for k, v, d in items}
    assert items_dict["autopilot.threshold"] == (85, False)
    assert items_dict["ui.ascii"] == (True, False)
    assert items_dict["updates.check"] == (True, True)

    # Unset threshold
    unset_setting("autopilot.threshold", path=p)
    val_after, is_def_after = get_setting("autopilot.threshold", path=p)
    assert val_after == 90
    assert is_def_after is True

    # Unset ui.ascii (emptying ui section)
    unset_setting("ui.ascii", path=p)
    content = p.read_text(encoding="utf-8")
    assert "[ui]" not in content


def test_settings_unknown_key_in_raw_toml(tmp_path: Path) -> None:
    from mswap.core.settings import get_setting, unset_setting

    p = tmp_path / "settings.toml"
    p.write_text('[custom]\nplugin_val = "active"\n', encoding="utf-8")

    val, is_def = get_setting("custom.plugin_val", path=p)
    assert val == "active"
    assert is_def is False

    unset_setting("custom.plugin_val", path=p)
    content = p.read_text(encoding="utf-8")
    assert "plugin_val" not in content


def test_load_raw_toml_syntax_error(tmp_path: Path) -> None:
    from mswap.core.settings import load_raw_toml

    p = tmp_path / "settings.toml"
    p.write_text("[broken\nkey = ", encoding="utf-8")
    with pytest.raises(UsageError) as exc_info:
        load_raw_toml(p)
    assert "Failed to parse settings file" in str(exc_info.value)
