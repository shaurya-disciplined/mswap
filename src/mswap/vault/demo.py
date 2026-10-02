"""JSON file-backed demo vault for simulated environments (MSWAP_DEMO=1).

Used strictly for recording demo GIFs and running simulated offline scenarios.
Must never be used with real credentials.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from mswap.core.errors import VaultError
from mswap.vault.base import MAX_BLOB


class DemoVault:
    """A file-backed Vault implementation for offline demo tapes and testing."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, tuple[str, str]]:
        if not self.path.exists():
            return {}
        with contextlib.suppress(Exception):
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                result: dict[str, tuple[str, str]] = {}
                for k, v in raw.items():
                    if isinstance(v, list) and len(v) == 2:
                        result[k] = (str(v[0]), str(v[1]))
                return result
        return {}

    def _save(self, data: dict[str, tuple[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def read(self, target: str) -> bytes | None:
        """Read credential blob for target."""
        data = self._load()
        if target in data:
            hex_data, _ = data[target]
            return bytes.fromhex(hex_data)
        return None

    def write(self, target: str, blob: bytes, user: str) -> None:
        """Store credential blob for target."""
        if len(blob) > MAX_BLOB:
            raise VaultError(
                f"Blob exceeds maximum size ({MAX_BLOB} bytes).",
                hint="Credentials shouldn't be larger than 2.5 KB.",
            )
        data = self._load()
        data[target] = (blob.hex(), user)
        self._save(data)

    def delete(self, target: str) -> bool:
        """Delete credential for target."""
        data = self._load()
        if target in data:
            del data[target]
            self._save(data)
            return True
        return False

    def list(self, prefix: str) -> list[str]:
        """List all targets matching prefix."""
        data = self._load()
        return [t for t in data if t.startswith(prefix)]
