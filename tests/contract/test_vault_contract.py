"""Contract test suite for vault backends."""

from __future__ import annotations

import functools
import sys
from collections.abc import Generator
from uuid import uuid4

import pytest

from mswap.core.errors import VaultError
from mswap.vault.base import Vault
from mswap.vault.memory import MemoryVault
from mswap.vault.windows import WindowsVault


class VaultContractBackend:
    """Wrapper around a Vault implementation managing test-scoped targets."""

    def __init__(self, name: str, vault: Vault, prefix: str) -> None:
        self.name = name
        self.vault = vault
        self.prefix = prefix
        self.created_targets: set[str] = set()

    def target(self, suffix: str = "item") -> str:
        """Create a full target name prefixed with the test's unique prefix."""
        return f"{self.prefix}{suffix}"

    def read(self, target: str) -> bytes | None:
        return self.vault.read(target)

    def write(self, target: str, blob: bytes, user: str) -> None:
        self.created_targets.add(target)
        self.vault.write(target, blob, user)

    def delete(self, target: str) -> bool:
        res = self.vault.delete(target)
        self.created_targets.discard(target)
        return res

    def list(self, prefix: str) -> list[str]:
        return self.vault.list(prefix)

    def read_user(self, target: str) -> str | None:
        read_user_fn = getattr(self.vault, "_read_user", None)
        if read_user_fn is not None:
            return read_user_fn(target)  # type: ignore[no-any-return]
        return None


def _has_macos_keychain() -> bool:
    if sys.platform != "darwin":
        return False
    import os
    import shutil

    return bool(os.environ.get("MSWAP_MAC_KEYCHAIN") or shutil.which("security"))


@functools.lru_cache(maxsize=1)
def _has_linux_secret_service() -> bool:
    if sys.platform != "linux":
        return False
    import shutil
    import subprocess

    if not shutil.which("secret-tool"):
        return False
    try:
        probe_svc = "mswap_probe"
        probe_user = f"probe_{uuid4().hex[:6]}"
        store = subprocess.run(
            [
                "secret-tool",
                "store",
                "--label",
                "mswap_probe",
                "service",
                probe_svc,
                "username",
                probe_user,
            ],
            input=b"probe",
            capture_output=True,
            timeout=5,
        )
        if store.returncode != 0:
            return False
        lookup = subprocess.run(
            ["secret-tool", "lookup", "service", probe_svc, "username", probe_user],
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["secret-tool", "clear", "service", probe_svc, "username", probe_user],
            capture_output=True,
            timeout=5,
        )
        return lookup.returncode == 0 and lookup.stdout.strip() == b"probe"
    except Exception:
        return False


def _contract_backends() -> list[str]:
    backends = ["memory"]
    if sys.platform == "win32":
        backends.append("windows")
    if _has_macos_keychain():
        backends.append("macos")
    if _has_linux_secret_service():
        backends.append("linux-secret-service")
    if sys.platform != "win32":
        backends.append("file")
    return backends


