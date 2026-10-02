"""User configuration and settings loader for settings.toml.

Owns loading, validating, reading, and writing configuration settings per §A11.
Must never store credentials or perform network access.
"""

from __future__ import annotations

import tomllib
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mswap.core.errors import UsageError
from mswap.core.policy import Settings as PolicySettings
from mswap.core.store import data_dir
from mswap.util.fsx import write_private_text

VALID_STRATEGIES: set[str] = {"best", "consume-first"}
VALID_FOCUSES: set[str] = {"auto", "gemini", "3p", "both"}
VALID_COLORS: set[str] = {"auto", "always", "never"}
VALID_HOOK_ACTIONS: set[str] = {"notify", "switch"}

KNOWN_KEYS: dict[str, tuple[str, str]] = {
    "autopilot.threshold": ("autopilot", "threshold"),
    "autopilot.margin": ("autopilot", "margin"),
    "autopilot.cooldown": ("autopilot", "cooldown"),
    "autopilot.strategy": ("autopilot", "strategy"),
    "autopilot.focus": ("autopilot", "focus"),
    "autopilot.hook_action": ("autopilot", "hook_action"),
    "ui.ascii": ("ui", "ascii"),
    "ui.color": ("ui", "color"),
    "updates.check": ("updates", "check"),
}

SECTION_ORDER: list[str] = ["autopilot", "ui", "updates"]
KEY_ORDER: dict[str, list[str]] = {
    "autopilot": ["threshold", "margin", "cooldown", "strategy", "focus", "hook_action"],
    "ui": ["ascii", "color"],
    "updates": ["check"],
}


@dataclass(frozen=True)
class AutopilotConfig:
    """Autopilot section configuration."""

    threshold: int = 90
    margin: int = 10
    cooldown: int = 300
    strategy: Literal["best", "consume-first"] = "best"
    focus: Literal["auto", "gemini", "3p", "both"] = "auto"
    hook_action: Literal["notify", "switch"] = "notify"

    def to_policy_settings(self, max_data_age_s: int = 900) -> PolicySettings:
        """Convert to policy.Settings instance."""
        return PolicySettings(
            threshold=self.threshold,
            margin=self.margin,
            cooldown_s=self.cooldown,
            strategy=self.strategy,
            focus=self.focus,
            max_data_age_s=max_data_age_s,
        )


@dataclass(frozen=True)
class UiConfig:
    """UI presentation settings."""

    ascii: bool = False
    color: Literal["auto", "always", "never"] = "auto"


@dataclass(frozen=True)
class UpdatesConfig:
    """Update check settings."""

    check: bool = True


@dataclass(frozen=True)
class Settings:
    """Root configuration holding all settings sections."""

    autopilot: AutopilotConfig = field(default_factory=AutopilotConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)


def settings_path(path: Path | str | None = None) -> Path:
    """Return the resolved Path to settings.toml."""
    return Path(path) if path is not None else (data_dir() / "settings.toml")


