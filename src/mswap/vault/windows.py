"""Windows Credential Manager vault backend using ctypes advapi32."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

from mswap.core.errors import VaultError
from mswap.vault.base import MAX_BLOB

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class _CRED(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD),
        ("Type", wt.DWORD),
        ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR),
        ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wt.DWORD),
        ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR),
    ]


class WindowsVault:
    """Windows Credential Manager implementation of Vault Protocol."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise VaultError("Windows Credential Manager is only available on Windows")
        self.forbidden: set[str] = (
            {"gemini:antigravity"} if os.environ.get("PYTEST_CURRENT_TEST") else set()
        )
        self._init_ctypes()

    def _init_ctypes(self) -> None:
        import ctypes
        import ctypes.wintypes as wt

        self._ctypes = ctypes
        self._wt = wt
        self._CRED = _CRED

        win_dll = getattr(ctypes, "WinDLL")  # noqa: B009
        self._adv = win_dll("advapi32", use_last_error=True)

        self._adv.CredReadW.argtypes = [
            wt.LPCWSTR,
            wt.DWORD,
            wt.DWORD,
            ctypes.POINTER(ctypes.POINTER(self._CRED)),
        ]
        self._adv.CredReadW.restype = wt.BOOL

        self._adv.CredWriteW.argtypes = [
            ctypes.POINTER(self._CRED),
            wt.DWORD,
        ]
        self._adv.CredWriteW.restype = wt.BOOL

        self._adv.CredDeleteW.argtypes = [
            wt.LPCWSTR,
            wt.DWORD,
            wt.DWORD,
        ]
        self._adv.CredDeleteW.restype = wt.BOOL

        self._adv.CredEnumerateW.argtypes = [
            wt.LPCWSTR,
            wt.DWORD,
            ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(self._CRED))),
        ]
        self._adv.CredEnumerateW.restype = wt.BOOL

        self._adv.CredFree.argtypes = [ctypes.c_void_p]
        self._adv.CredFree.restype = None

    def read(self, target: str) -> bytes | None:
        """Read credential blob for target, or None if not found."""
        if sys.platform != "win32":
            raise VaultError("Windows Credential Manager is only available on Windows")
        if target in self.forbidden:
            raise AssertionError(f"Target '{target}' is forbidden in tests.")
        ctypes = self._ctypes
        p = ctypes.POINTER(self._CRED)()
        if not self._adv.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
            err = ctypes.get_last_error()
            if err == _ERROR_NOT_FOUND:
                return None
            raise VaultError(
                f"Windows Credential Manager error {err}: {ctypes.FormatError(err).strip()}"
            )
        try:
            c = p.contents
            return bytes(ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize))
        finally:
            self._adv.CredFree(p)

    def write(self, target: str, blob: bytes, user: str) -> None:
        """Write credential blob for target with given user name."""
        if sys.platform != "win32":
            raise VaultError("Windows Credential Manager is only available on Windows")
        if target in self.forbidden:
            raise AssertionError(f"Target '{target}' is forbidden in tests.")
        if len(blob) == 0:
            raise VaultError("Credential blob cannot be empty.")
        if len(blob) > MAX_BLOB:
            raise VaultError(
                f"Credential is too large for Windows Credential Manager "
                f"({len(blob)} bytes, max {MAX_BLOB})."
            )
        ctypes = self._ctypes
        buf = ctypes.create_string_buffer(blob, len(blob))
        try:
            c = self._CRED()
            c.Type = _CRED_TYPE_GENERIC
            c.TargetName = target
            c.CredentialBlobSize = len(blob)
            c.CredentialBlob = ctypes.cast(buf, ctypes.c_void_p)
            c.Persist = _CRED_PERSIST_LOCAL_MACHINE
            c.UserName = user
            if not self._adv.CredWriteW(ctypes.byref(c), 0):
                err = ctypes.get_last_error()
                raise VaultError(
                    f"Windows Credential Manager error {err}: {ctypes.FormatError(err).strip()}"
                )
        finally:
            ctypes.memset(buf, 0, len(blob))

    def delete(self, target: str) -> bool:
        """Delete credential for target. Return True if deleted, False if not found."""
        if sys.platform != "win32":
            raise VaultError("Windows Credential Manager is only available on Windows")
        if target in self.forbidden:
            raise AssertionError(f"Target '{target}' is forbidden in tests.")
        ctypes = self._ctypes
        if not self._adv.CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
            err = ctypes.get_last_error()
            if err == _ERROR_NOT_FOUND:
                return False
            raise VaultError(
                f"Windows Credential Manager error {err}: {ctypes.FormatError(err).strip()}"
            )
        return True

    def list(self, prefix: str) -> list[str]:
        """List all credential target names matching prefix, sorted."""
        if sys.platform != "win32":
            raise VaultError("Windows Credential Manager is only available on Windows")
        ctypes = self._ctypes
        count = self._wt.DWORD()
        p_creds = ctypes.POINTER(ctypes.POINTER(self._CRED))()
        filter_str = f"{prefix}*"
        if not self._adv.CredEnumerateW(filter_str, 0, ctypes.byref(count), ctypes.byref(p_creds)):
            err = ctypes.get_last_error()
            if err == _ERROR_NOT_FOUND:
                return []
            raise VaultError(
                f"Windows Credential Manager error {err}: {ctypes.FormatError(err).strip()}"
            )
        try:
            results: list[str] = []
            for i in range(count.value):
                cred = p_creds[i].contents
                target = str(cred.TargetName)
                if target.startswith(prefix):
                    results.append(target)
            return sorted(results)
        finally:
            self._adv.CredFree(p_creds)

    def _read_user(self, target: str) -> str | None:
        """Test-only helper to read back the user name stored with a credential."""
        if sys.platform != "win32":
            raise VaultError("Windows Credential Manager is only available on Windows")
        if target in self.forbidden:
            raise AssertionError(f"Target '{target}' is forbidden in tests.")
        ctypes = self._ctypes
        p = ctypes.POINTER(self._CRED)()
        if not self._adv.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
            err = ctypes.get_last_error()
            if err == _ERROR_NOT_FOUND:
                return None
            raise VaultError(
                f"Windows Credential Manager error {err}: {ctypes.FormatError(err).strip()}"
            )
        try:
            c = p.contents
            return str(c.UserName) if c.UserName is not None else None
        finally:
            self._adv.CredFree(p)
