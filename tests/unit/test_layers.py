"""Unit tests for layer boundaries and scripts/check_layers.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_layers import check_layers, main


def test_existing_codebase_layers_ok() -> None:
    # Act
    exit_code = main([])

    # Assert
    assert exit_code == 0


def test_layer_violation_caught(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    # Create fake src structure where util imports ui (0 imports 4)
    src_dir = tmp_path / "src" / "mswap"
    util_dir = src_dir / "util"
    util_dir.mkdir(parents=True)
    bad_file = util_dir / "bad.py"
    bad_file.write_text("import mswap.ui.render\n", encoding="utf-8")

    # Act
    exit_code = check_layers(src_dir)
    out, _ = capsys.readouterr()

    # Assert
    assert exit_code == 1
    assert "layer violation: util (rank 0) imports mswap.ui.render (rank 4)" in out


def test_exempt_modules_allowed_across_layers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    # util importing mswap.core.errors and mswap.core.models
    src_dir = tmp_path / "src" / "mswap"
    util_dir = src_dir / "util"
    util_dir.mkdir(parents=True)
    valid_file = util_dir / "valid.py"
    valid_file.write_text(
        "from mswap.core.errors import VaultError\nfrom mswap.core.models import Account\n",
        encoding="utf-8",
    )

    # Act
    exit_code = check_layers(src_dir)
    out, _ = capsys.readouterr()

    # Assert
    assert exit_code == 0
    assert "layers ok" in out
