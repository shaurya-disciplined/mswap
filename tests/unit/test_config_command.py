"""Unit tests for `mswap config` CLI command."""

from __future__ import annotations

import json

import pytest

from mswap.cli import main
from mswap.cli.context import AppContext
from mswap.core.settings import get_setting, load_settings, set_setting, settings_path


def test_config_path_human(ctx: AppContext) -> None:
    code = main(["config", "path"], ctx=ctx)
    assert code == 0
    out = ctx.out.getvalue()
    p = settings_path()
    assert str(p) in out
    assert "comments" in out.lower()
    assert "not preserved" in out.lower()


def test_config_path_json(ctx: AppContext) -> None:
    code = main(["config", "path", "--json"], ctx=ctx)
    assert code == 0
    data = json.loads(ctx.out.getvalue())
    assert data["schema"] == 1
    assert data["ok"] is True
    assert data["command"] == "config"
    assert data["data"]["path"] == str(settings_path())
    assert data["data"]["comments_preserved"] is False


def test_config_default_action_is_list(ctx: AppContext) -> None:
    code = main(["config"], ctx=ctx)
    assert code == 0
    out = ctx.out.getvalue()
    assert "autopilot.threshold = 90 (default)" in out
    assert "ui.ascii = false (default)" in out
    assert "updates.check = true (default)" in out


def test_config_list_human_with_custom_values(ctx: AppContext) -> None:
    set_setting("autopilot.threshold", 85)
    code = main(["config", "list"], ctx=ctx)
    assert code == 0
    out = ctx.out.getvalue()
    assert "autopilot.threshold = 85 (default)" not in out
    assert "autopilot.threshold = 85" in out
    assert "autopilot.margin = 10 (default)" in out


def test_config_list_json(ctx: AppContext) -> None:
    set_setting("autopilot.threshold", 80)
    code = main(["config", "list", "--json"], ctx=ctx)
    assert code == 0
    data = json.loads(ctx.out.getvalue())
    assert data["schema"] == 1
    assert data["ok"] is True
    assert data["command"] == "config"
    d = data["data"]
    assert d["autopilot"]["threshold"] == 80
    assert d["settings"]["autopilot"]["threshold"] == 80
    assert "autopilot.threshold" not in d["defaults"]
    assert "autopilot.margin" in d["defaults"]
    items = {item["key"]: item for item in d["items"]}
    assert items["autopilot.threshold"]["value"] == 80
    assert items["autopilot.threshold"]["default"] is False
    assert items["autopilot.margin"]["value"] == 10
    assert items["autopilot.margin"]["default"] is True


@pytest.mark.parametrize(
    ("key", "default_val"),
    [
        ("autopilot.threshold", "90"),
        ("autopilot.margin", "10"),
        ("autopilot.cooldown", "300"),
        ("autopilot.strategy", "best"),
        ("autopilot.focus", "auto"),
        ("autopilot.hook_action", "notify"),
        ("ui.ascii", "false"),
        ("ui.color", "auto"),
        ("updates.check", "true"),
    ],
)
def test_config_get_all_keys_defaults(ctx: AppContext, key: str, default_val: str) -> None:
    code = main(["config", "get", key], ctx=ctx)
    assert code == 0
    assert ctx.out.getvalue().strip() == default_val


@pytest.mark.parametrize(
    ("key", "val_str", "expected_typed"),
    [
        ("autopilot.threshold", "75", 75),
        ("autopilot.margin", "20", 20),
        ("autopilot.cooldown", "600", 600),
        ("autopilot.strategy", "consume-first", "consume-first"),
        ("autopilot.focus", "gemini", "gemini"),
        ("autopilot.hook_action", "switch", "switch"),
        ("ui.ascii", "true", True),
        ("ui.color", "always", "always"),
        ("updates.check", "false", False),
    ],
)
def test_config_set_get_unset_roundtrip(
    ctx: AppContext, key: str, val_str: str, expected_typed: object
) -> None:
    # 1. Set key
    set_code = main(["config", "set", key, val_str], ctx=ctx)
    assert set_code == 0
    assert "Set " in ctx.out.getvalue()
    ctx.out.truncate(0)
    ctx.out.seek(0)

    # 2. Get key returns new value
    get_code = main(["config", "get", key], ctx=ctx)
    assert get_code == 0
    val_out, is_default = get_setting(key)
    assert val_out == expected_typed
    assert is_default is False
    ctx.out.truncate(0)
    ctx.out.seek(0)

    # 3. Unset key restores default
    unset_code = main(["config", "unset", key], ctx=ctx)
    assert unset_code == 0
    assert "Unset " in ctx.out.getvalue()
    _, is_default_after = get_setting(key)
    assert is_default_after is True


