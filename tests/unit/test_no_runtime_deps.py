"""Unit test verifying that runtime dependencies remain empty."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_runtime_dependencies_are_empty() -> None:
    # Arrange
    pyproject_file = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"

    # Act
    with pyproject_file.open("rb") as f:
        data = tomllib.load(f)

    # Assert
    assert data["project"]["dependencies"] == []
