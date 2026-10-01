"""Unit tests for check_fixtures.py scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_fixtures import check_fixtures


def test_clean_fixtures_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    (clean_dir / "valid.json").write_text(
        '{"token": "ya29.FAKE-access-1", "email": "alice@example.com"}',
        encoding="utf-8",
    )

    # Act
    code = check_fixtures([clean_dir])
    out, err = capsys.readouterr()

    # Assert
    assert code == 0
    assert "fixtures clean (1 files)" in out
    assert err == ""


def test_each_pattern_detected_by_name_without_leaking_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    # Construct strings dynamically to avoid static scanner false positives in test source
    test_cases = [
        ("access_token", "ya29." + "REALTOKEN1234567890"),
        ("refresh_token", "1//" + "REALREFRESH1234567890"),
        ("client_secret", "GOCSPX-" + "SECRET123456789012345"),
        ("jwt", "eyJ" + "header1234" + "." + "payload1234" + "." + "sig12345678"),
        ("email", "danger" + "@" + "gmail.com"),
    ]

    for pattern_name, bad_str in test_cases:
        case_dir = tmp_path / pattern_name
        case_dir.mkdir()
        file_path = case_dir / "sample.json"
        file_path.write_text(f'{{"key": "{bad_str}"}}', encoding="utf-8")

        # Act
        code = check_fixtures([case_dir])
        out, _ = capsys.readouterr()

        # Assert
        assert code == 1, f"Expected violation for {pattern_name}"
        assert f":1: {pattern_name}" in out, f"Expected {pattern_name} in output"
        # Ensure the sensitive string itself was never printed
        assert bad_str not in out, f"Matched string {bad_str} was leaked in output"