def validate_key_value(key: str, val: Any) -> Any:
    """Validate a typed configuration value for a dotted key.

    Raises UsageError on invalid type, range, or enum value.
    """
    if key not in KNOWN_KEYS:
        allowed = ", ".join(sorted(KNOWN_KEYS.keys()))
        raise UsageError(
            f"Unknown configuration key '{key}'.",
            hint=f"Allowed keys: {allowed}.",
        )

    if key == "autopilot.threshold":
        if not isinstance(val, int) or isinstance(val, bool) or not (50 <= val <= 99):
            raise UsageError(
                "Invalid value for 'autopilot.threshold': must be an integer between 50 and 99.",
                hint="Allowed values: 50-99.",
            )
        return val

    if key == "autopilot.margin":
        if not isinstance(val, int) or isinstance(val, bool) or not (0 <= val <= 50):
            raise UsageError(
                "Invalid value for 'autopilot.margin': must be an integer between 0 and 50.",
                hint="Allowed values: 0-50.",
            )
        return val

    if key == "autopilot.cooldown":
        if not isinstance(val, int) or isinstance(val, bool) or not (60 <= val <= 86400):
            raise UsageError(
                "Invalid value for 'autopilot.cooldown': must be an integer between 60 and 86400.",
                hint="Allowed values: 60-86400.",
            )
        return val

    if key == "autopilot.strategy":
        if not isinstance(val, str) or val not in VALID_STRATEGIES:
            allowed = ", ".join(f"'{s}'" for s in sorted(VALID_STRATEGIES))
            raise UsageError(
                f"Invalid value for 'autopilot.strategy': '{val}'. Allowed values are {allowed}.",
                hint=f"Allowed values: {allowed}.",
            )
        return val

    if key == "autopilot.focus":
        if not isinstance(val, str) or val not in VALID_FOCUSES:
            allowed = ", ".join(f"'{f}'" for f in sorted(VALID_FOCUSES))
            raise UsageError(
                f"Invalid value for 'autopilot.focus': '{val}'. Allowed values are {allowed}.",
                hint=f"Allowed values: {allowed}.",
            )
        return val

    if key == "autopilot.hook_action":
        if not isinstance(val, str) or val not in VALID_HOOK_ACTIONS:
            allowed = ", ".join(f"'{a}'" for a in sorted(VALID_HOOK_ACTIONS))
            raise UsageError(
                f"Invalid value for 'autopilot.hook_action': '{val}'. "
                f"Allowed values are {allowed}.",
                hint=f"Allowed values: {allowed}.",
            )
        return val

    if key == "ui.ascii":
        if not isinstance(val, bool):
            raise UsageError(
                "Invalid value for 'ui.ascii': must be a boolean (true or false).",
                hint="Set ascii = false in settings.toml.",
            )
        return val

    if key == "ui.color":
        if not isinstance(val, str) or val not in VALID_COLORS:
            allowed = ", ".join(f"'{c}'" for c in sorted(VALID_COLORS))
            raise UsageError(
                f"Invalid value for 'ui.color': '{val}'. Allowed values are {allowed}.",
                hint=f"Allowed values: {allowed}.",
            )
        return val

    if key == "updates.check":
        if not isinstance(val, bool):
            raise UsageError(
                "Invalid value for 'updates.check': must be a boolean (true or false).",
                hint="Set check = true in settings.toml.",
            )
        return val

    return val


def parse_and_validate(key: str, raw_str: str) -> Any:
    """Parse raw string input from CLI and validate against key definition."""
    if key not in KNOWN_KEYS:
        allowed = ", ".join(sorted(KNOWN_KEYS.keys()))
        raise UsageError(
            f"Unknown configuration key '{key}'.",
            hint=f"Allowed keys: {allowed}.",
        )

    # Int keys
    if key in ("autopilot.threshold", "autopilot.margin", "autopilot.cooldown"):
        try:
            val = int(raw_str)
        except (ValueError, TypeError):
            return validate_key_value(key, raw_str)
        return validate_key_value(key, val)

    # Bool keys
    if key in ("ui.ascii", "updates.check"):
        if raw_str.lower() == "true":
            return True
        if raw_str.lower() == "false":
            return False
        return validate_key_value(key, raw_str)

    # Str / Enum keys
    return validate_key_value(key, raw_str)


def _validate_autopilot(data: dict[str, Any]) -> AutopilotConfig:
    """Validate and extract autopilot configuration section."""
    threshold = 90
    margin = 10
    cooldown = 300
    strategy: Literal["best", "consume-first"] = "best"
    focus: Literal["auto", "gemini", "3p", "both"] = "auto"
    hook_action: Literal["notify", "switch"] = "notify"

    for key, val in data.items():
        dotted = f"autopilot.{key}"
        if dotted in KNOWN_KEYS:
            validated = validate_key_value(dotted, val)
            if key == "threshold":
                threshold = validated
            elif key == "margin":
                margin = validated
            elif key == "cooldown":
                cooldown = validated
            elif key == "strategy":
                strategy = validated
            elif key == "focus":
                focus = validated
            elif key == "hook_action":
                hook_action = validated
        else:
            warnings.warn(
                f"Unknown settings key 'autopilot.{key}' is ignored.",
                UserWarning,
                stacklevel=3,
            )

    return AutopilotConfig(
        threshold=threshold,
        margin=margin,
        cooldown=cooldown,
        strategy=strategy,
        focus=focus,
        hook_action=hook_action,
    )


