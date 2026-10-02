"""Unit tests for the W5.S1 POSIX probe script."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_posix_probe_script_structure() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    script_path = repo_root / "scripts" / "spikes" / "posix_probe.sh"
    assert script_path.is_file(), f"Probe script not found at {script_path}"

    content = script_path.read_text(encoding="utf-8")

    # Strict safety flags
    assert content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content

    # Prohibited dangerous commands in non-comment lines
    code_lines = [line.strip() for line in content.splitlines() if not line.strip().startswith("#")]
    assert not any(line.startswith("sudo ") or " sudo " in line for line in code_lines)
    assert not any("delete-generic-password" in line for line in code_lines)
    assert not any("secret-tool clear" in line for line in code_lines)

    # Required collection items
    assert "uname -sm" in content
    assert "command -v agy" in content
    assert "security find-generic-password -s gemini -a antigravity" in content
    assert "secret-tool lookup service gemini username antigravity" in content
    assert "secret-tool search service gemini username antigravity" in content
    assert "org.freedesktop.secrets" in content
    assert "Paste this whole file to agy. It contains no secrets." in content

    # Self-check safety net
    assert "ya29\\." in content or "ya29." in content
    assert "1//" in content
    assert "GOCSPX-" in content
    assert "secret =" in content
    assert "rm -f" in content


def test_posix_probe_bash_syntax() -> None:
    bash_bin = shutil.which("bash")
    if not bash_bin:
        # Fallback check for Git Bash on Windows
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if git_bash.is_file():
            bash_bin = str(git_bash)

    if not bash_bin:
        return

    repo_root = Path(__file__).resolve().parent.parent.parent
    script_path = repo_root / "scripts" / "spikes" / "posix_probe.sh"

    result = subprocess.run(
        [bash_bin, "-n", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"
