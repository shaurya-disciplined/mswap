"""Tests for mswap version and version CLI flag."""

from __future__ import annotations

import pytest

import mswap
from mswap.cli import main


def test_version_is_semver() -> None:
    assert mswap.__version__ == "0.1.0"


def test_main_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--version"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == "mswap 0.1.0 · not affiliated with Google\n"

    rc_short = main(["-V"])
    assert rc_short == 0
    captured_short = capsys.readouterr()
    assert captured_short.out == "mswap 0.1.0 · not affiliated with Google\n"


def test_main_unknown_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["nope"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "mswap: switch Google accounts in agy" in captured.out