def _validate_ui(data: dict[str, Any]) -> UiConfig:
    """Validate and extract UI configuration section."""
    ascii_flag = False
    color: Literal["auto", "always", "never"] = "auto"

    for key, val in data.items():
        dotted = f"ui.{key}"
        if dotted in KNOWN_KEYS:
            validated = validate_key_value(dotted, val)
            if key == "ascii":
                ascii_flag = validated
            elif key == "color":
                color = validated
        else:
            warnings.warn(
                f"Unknown settings key 'ui.{key}' is ignored.",
                UserWarning,
                stacklevel=3,
            )

    return UiConfig(ascii=ascii_flag, color=color)


def _validate_updates(data: dict[str, Any]) -> UpdatesConfig:
    """Validate and extract updates configuration section."""
    check = True

    for key, val in data.items():
        dotted = f"updates.{key}"
        if dotted in KNOWN_KEYS:
            check = validate_key_value(dotted, val)
        else:
            warnings.warn(
                f"Unknown settings key 'updates.{key}' is ignored.",
                UserWarning,
                stacklevel=3,
            )

    return UpdatesConfig(check=check)


def _format_toml_value(val: Any) -> str:
    """Format a single Python value as a TOML scalar or list."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return str(val)
    if isinstance(val, str):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(val, (list, tuple)):
        items = [_format_toml_value(item) for item in val]
        return f"[{', '.join(items)}]"
    escaped = str(val).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _key_sorter(canonical: list[str]) -> Callable[[str], tuple[int, str]]:
    """Return a sort key placing canonical keys first, in canonical order."""

    def key_sort_key(k: str) -> tuple[int, str]:
        if k in canonical:
            return (0, f"{canonical.index(k):03d}")
        return (1, k)

    return key_sort_key


def dump_toml(data: dict[str, dict[str, Any]]) -> str:
    """Serialize flat tables dictionary into a TOML formatted string.

    Preserves unknown tables and keys while maintaining canonical section order.
    """
    if not data:
        return ""

    lines: list[str] = []

    def section_sort_key(sec: str) -> tuple[int, str]:
        if sec in SECTION_ORDER:
            return (0, f"{SECTION_ORDER.index(sec):03d}")
        return (1, sec)

    sections = sorted(data.keys(), key=section_sort_key)

    for sec in sections:
        table = data[sec]
        if not isinstance(table, dict):
            continue
        lines.append(f"[{sec}]")

        canonical_keys = KEY_ORDER.get(sec, [])

        keys = sorted(table.keys(), key=_key_sorter(canonical_keys))
        for k in keys:
            formatted_val = _format_toml_value(table[k])
            lines.append(f"{k} = {formatted_val}")

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def load_raw_toml(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Load settings.toml into a raw dictionary of tables without stripping unknown keys."""
    p = settings_path(path)
    if not p.exists():
        return {}
    try:
        content = p.read_bytes()
        loaded = tomllib.loads(content.decode("utf-8"))
        res: dict[str, dict[str, Any]] = {}
        for sec, val in loaded.items():
            if isinstance(val, dict):
                res[sec] = dict(val)
        return res
    except UsageError:
        raise
    except Exception as e:
        raise UsageError(
            f"Failed to parse settings file '{p}': {e}",
            hint="Check that settings.toml is valid TOML.",
        ) from e


def load_settings(path: Path | str | None = None) -> Settings:
    """Load settings from settings.toml or return default configuration if missing."""
    p = settings_path(path)
    if not p.exists():
        return Settings()

    try:
        content = p.read_bytes()
        data = tomllib.loads(content.decode("utf-8"))
    except UsageError:
        raise
    except Exception as e:
        raise UsageError(
            f"Failed to parse settings file '{p}': {e}",
            hint="Check that settings.toml is valid TOML.",
        ) from e

    autopilot_cfg = AutopilotConfig()
    ui_cfg = UiConfig()
    updates_cfg = UpdatesConfig()

    for section, sec_data in data.items():
        if section == "autopilot":
            if not isinstance(sec_data, dict):
                raise UsageError("Section [autopilot] must be a TOML table.")
            autopilot_cfg = _validate_autopilot(sec_data)
        elif section == "ui":
            if not isinstance(sec_data, dict):
                raise UsageError("Section [ui] must be a TOML table.")
            ui_cfg = _validate_ui(sec_data)
        elif section == "updates":
            if not isinstance(sec_data, dict):
                raise UsageError("Section [updates] must be a TOML table.")
            updates_cfg = _validate_updates(sec_data)
        else:
            warnings.warn(
                f"Unknown settings section '[{section}]' is ignored.",
                UserWarning,
                stacklevel=2,
            )

    return Settings(
        autopilot=autopilot_cfg,
        ui=ui_cfg,
        updates=updates_cfg,
    )


