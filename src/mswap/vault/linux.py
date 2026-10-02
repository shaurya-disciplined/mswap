"""Linux Secret Service vault backend using the secret-tool CLI."""

from __future__ import annotations

import base64
import os
import re
import subprocess
from collections.abc import Callable
from typing import Any

from mswap.core.errors import VaultError
from mswap.util.systools import run_system
from mswap.vault.base import MAX_BLOB

_VALID_TARGET_CHARS = re.compile(r"^[A-Za-z0-9._:@+-]+$")
_PREFIX_BASE64 = b"go-keyring-base64:"
_PREFIX_ENCODED = b"go-keyring-encoded:"
_HINT_NO_DAEMON = (
    "Install gnome-keyring + libsecret-tools, or set MSWAP_VAULT=file "
    "(less secure) on headless machines."
)


class SecretToolVault:
    """Linux Secret Service implementation of Vault Protocol using `secret-tool`."""

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[Any]] = run_system,
    ) -> None:
        self._runner = runner

    def _live_target(self) -> str:
        return os.environ.get("MSWAP_LIVE_TARGET", "gemini:antigravity")

    def split(self, target: str) -> tuple[str, str]:
        """Split target into (service, account) at the first colon."""
        if ":" not in target:
            raise VaultError(
                f"Invalid target '{target}': target must contain ':' to separate "
                "service and account."
            )
        svc, acct = target.split(":", 1)
        if not svc or not acct:
            raise VaultError(
                f"Invalid target '{target}': service and account components cannot be empty."
            )
        if not _VALID_TARGET_CHARS.fullmatch(svc) or not _VALID_TARGET_CHARS.fullmatch(acct):
            raise VaultError(
                f"Invalid target '{target}': only alphanumeric characters and ._:@+- are allowed."
            )
        return svc, acct

    def attrs(self, target: str) -> list[str]:
        """Return secret-tool attribute list for target.

        go-keyring compatible attributes: service <svc> username <acct>.
        For mswap's own entries also add: mswap 1 so list() can find them.
        """
        svc, acct = self.split(target)
        res = ["service", svc, "username", acct]
        if target != self._live_target():
            res.extend(["mswap", "1"])
        return res

    def _check_dbus_error(self, err_text: str) -> None:
        lower = err_text.lower()
        if (
            "cannot autolaunch d-bus" in lower
            or "no secret service" in lower
            or "cannot autolaunch" in lower
            or "org.freedesktop.secrets" in lower
            or "is d-bus running" in lower
            or "failed to execute child process" in lower
            or "cannot spawn" in lower
        ):
            raise VaultError("No Secret Service available.", hint=_HINT_NO_DAEMON)

    def _run(
        self, cmd: list[str], input_data: bytes | None = None
    ) -> subprocess.CompletedProcess[Any]:
        try:
            res = self._runner(cmd, input=input_data, capture_output=True, text=False)
        except (FileNotFoundError, PermissionError) as e:
            raise VaultError("No Secret Service available.", hint=_HINT_NO_DAEMON) from e

        stderr = (
            res.stderr.decode("utf-8", errors="replace")
            if isinstance(res.stderr, bytes)
            else str(res.stderr or "")
        )
        stdout = (
            res.stdout.decode("utf-8", errors="replace")
            if isinstance(res.stdout, bytes)
            else str(res.stdout or "")
        )
        self._check_dbus_error(stderr)
        self._check_dbus_error(stdout)
        return res

    def _read_raw(self, target: str) -> bytes | None:
        attrs = self.attrs(target)
        cmd = ["secret-tool", "lookup", *attrs]
        res = self._run(cmd)
        if (res.returncode == 1 and not res.stdout) or (res.returncode == 0 and not res.stdout):
            return None
        if res.returncode != 0:
            err = (
                res.stderr.decode("utf-8", errors="replace").strip()
                if res.stderr
                else f"exit code {res.returncode}"
            )
            raise VaultError(f"Linux Secret Service error reading '{target}': {err}")

        raw: bytes = res.stdout if isinstance(res.stdout, bytes) else bytes(res.stdout or b"")
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        return raw

    def read(self, target: str) -> bytes | None:
        """Read and decode credential blob for target, or None if not found."""
        raw = self._read_raw(target)
        if raw is None:
            return None
        if raw.startswith(_PREFIX_BASE64):
            encoded = raw[len(_PREFIX_BASE64) :]
            try:
                return base64.b64decode(encoded)
            except Exception as e:
                raise VaultError(
                    f"Failed to decode base64 Secret Service payload for '{target}': {e}"
                ) from e
        if raw.startswith(_PREFIX_ENCODED):
            encoded = raw[len(_PREFIX_ENCODED) :]
            try:
                return bytes.fromhex(encoded.decode("ascii"))
            except Exception as e:
                raise VaultError(
                    f"Failed to decode hex Secret Service payload for '{target}': {e}"
                ) from e
        return raw

    def write(
        self,
        target: str,
        blob: bytes,
        user: str,  # noqa: ARG002 - Vault protocol parameter; user metadata is in accounts.json
    ) -> None:
        """Write credential blob for target using secret-tool store over stdin."""
        if len(blob) == 0:
            raise VaultError("Credential blob cannot be empty.")
        if len(blob) > MAX_BLOB:
            raise VaultError(f"Credential is too large ({len(blob)} bytes, max {MAX_BLOB}).")

        svc, acct = self.split(target)
        is_live = target == self._live_target()

        if is_live:
            existing_raw = self._read_raw(target)
            if existing_raw is not None and existing_raw.startswith(_PREFIX_BASE64):
                encoded = _PREFIX_BASE64.decode("ascii") + base64.b64encode(blob).decode("ascii")
            elif existing_raw is not None and existing_raw.startswith(_PREFIX_ENCODED):
                encoded = _PREFIX_ENCODED.decode("ascii") + blob.hex()
            elif existing_raw is not None:
                try:
                    encoded = blob.decode("utf-8")
                except UnicodeDecodeError:
                    encoded = _PREFIX_BASE64.decode("ascii") + base64.b64encode(blob).decode(
                        "ascii"
                    )
            else:
                try:
                    encoded = blob.decode("utf-8")
                except UnicodeDecodeError:
                    encoded = _PREFIX_BASE64.decode("ascii") + base64.b64encode(blob).decode(
                        "ascii"
                    )
        else:
            encoded = _PREFIX_BASE64.decode("ascii") + base64.b64encode(blob).decode("ascii")

        attrs = self.attrs(target)
        cmd = ["secret-tool", "store", "--label", f"{svc}:{acct}", *attrs]
        res = self._run(cmd, input_data=encoded.encode("utf-8"))
        if res.returncode != 0:
            err = (
                res.stderr.decode("utf-8", errors="replace").strip()
                if res.stderr
                else f"exit code {res.returncode}"
            )
            raise VaultError(f"Linux Secret Service error writing '{target}': {err}")

    def delete(self, target: str) -> bool:
        """Delete credential for target. Return True if deleted, False if not found."""
        if self._read_raw(target) is None:
            return False
        attrs = self.attrs(target)
        cmd = ["secret-tool", "clear", *attrs]
        res = self._run(cmd)
        if res.returncode != 0:
            err = (
                res.stderr.decode("utf-8", errors="replace").strip()
                if res.stderr
                else f"exit code {res.returncode}"
            )
            raise VaultError(f"Linux Secret Service error clearing '{target}': {err}")
        if self._read_raw(target) is not None:
            raise VaultError(f"Linux Secret Service error deleting '{target}': item still exists")
        return True

    def list(self, prefix: str) -> list[str]:
        """List all credential targets matching prefix, sorted."""
        if ":" in prefix:
            _svc, remainder = self.split(prefix + "x")
            _ = remainder[:-1]
        elif prefix == "":
            pass
        else:
            if not _VALID_TARGET_CHARS.fullmatch(prefix):
                raise VaultError(
                    f"Invalid prefix '{prefix}': only alphanumeric characters "
                    "and ._:@+- are allowed."
                )

        cmd = ["secret-tool", "search", "--all", "mswap", "1"]
        res = self._run(cmd)

        results: list[str] = []
        if res.returncode == 1 and not res.stdout and not res.stderr:
            results = []
        elif res.returncode != 0:
            err = (
                res.stderr.decode("utf-8", errors="replace").strip()
                if res.stderr
                else f"exit code {res.returncode}"
            )
            raise VaultError(f"Linux Secret Service error searching: {err}")
        else:
            stdout = (
                res.stdout.decode("utf-8", errors="replace")
                if isinstance(res.stdout, bytes)
                else str(res.stdout or "")
            )
            stderr = (
                res.stderr.decode("utf-8", errors="replace")
                if isinstance(res.stderr, bytes)
                else str(res.stderr or "")
            )
            combined = stdout + "\n" + stderr
            results = _parse_search_output(combined, prefix)

        live_tgt = self._live_target()
        if live_tgt.startswith(prefix) and live_tgt not in results:
            if self._read_raw(live_tgt) is not None:
                results.append(live_tgt)
                results.sort()

        return results


def _parse_search_output(output: str, prefix: str) -> list[str]:
    results: set[str] = set()
    current: dict[str, str] = {}

    def flush() -> None:
        nonlocal current
        if "service" in current and "username" in current:
            tgt = f"{current['service']}:{current['username']}"
            if tgt.startswith(prefix):
                results.add(tgt)
        elif "label" in current:
            lbl = current["label"]
            if ":" in lbl and lbl.startswith(prefix):
                results.add(lbl)
        current = {}

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("secret =") or line.startswith("secret="):
            continue
        if line.startswith("["):
            flush()
            continue
        if line.startswith("label =") or line.startswith("label="):
            parts = line.split("=", 1)
            if len(parts) == 2:
                current["label"] = parts[1].strip()
        elif line.startswith("attribute."):
            parts = line.split("=", 1)
            if len(parts) == 2:
                key = parts[0].strip()[len("attribute.") :].strip()
                val = parts[1].strip()
                if key in current:
                    flush()
                current[key] = val

    flush()
    return sorted(results)
