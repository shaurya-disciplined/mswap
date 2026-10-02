import json
from pathlib import Path

import pytest

from mswap import __version__
from mswap.cli import main


def test_version_no_agy(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--version"])
    assert code == 0
    out, _err = capsys.readouterr()
    assert out.strip() == f"mswap {__version__} (agy not found) · not affiliated with Google"


def test_version_with_agy(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Set up fake agy and client.json
    exe = tmp_path / "fake_agy.exe"
    exe.write_bytes(b"dummy")
    monkeypatch.setattr("mswap.agy.paths.agy_exe", lambda: exe)

    # Write client.json
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr("mswap.agy.paths.data_dir", lambda: data)

    client_path = data / "client.json"
    client_path.write_text(
        json.dumps(
            {"exe_sig": f"{exe.stat().st_size}:{int(exe.stat().st_mtime)}", "agy_version": "1.2.12"}
        ),
        encoding="utf-8",
    )

    code = main(["--version"])
    assert code == 0
    out, _err = capsys.readouterr()
    assert out.strip() == f"mswap {__version__} (agy 1.2.12) · not affiliated with Google"