def get_setting(key: str, path: Path | str | None = None) -> tuple[Any, bool]:
    """Get the effective value and default status for a configuration key.

    Returns (value, is_default).
    """
    raw = load_raw_toml(path)
    if key in KNOWN_KEYS:
        sec, field_name = KNOWN_KEYS[key]
        if sec in raw and field_name in raw[sec]:
            return raw[sec][field_name], False
        defaults = Settings()
        sec_obj = getattr(defaults, sec)
        val = getattr(sec_obj, field_name)
        return val, True

    if "." in key:
        sec, field_name = key.split(".", 1)
        if sec in raw and field_name in raw[sec]:
            return raw[sec][field_name], False

    allowed = ", ".join(sorted(KNOWN_KEYS.keys()))
    raise UsageError(
        f"Unknown configuration key '{key}'.",
        hint=f"Allowed keys: {allowed}.",
    )


def set_setting(key: str, raw_val: Any, path: Path | str | None = None) -> Any:
    """Set and persist a configuration key in settings.toml.

    Creates the file and parent directories if they don't exist.
    Preserves unknown tables and keys.
    """
    if key not in KNOWN_KEYS:
        allowed = ", ".join(sorted(KNOWN_KEYS.keys()))
        raise UsageError(
            f"Unknown configuration key '{key}'.",
            hint=f"Allowed keys: {allowed}.",
        )

    val = (
        parse_and_validate(key, raw_val)
        if isinstance(raw_val, str)
        else validate_key_value(key, raw_val)
    )

    p = settings_path(path)
    raw = load_raw_toml(p)

    sec, field_name = KNOWN_KEYS[key]
    if sec not in raw:
        raw[sec] = {}
    raw[sec][field_name] = val

    toml_text = dump_toml(raw)
    write_private_text(p, toml_text)
    return val


def unset_setting(key: str, path: Path | str | None = None) -> None:
    """Remove a configuration key from settings.toml.

    If the key was not set, this is a no-op.
    Preserves unknown tables and keys.
    """
    p = settings_path(path)
    raw = load_raw_toml(p)

    if key not in KNOWN_KEYS:
        if "." in key:
            sec, field_name = key.split(".", 1)
            if sec in raw and field_name in raw[sec]:
                del raw[sec][field_name]
                if not raw[sec]:
                    del raw[sec]
                write_private_text(p, dump_toml(raw))
                return
        allowed = ", ".join(sorted(KNOWN_KEYS.keys()))
        raise UsageError(
            f"Unknown configuration key '{key}'.",
            hint=f"Allowed keys: {allowed}.",
        )

    sec, field_name = KNOWN_KEYS[key]
    if sec in raw and field_name in raw[sec]:
        del raw[sec][field_name]
        if not raw[sec]:
            del raw[sec]
        write_private_text(p, dump_toml(raw))


def list_settings(path: Path | str | None = None) -> list[tuple[str, Any, bool]]:
    """List all effective settings with their default status.

    Returns list of (dotted_key, value, is_default).
    """
    raw = load_raw_toml(path)
    defaults = Settings()
    items: list[tuple[str, Any, bool]] = []

    for dotted_key in (
        "autopilot.threshold",
        "autopilot.margin",
        "autopilot.cooldown",
        "autopilot.strategy",
        "autopilot.focus",
        "autopilot.hook_action",
        "ui.ascii",
        "ui.color",
        "updates.check",
    ):
        sec, field_name = KNOWN_KEYS[dotted_key]
        if sec in raw and field_name in raw[sec]:
            items.append((dotted_key, raw[sec][field_name], False))
        else:
            sec_obj = getattr(defaults, sec)
            val = getattr(sec_obj, field_name)
            items.append((dotted_key, val, True))

    for sec, table in raw.items():
        canonical_keys = KEY_ORDER.get(sec, [])
        for k, v in table.items():
            if k not in canonical_keys or sec not in SECTION_ORDER:
                items.append((f"{sec}.{k}", v, False))

    return items
