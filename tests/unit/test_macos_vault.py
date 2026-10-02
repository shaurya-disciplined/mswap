"""Unit tests for MacKeychainVault and macOS keychain backend."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from mswap.core.errors import VaultError
from mswap.vault.macos import MacKeychainVault


class FakeProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[FakeProcess] = []
        self.default_response = FakeProcess(0, b"", b"")

    def __call__(
        self,
        cmd: list[str],
        input: bytes | None = None,
        capture_output: bool = True,
        text: bool = False,
    ) -> FakeProcess:
        self.calls.append(
            {
                "cmd": list(cmd),
                "input": input,
                "capture_output": capture_output,
                "text": text,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return self.default_response


def test_split_target_valid() -> None:
    vault = MacKeychainVault()
    assert vault.split("mswap:slot1") == ("mswap", "slot1")
    assert vault.split("gemini:antigravity") == ("gemini", "antigravity")
    assert vault.split("mswaptest:contract:abcd:item") == ("mswaptest", "contract:abcd:item")
    assert vault.split("a.b-c+d_e:x@y:z") == ("a.b-c+d_e", "x@y:z")


def test_split_target_invalid_missing_colon() -> None:
    vault = MacKeychainVault()
    with pytest.raises(VaultError, match="must contain ':'"):
        vault.split("notarget")


def test_split_target_invalid_empty_parts() -> None:
    vault = MacKeychainVault()
    with pytest.raises(VaultError, match="cannot be empty"):
        vault.split(":account")
    with pytest.raises(VaultError, match="cannot be empty"):
        vault.split("service:")


def test_split_target_injection_rejected() -> None:
    vault = MacKeychainVault()
    injection_targets = [
        'a:b" -s evil',
        "a:b\nevil",
        "a:b\revil",
        "a:b; rm -rf /",
        "a:b evil",
        'a"evil:b',
        "a$evil:b",
        "a`evil`:b",
    ]
    for target in injection_targets:
        with pytest.raises(VaultError, match="only alphanumeric characters and"):
            vault.split(target)


def test_argv_never_contains_secret_or_blob() -> None:
    runner = FakeRunner()
    vault = MacKeychainVault(runner=runner)
    secret_bytes = b"super-secret-token-payload-xyz-12345"
    vault.write("mswap:slot1", secret_bytes, "alice@example.com")

    assert len(runner.calls) == 1
    call = runner.calls[0]
    # argv must be strictly ["security", "-i"]
    assert call["cmd"] == ["security", "-i"]

    b64_encoded = base64.b64encode(secret_bytes).decode("ascii")
    for arg in call["cmd"]:
        assert "super-secret" not in arg
        assert b64_encoded not in arg
        assert "alice@example.com" not in arg


def test_write_stdin_command_format() -> None:
    runner = FakeRunner()
    vault = MacKeychainVault(runner=runner)
    payload = b"test-payload"
    vault.write("mswap:slot3", payload, "alice@example.com")

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["input"] is not None
    stdin_text = call["input"].decode("utf-8")

    expected_b64 = "go-keyring-base64:" + base64.b64encode(payload).decode("ascii")
    expected_line = (
        f'add-generic-password -U -s mswap -a slot3 -l "mswap:slot3" -w {expected_b64}\n'
    )
    assert stdin_text == expected_line
    # User email must not be placed into label, comment, or command
    assert "alice@example.com" not in stdin_text


def test_write_with_custom_keychain() -> None:
    runner = FakeRunner()
    vault = MacKeychainVault(keychain="/tmp/mswaptest.keychain-db", runner=runner)
    vault.write("mswap:slot1", b"secret", "alice@example.com")

    call = runner.calls[0]
    stdin_text = call["input"].decode("utf-8")
    assert stdin_text.endswith(' "/tmp/mswaptest.keychain-db"\n')


def test_custom_keychain_injection_prevented() -> None:
    for invalid_kc in ['/tmp/evil"kc', "/tmp/evil\nkc", "/tmp/evil;kc"]:
        with pytest.raises(VaultError, match="Invalid characters in keychain path"):
            MacKeychainVault(keychain=invalid_kc)


def test_read_go_keyring_base64_prefix() -> None:
    runner = FakeRunner()
    raw_payload = b"my-binary-secret-\x00\xff\xfe"
    encoded = b"go-keyring-base64:" + base64.b64encode(raw_payload) + b"\n"
    runner.responses.append(FakeProcess(0, stdout=encoded))

    vault = MacKeychainVault(runner=runner)
    result = vault.read("mswap:slot1")
    assert result == raw_payload

    # Check find command arguments
    call = runner.calls[0]
    assert call["cmd"] == [
        "security",
        "find-generic-password",
        "-s",
        "mswap",
        "-a",
        "slot1",
        "-w",
    ]


def test_read_go_keyring_encoded_prefix() -> None:
    runner = FakeRunner()
    raw_payload = b"another-secret-token"
    encoded = b"go-keyring-encoded:" + raw_payload.hex().encode("ascii") + b"\n"
    runner.responses.append(FakeProcess(0, stdout=encoded))

    vault = MacKeychainVault(runner=runner)
    result = vault.read("mswap:slot2")
    assert result == raw_payload


def test_read_plain_without_prefix() -> None:
    runner = FakeRunner()
    raw_payload = b'{"access_token": "ya29.sample"}\n'
    runner.responses.append(FakeProcess(0, stdout=raw_payload))

    vault = MacKeychainVault(runner=runner)
    result = vault.read("gemini:antigravity")
    assert result == b'{"access_token": "ya29.sample"}'


def test_read_missing_item_returns_none() -> None:
    runner = FakeRunner()
    # security returns exit code 44 when item is not found
    runner.responses.append(
        FakeProcess(
            44,
            stdout=b"",
            stderr=b"SecKeychainSearchCopyNext: The item could not be found.\n",
        )
    )

    vault = MacKeychainVault(runner=runner)
    assert vault.read("mswap:nonexistent") is None


def test_read_corrupt_base64_raises_vault_error() -> None:
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, stdout=b"go-keyring-base64:!!!not-valid-b64!!!\n"))

    vault = MacKeychainVault(runner=runner)
    with pytest.raises(VaultError, match="Failed to decode base64"):
        vault.read("mswap:slot1")


def test_read_corrupt_hex_raises_vault_error() -> None:
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, stdout=b"go-keyring-encoded:not-valid-hex!\n"))

    vault = MacKeychainVault(runner=runner)
    with pytest.raises(VaultError, match="Failed to decode hex"):
        vault.read("mswap:slot1")


def test_write_live_target_always_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "mswaptest:live")
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, b"", b""))

    vault = MacKeychainVault(runner=runner)
    vault.write("mswaptest:live", b"new-token", "user")

    write_call = runner.calls[0]
    assert write_call["cmd"] == ["security", "-i"]
    stdin_text = write_call["input"].decode("utf-8")
    expected_b64 = "go-keyring-base64:" + base64.b64encode(b"new-token").decode("ascii")
    assert f"-w {expected_b64}" in stdin_text


def test_write_json_with_spaces_and_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "mswaptest:live")
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, b"", b""))

    vault = MacKeychainVault(runner=runner)
    json_bytes = b'{"access_token": "abc 123", "key": "value"}'
    vault.write("mswaptest:live", json_bytes, "user")

    call = runner.calls[0]
    stdin_text = call["input"].decode("utf-8")
    expected_b64 = "go-keyring-base64:" + base64.b64encode(json_bytes).decode("ascii")
    assert f"-w {expected_b64}" in stdin_text


def test_delete_generic_password() -> None:
    runner = FakeRunner()
    # Success
    runner.responses.append(FakeProcess(0, b"", b""))
    # Not found
    runner.responses.append(FakeProcess(44, b"", b"Not found"))
    # Other error
    runner.responses.append(FakeProcess(1, b"", b"Permission denied"))

    vault = MacKeychainVault(runner=runner)
    assert vault.delete("mswap:slot1") is True
    assert vault.delete("mswap:slot2") is False
    with pytest.raises(VaultError, match="Permission denied"):
        vault.delete("mswap:slot3")


def test_dump_keychain_parsing_realistic_fixture() -> None:
    fixture_dump = """
