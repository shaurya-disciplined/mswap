"""Performance and import isolation tests for mswap status command."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def test_status_imports_no_http_or_api_modules(tmp_path: Path) -> None:
    """Ensure the status execution path imports zero http/api modules."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["MSWAP_HOME"] = str(home)
    env["MSWAP_VAULT"] = "memory"
    env["MSWAP_LIVE_TARGET"] = "mswaptest:live"
    env["MSWAP_VAULT_PREFIX"] = "mswaptest:"
    env["MSWAP_NO_NETWORK"] = "1"

    code_snippet = """
import sys
from mswap.cli import main

# Run status command
exit_code = main(["status"])
assert exit_code == 0, f"Expected 0, got {exit_code}"

forbidden = ["mswap.util.http", "mswap.agy.api", "http.client", "urllib.request"]
imported_forbidden = [m for m in forbidden if m in sys.modules]
if imported_forbidden:
    print(f"FORBIDDEN_MODULES_IMPORTED: {imported_forbidden}", file=sys.stderr)
    sys.exit(1)
"""

    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", code_snippet],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    elapsed = time.perf_counter() - start

    assert proc.returncode == 0, f"Failed with stderr: {proc.stderr}"
    # Status is targeted for fast prompt invocation
    assert elapsed < 3.0  # Safe ceiling across CI and Windows subprocess overhead


def test_status_subprocess_execution_with_accounts(tmp_path: Path) -> None:
    """Run mswap status with configured accounts in a clean subprocess."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)

    # Seed accounts.json
    accounts_file = home / "accounts.json"
    accounts_json = (
        '{"schema": 2, "accounts": [{"slot": 1, "email": "alice@example.com", '
        '"fp": "fp1", "added_at": "2026-10-02T12:00:00+00:00", '
        '"updated_at": "2026-10-02T12:00:00+00:00"}]}'
    )
    accounts_file.write_text(accounts_json, encoding="utf-8")

    env = dict(os.environ)
    env["MSWAP_HOME"] = str(home)
    env["MSWAP_VAULT"] = "memory"
    env["MSWAP_LIVE_TARGET"] = "mswaptest:live"
    env["MSWAP_VAULT_PREFIX"] = "mswaptest:"
    env["MSWAP_NO_NETWORK"] = "1"

    proc = subprocess.run(
        [sys.executable, "-m", "mswap", "status"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    # No live login in memory vault -> silent exit 0
    assert proc.stdout == ""
