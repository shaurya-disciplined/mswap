"""User configuration and settings loader for settings.toml.

Owns loading and validating configuration settings per §A11.
Must never store credentials or perform network access.
"""

from __future__ import annotations

import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mswap.core.errors import UsageError
from mswap.core.policy import Settings as PolicySettings
from mswap.core.store import data_dir

VALID_STRATEGIES: set[str] = {"best", "consume-first"}
VALID_FOCUSES: set[str] = {"auto", "gemini", "3p", "both"}
VALID_COLORS: set[str] = {"auto", "always", "never"}


@dataclass(frozen=True)
class AutopilotConfig:
    """Autopilot section configuration."""

    threshold: int = 90
    margin: int = 10
    cooldown: int = 300
    strategy: Literal["best", "consume-first"] = "best"
    focus: Literal["auto", "gemini", "3p", "both"] = "auto"

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


def _validate_autopilot(data: dict[str, Any]) -> AutopilotConfig:
    """Validate and extract autopilot configuration section."""
    threshold = 90
    margin = 10
    cooldown = 300
    strategy: Literal["best", "consume-first"] = "best"
    focus: Literal["auto", "gemini", "3p", "both"] = "auto"

    for key, val in data.items():
        if key == "threshold":
            if not isinstance(val, int) or isinstance(val, bool) or not (0 <= val <= 100):
                raise UsageError(
                    "Invalid value for 'autopilot.threshold': "
                    "must be an integer between 0 and 100.",
                    hint="Set threshold = 90 in settings.toml.",
                )
            threshold = val
        elif key == "margin":
            if not isinstance(val, int) or isinstance(val, bool) or not (0 <= val <= 100):
                raise UsageError(
                    "Invalid value for 'autopilot.margin': must be an integer between 0 and 100.",
                    hint="Set margin = 10 in settings.toml.",
                )
            margin = val
        elif key == "cooldown":
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                raise UsageError(
                    "Invalid value for 'autopilot.cooldown': must be a non-negative integer.",
                    hint="Set cooldown = 300 in settings.toml.",
                )
            cooldown = val
        elif key == "strategy":
            if not isinstance(val, str) or val not in VALID_STRATEGIES:
                allowed = ", ".join(f"'{s}'" for s in sorted(VALID_STRATEGIES))
                raise UsageError(
                    f"Invalid value for 'autopilot.strategy': '{val}'. "
                    f"Allowed values are {allowed}.",
                    hint=f"Allowed values: {allowed}.",
                )
            strategy = val  # type: ignore[assignment]
        elif key == "focus":
            if not isinstance(val, str) or val not in VALID_FOCUSES:
                allowed = ", ".join(f"'{f}'" for f in sorted(VALID_FOCUSES))
                raise UsageError(
                    f"Invalid value for 'autopilot.focus': '{val}'. Allowed values are {allowed}.",
                    hint=f"Allowed values: {allowed}.",
                )
            focus = val  # type: ignore[assignment]
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
    )


def _validate_ui(data: dict[str, Any]) -> UiConfig:
    """Validate and extract UI configuration section."""
    ascii_flag = False
    color: Literal["auto", "always", "never"] = "auto"

    for key, val in data.items():
        if key == "ascii":
            if not isinstance(val, bool):
                raise UsageError(
                    "Invalid value for 'ui.ascii': must be a boolean (true or false).",
                    hint="Set ascii = false in settings.toml.",
                )
            ascii_flag = val
        elif key == "color":
            if not isinstance(val, str) or val not in VALID_COLORS:
                allowed = ", ".join(f"'{c}'" for c in sorted(VALID_COLORS))
                raise UsageError(
                    f"Invalid value for 'ui.color': '{val}'. Allowed values are {allowed}.",
                    hint=f"Allowed values: {allowed}.",
                )
            color = val  # type: ignore[assignment]
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
        if key == "check":
            if not isinstance(val, bool):
                raise UsageError(
                    "Invalid value for 'updates.check': must be a boolean (true or false).",
                    hint="Set check = true in settings.toml.",
                )
            check = val
        else:
            warnings.warn(
                f"Unknown settings key 'updates.{key}' is ignored.",
                UserWarning,
                stacklevel=3,
            )

    return UpdatesConfig(check=check)


def load_settings(path: Path | str | None = None) -> Settings:
    """Load settings from settings.toml or return default configuration if missing."""
    p = Path(path) if path is not None else (data_dir() / "settings.toml")
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