@pytest.fixture(params=_contract_backends())
def backend(request: pytest.FixtureRequest) -> Generator[VaultContractBackend, None, None]:
    name = request.param
    prefix = f"mswaptest:contract:{uuid4().hex[:8]}:"
    vault: Vault
    kc_path: Path | None = None
    tmp_dir: Path | None = None

    if name == "memory":
        vault = MemoryVault()
    elif name == "windows":
        vault = WindowsVault()
    elif name == "macos":
        import os
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        from mswap.vault.macos import MacKeychainVault

        if "MSWAP_MAC_KEYCHAIN" in os.environ:
            vault = MacKeychainVault(keychain=os.environ["MSWAP_MAC_KEYCHAIN"])
        else:
            tmp_dir = Path(tempfile.mkdtemp(prefix="mswap_kc_"))
            kc_path = tmp_dir / "mswaptest.keychain-db"
            res = subprocess.run(
                ["security", "create-keychain", "-p", "mswaptest", str(kc_path)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                pytest.skip(f"security create-keychain failed: {res.stderr}")
            subprocess.run(
                ["security", "unlock-keychain", "-p", "mswaptest", str(kc_path)],
                check=True,
                capture_output=True,
            )
            vault = MacKeychainVault(keychain=str(kc_path))
    elif name == "linux-secret-service":
        from mswap.vault.linux import SecretToolVault

        vault = SecretToolVault()
    elif name == "file":
        import tempfile
        from pathlib import Path

        from mswap.vault.file import FileVault

        tmp_dir = Path(tempfile.mkdtemp(prefix="mswap_file_vault_"))
        vault = FileVault(root=tmp_dir)
    else:
        raise ValueError(f"Unknown backend: {name}")

    be = VaultContractBackend(name=name, vault=vault, prefix=prefix)
    yield be

    # Finalizer: delete every target created and ensure list(prefix) is empty
    for t in list(be.created_targets):
        vault.delete(t)
    for t in vault.list(prefix):
        vault.delete(t)
    remaining = vault.list(prefix)
    assert remaining == [], f"Targets remained in {name} vault after cleanup: {remaining}"

    if name == "macos" and kc_path is not None and tmp_dir is not None:
        import shutil
        import subprocess

        subprocess.run(["security", "delete-keychain", str(kc_path)], capture_output=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    elif name == "file" and tmp_dir is not None:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_round_trip(backend: VaultContractBackend) -> None:
    target = backend.target("roundtrip")
    blob = b"sample-secret-payload"
    user = "alice@example.com"
    backend.write(target, blob, user)
    assert backend.read(target) == blob


def test_overwrite(backend: VaultContractBackend) -> None:
    target = backend.target("overwrite")
    backend.write(target, b"first-blob", "user1@example.com")
    assert backend.read(target) == b"first-blob"
    if backend.read_user(target) is not None:
        assert backend.read_user(target) == "user1@example.com"

    backend.write(target, b"second-blob", "user2@example.com")
    assert backend.read(target) == b"second-blob"
    if backend.read_user(target) is not None:
        assert backend.read_user(target) == "user2@example.com"


def test_read_missing_returns_none(backend: VaultContractBackend) -> None:
    target = backend.target("nonexistent")
    assert backend.read(target) is None


def test_delete_missing_returns_false(backend: VaultContractBackend) -> None:
    target = backend.target("nonexistent")
    assert backend.delete(target) is False


def test_delete_existing_returns_true(backend: VaultContractBackend) -> None:
    target = backend.target("to_delete")
    backend.write(target, b"payload", "user@example.com")
    assert backend.delete(target) is True
    assert backend.read(target) is None
    assert backend.delete(target) is False


def test_list_prefix_sorted_and_isolated(backend: VaultContractBackend) -> None:
    t2 = backend.target("item_b")
    t1 = backend.target("item_a")
    t3 = backend.target("item_c")
    backend.write(t2, b"b", "user@example.com")
    backend.write(t1, b"a", "user@example.com")
    backend.write(t3, b"c", "user@example.com")

    assert backend.list(backend.prefix) == [t1, t2, t3]
    assert backend.list(backend.target("nonexistent_prefix")) == []


def test_binary_safe_bytes(backend: VaultContractBackend) -> None:
    target = backend.target("binary_safe")
    all_bytes = bytes(range(256))
    backend.write(target, all_bytes, "binary@example.com")
    assert backend.read(target) == all_bytes


def test_max_size_blob_accepted(backend: VaultContractBackend) -> None:
    target = backend.target("max_blob")
    blob = b"x" * 2560
    backend.write(target, blob, "user@example.com")
    assert backend.read(target) == blob


def test_oversized_blob_raises_vault_error(backend: VaultContractBackend) -> None:
    target = backend.target("oversized")
    blob = b"x" * 2561
    with pytest.raises(VaultError):
        backend.write(target, blob, "user@example.com")


def test_empty_blob_raises_vault_error(backend: VaultContractBackend) -> None:
    target = backend.target("empty")
    with pytest.raises(VaultError):
        backend.write(target, b"", "user@example.com")


def test_unicode_user_roundtrips(backend: VaultContractBackend) -> None:
    target = backend.target("unicode_user")
    user = "Ünïcödé@example.com"
    blob = b"unicode-user-blob"
    backend.write(target, blob, user)
    assert backend.read(target) == blob
    if backend.read_user(target) is not None:
        assert backend.read_user(target) == user


def test_long_target_name(backend: VaultContractBackend) -> None:
    target = backend.target("long_" + "k" * 128)
    blob = b"long-name-blob"
    backend.write(target, blob, "user@example.com")
    assert backend.read(target) == blob


def test_list_sub_prefix_filtering(backend: VaultContractBackend) -> None:
    t_sub1 = backend.target("sub:item1")
    t_sub2 = backend.target("sub:item2")
    t_other = backend.target("other:item1")
    backend.write(t_sub1, b"1", "user@example.com")
    backend.write(t_sub2, b"2", "user@example.com")
    backend.write(t_other, b"3", "user@example.com")

    sub_prefix = backend.target("sub:")
    assert backend.list(sub_prefix) == [t_sub1, t_sub2]
