"""Spike runner for W2.S3: ADC session mode (U3) with billing STOP rules.

Safely tests whether AGY_ADC_AUTH + GOOGLE_APPLICATION_CREDENTIALS
can execute agy under a per-session account without touching the global login.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mswap.agy.client_discovery import discover
from mswap.agy.paths import agy_exe, data_dir
from mswap.util.http import UrllibHttp
from mswap.util.redact import redact, redact_emails
from mswap.vault.windows import WindowsVault

BILLING_STOP_KEYWORDS = [
    "billing",
    "project",
    "quota project",
    "vertex",
    "enable an api",
    "enable api",
    "x-goog-user-project",
    "cloud billing",
]

INTERACTIVE_STOP_KEYWORDS = [
    "sign in",
    "open your browser",
    "browser",
    "verification code",
    "paste the authorization code",
]


def run_mswap_list() -> dict[str, Any]:
    """Execute installed mswap list --json --refresh and return parsed output."""
    mswap_bin = shutil.which("mswap") or "mswap"
    res = subprocess.run(  # noqa: S603
        [mswap_bin, "list", "--json", "--refresh"],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"mswap list --refresh failed (code {res.returncode}): {res.stderr}")
    return json.loads(res.stdout)  # type: ignore[no-any-return]


def check_stop_rules(output: str) -> str | None:
    """Return reason if any STOP rule is triggered by output, else None."""
    lower = output.lower()
    for kw in BILLING_STOP_KEYWORDS:
        if kw in lower:
            return f"billing/project keyword detected: {kw!r}"
    for kw in INTERACTIVE_STOP_KEYWORDS:
        if kw in lower:
            return f"interactive prompt or browser login requested: {kw!r}"
    return None


def main() -> None:
    print("=== Step 1: Query current accounts via mswap list ===")
    list_before = run_mswap_list()
    data = list_before.get("data", {})
    active_slot = data.get("active_slot")
    accounts = data.get("accounts", [])
    print(f"Active slot: {active_slot}, total accounts: {len(accounts)}")

    # Pick candidate: highest slot number that is NOT active
    candidates = [acc for acc in accounts if acc.get("slot") != active_slot]
    if not candidates:
        print("RESULT: INCONCLUSIVE (needs >= 2 accounts)")
        return

    candidate = max(candidates, key=lambda a: int(a["slot"]))
    target_slot = int(candidate["slot"])
    print(f"Target non-active slot: {target_slot}")

    # Read target slot credential
    vault = WindowsVault()
    blob = vault.read(f"mswap:slot{target_slot}")
    if not blob:
        raise RuntimeError(f"Could not read credential for slot {target_slot}")

    cred_data = json.loads(blob.decode("utf-8"))
    token_dict = cred_data.get("token", {})
    refresh_token = token_dict.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh token in credential blob")

    # Discover OAuth client details
    client = discover(agy_exe(), data_dir() / "client.json", UrllibHttp())

    # Build authorized_user JSON
    auth_user = {
        "type": "authorized_user",
        "client_id": client.client_id,
        "client_secret": client.client_secret,
        "refresh_token": refresh_token,
    }

    temp_dir = Path(tempfile.mkdtemp(prefix="mswap_adc_"))
    adc_file = temp_dir / "adc_credentials.json"
    print(f"Created temp directory: {temp_dir}")

    attempts_log: list[dict[str, Any]] = []
    verdict = "INCONCLUSIVE"
    verdict_reason = ""

    try:
        adc_file.write_text(json.dumps(auth_user, indent=2), encoding="utf-8")

        # Restrict permissions with icacls
        username = os.environ.get("USERNAME", "")
        icacls_bin = shutil.which("icacls") or "icacls"
        if username:
            subprocess.run(  # noqa: S603
                [icacls_bin, str(adc_file), "/inheritance:r", "/grant:r", f"{username}:R"],
                capture_output=True,
                check=False,
            )

        exe = str(agy_exe())

        for attempt_idx, adc_val in enumerate(["1", "true"], start=1):
            print(f"\n--- Running Attempt {attempt_idx}: AGY_ADC_AUTH={adc_val} ---")
            env = os.environ.copy()
            env["GOOGLE_APPLICATION_CREDENTIALS"] = str(adc_file)
            env["AGY_ADC_AUTH"] = adc_val

            cmd = [
                exe,
                "-p",
                "Reply with exactly: OK",
                "--print-timeout",
                "120s",
                "--model",
                "Gemini 3.6 Flash (Low)",
            ]

            timed_out = False
            try:
                proc = subprocess.run(  # noqa: S603
                    cmd,
                    cwd=str(temp_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                stdout = proc.stdout
                stderr = proc.stderr
                returncode = proc.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
                returncode = -1

            combined = f"{stdout}\n{stderr}"
            redacted_stdout = redact_emails(redact(stdout))
            redacted_stderr = redact_emails(redact(stderr))

            print(f"Return code: {returncode}")
            print(f"Stdout:\n{redacted_stdout}")
            print(f"Stderr:\n{redacted_stderr}")

            attempt_info = {
                "attempt": attempt_idx,
                "adc_val": adc_val,
                "returncode": returncode,
                "timed_out": timed_out,
                "stdout": redacted_stdout,
                "stderr": redacted_stderr,
            }
            attempts_log.append(attempt_info)

            if timed_out:
                verdict = "NO-GO"
                verdict_reason = "Run exceeded 180s timeout"
                print(f"STOP TRIGGERED: {verdict_reason}")
                break

            stop_reason = check_stop_rules(combined)
            if stop_reason:
                verdict = "NO-GO"
                verdict_reason = stop_reason
                print(f"STOP TRIGGERED: {verdict_reason}")
                break

            # If child succeeded (printed OK)
            if "OK" in stdout:
                print("Child successfully printed OK!")
                break

    finally:
        # Secure cleanup
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"Deleted temp directory: {temp_dir} (exists: {temp_dir.exists()})")

    # Query quota after
    print("\n=== Step 5: Query quota after execution ===")
    list_after = run_mswap_list()

    # Save structured results to a file for inspection
    results_path = Path("adc_spike_results.json")
    results = {
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "attempts": attempts_log,
        "list_before": list_before,
        "list_after": list_after,
        "active_slot": active_slot,
        "target_slot": target_slot,
    }
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Saved results to {results_path}")


if __name__ == "__main__":
    main()