keychain: "/tmp/mswaptest.keychain-db"
version: 512
class: "genp"
attributes:
    0x00000000 "<uint32>"=0x00000000
    "acct"<blob>="slot2"
    "cdat"<timedate>=0x32303236313030323039303030305a00
    "svce"<blob>="mswap"
keychain: "/tmp/mswaptest.keychain-db"
version: 512
class: "genp"
attributes:
    "acct"<blob>="slot1"
    "svce"<blob>="mswap"
keychain: "/tmp/mswaptest.keychain-db"
version: 512
class: "inet"
attributes:
    "acct"<blob>="webuser"
    "svce"<blob>="https://example.com"
keychain: "/tmp/mswaptest.keychain-db"
version: 512
class: "genp"
attributes:
    "acct"<blob>="backup-last"
    "svce"<blob>="mswap"
keychain: "/tmp/mswaptest.keychain-db"
version: 512
class: "genp"
attributes:
    "acct"<blob>=0x616e746967726176697479
    "svce"<blob>=0x67656d696e69
"""
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, stdout=fixture_dump.encode("utf-8")))

    vault = MacKeychainVault(runner=runner)
    items = vault.list("mswap:")
    assert items == ["mswap:backup-last", "mswap:slot1", "mswap:slot2"]

    # Test sub-prefix
    runner.responses.append(FakeProcess(0, stdout=fixture_dump.encode("utf-8")))
    assert vault.list("mswap:slot") == ["mswap:slot1", "mswap:slot2"]

    # Test hex-decoded attributes (gemini:antigravity)
    runner.responses.append(FakeProcess(0, stdout=fixture_dump.encode("utf-8")))
    assert vault.list("gemini:") == ["gemini:antigravity"]

    # Test non-matching prefix
    runner.responses.append(FakeProcess(0, stdout=fixture_dump.encode("utf-8")))
    assert vault.list("other:") == []


def test_list_invalid_prefix_rejected() -> None:
    vault = MacKeychainVault()
    with pytest.raises(VaultError, match="only alphanumeric characters and"):
        vault.list('mswap:"evil')


def test_blob_size_checks() -> None:
    vault = MacKeychainVault()
    with pytest.raises(VaultError, match="cannot be empty"):
        vault.write("mswap:empty", b"", "user")

    with pytest.raises(VaultError, match="too large"):
        vault.write("mswap:oversized", b"x" * 2561, "user")


def test_read_error_raises_vault_error() -> None:
    runner = FakeRunner()
    runner.responses.append(FakeProcess(1, b"", b"SecKeychainItemCopyAttributesAndData error"))
    vault = MacKeychainVault(runner=runner)
    with pytest.raises(VaultError, match="macOS Keychain error reading"):
        vault.read("mswap:slot1")


def test_read_crlf_trailing_newline() -> None:
    runner = FakeRunner()
    raw = b"go-keyring-base64:" + base64.b64encode(b"payload-data") + b"\r\n"
    runner.responses.append(FakeProcess(0, stdout=raw))
    vault = MacKeychainVault(runner=runner)
    assert vault.read("mswap:slot1") == b"payload-data"


def test_write_error_raises_vault_error() -> None:
    runner = FakeRunner()
    runner.responses.append(FakeProcess(1, b"", b"Write failed: disk full"))
    vault = MacKeychainVault(runner=runner)
    with pytest.raises(VaultError, match="macOS Keychain error writing"):
        vault.write("mswap:slot1", b"payload", "user")


def test_dump_keychain_error_raises_vault_error() -> None:
    runner = FakeRunner()
    runner.responses.append(FakeProcess(1, b"", b"Dump error"))
    vault = MacKeychainVault(runner=runner)
    with pytest.raises(VaultError, match="macOS Keychain error dumping"):
        vault.list("mswap:")


def test_dump_keychain_empty_prefix() -> None:
    dump = """
keychain: "/tmp/kc"
class: "genp"
attributes:
    "acct"<blob>="slot1"
    "svce"<blob>="mswap"
"""
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, stdout=dump.encode()))
    vault = MacKeychainVault(runner=runner)
    assert vault.list("") == ["mswap:slot1"]


def test_dump_keychain_missing_attributes_ignored() -> None:
    dump = """
keychain: "/tmp/kc"
class: "genp"
attributes:
    "svce"<blob>="mswap"
keychain: "/tmp/kc"
class: "genp"
attributes:
    "acct"<blob>="slot2"
"""
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, stdout=dump.encode()))
    vault = MacKeychainVault(runner=runner)
    assert vault.list("mswap:") == []