def test_config_get_json(ctx: AppContext) -> None:
    code = main(["config", "get", "autopilot.threshold", "--json"], ctx=ctx)
    assert code == 0
    data = json.loads(ctx.out.getvalue())
    assert data["schema"] == 1
    assert data["ok"] is True
    assert data["data"]["key"] == "autopilot.threshold"
    assert data["data"]["value"] == 90
    assert data["data"]["default"] is True


def test_config_set_json(ctx: AppContext) -> None:
    code = main(["config", "set", "autopilot.threshold", "85", "--json"], ctx=ctx)
    assert code == 0
    data = json.loads(ctx.out.getvalue())
    assert data["schema"] == 1
    assert data["ok"] is True
    assert data["data"]["action"] == "set"
    assert data["data"]["key"] == "autopilot.threshold"
    assert data["data"]["value"] == 85


def test_config_unset_json(ctx: AppContext) -> None:
    set_setting("autopilot.threshold", 85)
    code = main(["config", "unset", "autopilot.threshold", "--json"], ctx=ctx)
    assert code == 0
    data = json.loads(ctx.out.getvalue())
    assert data["schema"] == 1
    assert data["ok"] is True
    assert data["data"]["action"] == "unset"
    assert data["data"]["key"] == "autopilot.threshold"


def test_first_set_creates_file(ctx: AppContext) -> None:
    p = settings_path()
    assert not p.exists()
    code = main(["config", "set", "autopilot.threshold", "85"], ctx=ctx)
    assert code == 0
    assert p.exists()
    cfg = load_settings()
    assert cfg.autopilot.threshold == 85


def test_config_preserves_unknown_keys_and_tables(ctx: AppContext) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        """
[custom_section]
custom_key = "custom_val"
flag = true

[autopilot]
threshold = 90
unknown_opt = 42
""",
        encoding="utf-8",
    )

    code = main(["config", "set", "ui.ascii", "true"], ctx=ctx)
    assert code == 0

    content = p.read_text(encoding="utf-8")
    assert "[custom_section]" in content
    assert 'custom_key = "custom_val"' in content
    assert "flag = true" in content
    assert "unknown_opt = 42" in content
    assert "ascii = true" in content

    ctx.out.truncate(0)
    ctx.out.seek(0)
    code = main(["config", "list"], ctx=ctx)
    assert code == 0
    out = ctx.out.getvalue()
    assert 'custom_section.custom_key = "custom_val"' in out
    assert "autopilot.unknown_opt = 42" in out


def test_invalid_key_raises_usage_error(
    ctx: AppContext, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["config", "set", "invalid_key", "123"], ctx=ctx)
    assert code == 64
    captured = capsys.readouterr()
    assert "Unknown configuration key" in captured.err


def test_missing_args_raise_usage_error(
    ctx: AppContext, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config", "get"], ctx=ctx) == 64
    captured = capsys.readouterr()
    assert "Missing key" in captured.err

    assert main(["config", "set"], ctx=ctx) == 64
    captured = capsys.readouterr()
    assert "Missing key" in captured.err

    assert main(["config", "set", "autopilot.threshold"], ctx=ctx) == 64
    captured = capsys.readouterr()
    assert "Missing value" in captured.err

    assert main(["config", "unset"], ctx=ctx) == 64
    captured = capsys.readouterr()
    assert "Missing key" in captured.err


@pytest.mark.parametrize(
    ("key", "val"),
    [
        ("autopilot.threshold", "49"),
        ("autopilot.threshold", "100"),
        ("autopilot.threshold", "invalid"),
        ("autopilot.margin", "-1"),
        ("autopilot.margin", "51"),
        ("autopilot.margin", "invalid"),
        ("autopilot.cooldown", "59"),
        ("autopilot.cooldown", "86401"),
        ("autopilot.cooldown", "invalid"),
        ("autopilot.strategy", "other"),
        ("autopilot.focus", "gpt"),
        ("autopilot.hook_action", "other"),
        ("ui.ascii", "yes"),
        ("ui.color", "blue"),
        ("updates.check", "no"),
    ],
)
def test_invalid_values_raise_usage_error(
    ctx: AppContext, capsys: pytest.CaptureFixture[str], key: str, val: str
) -> None:
    code = main(["config", "set", key, val], ctx=ctx)
    assert code == 64
    captured = capsys.readouterr()
    assert f"Invalid value for '{key}'" in captured.err
