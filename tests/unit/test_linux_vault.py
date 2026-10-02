"""Unit tests for SecretToolVault and Linux Secret Service backend."""

from __future__ import annotations

import base64
import sys
from typing import Any

import pytest

from mswap.core.errors import VaultError
from mswap.vault import get_vault, reset_linux_warned, reset_memory_vault
from mswap.vault.linux import SecretToolVault, _parse_search_output


class FakeProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[FakeProcess | Exception] = []
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
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return self.default_response


def test_split_target_valid() -> None:
    vault = SecretToolVault()
    assert vault.split("mswap:slot1") == ("mswap", "slot1")
    assert vault.split("gemini:antigravity") == ("gemini", "antigravity")
    assert vault.split("mswaptest:contract:abcd:item") == ("mswaptest", "contract:abcd:item")
    assert vault.split("a.b-c+d_e:x@y:z") == ("a.b-c+d_e", "x@y:z")


def test_split_target_invalid_missing_colon() -> None:
    vault = SecretToolVault()
    with pytest.raises(VaultError, match="must contain ':'"):
        vault.split("notarget")


def test_split_target_invalid_empty_parts() -> None:
    vault = SecretToolVault()
    with pytest.raises(VaultError, match="cannot be empty"):
        vault.split(":account")
    with pytest.raises(VaultError, match="cannot be empty"):
        vault.split("service:")


def test_split_target_injection_rejected() -> None:
    vault = SecretToolVault()
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


def test_attrs_for_live_vs_mswap_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "gemini:antigravity")
    vault = SecretToolVault()

    # Live target does NOT include mswap 1 attribute (go-keyring compatibility)
    live_attrs = vault.attrs("gemini:antigravity")
    assert live_attrs == ["service", "gemini", "username", "antigravity"]

    # Mswap internal target includes mswap 1 attribute
    mswap_attrs = vault.attrs("mswap:slot1")
    assert mswap_attrs == ["service", "mswap", "username", "slot1", "mswap", "1"]


def test_argv_never_contains_secret_or_blob() -> None:
    runner = FakeRunner()
    vault = SecretToolVault(runner=runner)
    secret_bytes = b"super-secret-token-payload-xyz-12345"
    vault.write("mswap:slot1", secret_bytes, "alice@example.com")

    assert len(runner.calls) == 1
    call = runner.calls[0]
    # secret-tool store --label <label> <attrs...>
    cmd = call["cmd"]
    assert cmd[0] == "secret-tool"
    assert cmd[1] == "store"
    assert "--label" in cmd

    b64_encoded = base64.b64encode(secret_bytes).decode("ascii")
    for arg in cmd:
        assert "super-secret" not in arg
        assert b64_encoded not in arg
        assert "alice@example.com" not in arg

    # Secret must be sent exclusively through standard input
    assert call["input"] is not None
    assert b64_encoded.encode("ascii") in call["input"]


def test_write_stdin_command_format() -> None:
    runner = FakeRunner()
    vault = SecretToolVault(runner=runner)
    payload = b"test-payload"
    vault.write("mswap:slot3", payload, "alice@example.com")

    assert len(runner.calls) == 1
    call = runner.calls[0]
    expected_b64 = "go-keyring-base64:" + base64.b64encode(payload).decode("ascii")
    assert call["input"] == expected_b64.encode("utf-8")
    assert call["cmd"] == [
        "secret-tool",
        "store",
        "--label",
        "mswap:slot3",
        "service",
        "mswap",
        "username",
        "slot3",
        "mswap",
        "1",
    ]


def test_read_go_keyring_base64_prefix() -> None:
    runner = FakeRunner()
    raw_payload = b"my-binary-secret-\x00\xff\xfe"
    encoded = b"go-keyring-base64:" + base64.b64encode(raw_payload) + b"\n"
    runner.responses.append(FakeProcess(0, stdout=encoded))

    vault = SecretToolVault(runner=runner)
    result = vault.read("mswap:slot1")
    assert result == raw_payload

    call = runner.calls[0]
    assert call["cmd"] == [
        "secret-tool",
        "lookup",
        "service",
        "mswap",
        "username",
        "slot1",
        "mswap",
        "1",
    ]


def test_read_go_keyring_encoded_prefix() -> None:
    runner = FakeRunner()
    raw_payload = b"another-secret-token"
    encoded = b"go-keyring-encoded:" + raw_payload.hex().encode("ascii") + b"\n"
    runner.responses.append(FakeProcess(0, stdout=encoded))

    vault = SecretToolVault(runner=runner)
    result = vault.read("mswap:slot2")
    assert result == raw_payload


def test_read_plain_without_prefix() -> None:
    runner = FakeRunner()
    raw_payload = b'{"access_token": "ya29.sample"}\n'
    runner.responses.append(FakeProcess(0, stdout=raw_payload))

    vault = SecretToolVault(runner=runner)
    result = vault.read("gemini:antigravity")
    assert result == b'{"access_token": "ya29.sample"}'


def test_read_missing_item_returns_none() -> None:
    runner = FakeRunner()
    # secret-tool lookup exits 1 with empty stdout when item is not found
    runner.responses.append(FakeProcess(1, stdout=b"", stderr=b""))

    vault = SecretToolVault(runner=runner)
    assert vault.read("mswap:nonexistent") is None


def test_read_corrupt_base64_raises_vault_error() -> None:
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, stdout=b"go-keyring-base64:!!!not-valid-b64!!!\n"))

    vault = SecretToolVault(runner=runner)
    with pytest.raises(VaultError, match="Failed to decode base64"):
        vault.read("mswap:slot1")


def test_read_corrupt_hex_raises_vault_error() -> None:
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, stdout=b"go-keyring-encoded:not-valid-hex!\n"))

    vault = SecretToolVault(runner=runner)
    with pytest.raises(VaultError, match="Failed to decode hex"):
        vault.read("mswap:slot1")


def test_write_live_target_mirrors_existing_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "mswaptest:live")
    runner = FakeRunner()
    # 1. Existing live target has base64 prefix
    runner.responses.append(
        FakeProcess(0, stdout=b"go-keyring-base64:" + base64.b64encode(b"old-token") + b"\n")
    )
    # 2. Write succeeds
    runner.responses.append(FakeProcess(0, b"", b""))

    vault = SecretToolVault(runner=runner)
    vault.write("mswaptest:live", b"new-token", "user")

    write_call = runner.calls[1]
    assert write_call["cmd"] == [
        "secret-tool",
        "store",
        "--label",
        "mswaptest:live",
        "service",
        "mswaptest",
        "username",
        "live",
    ]
    expected_b64 = ("go-keyring-base64:" + base64.b64encode(b"new-token").decode("ascii")).encode(
        "utf-8"
    )
    assert write_call["input"] == expected_b64


def test_write_live_target_mirrors_existing_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "mswaptest:live")
    runner = FakeRunner()
    # 1. Existing live target has hex prefix
    runner.responses.append(
        FakeProcess(0, stdout=b"go-keyring-encoded:" + b"old-token".hex().encode() + b"\n")
    )
    # 2. Write succeeds
    runner.responses.append(FakeProcess(0, b"", b""))

    vault = SecretToolVault(runner=runner)
    vault.write("mswaptest:live", b"new-token", "user")

    write_call = runner.calls[1]
    expected_hex = ("go-keyring-encoded:" + b"new-token".hex()).encode("utf-8")
    assert write_call["input"] == expected_hex


def test_write_live_target_mirrors_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_LIVE_TARGET", "mswaptest:live")
    runner = FakeRunner()
    # 1. Existing live target is plain (no prefix)
    runner.responses.append(FakeProcess(0, stdout=b'{"plain": "token"}\n'))
    # 2. Write succeeds
    runner.responses.append(FakeProcess(0, b"", b""))

    vault = SecretToolVault(runner=runner)
    vault.write("mswaptest:live", b'{"new": "token"}', "user")

    write_call = runner.calls[1]
    assert write_call["input"] == b'{"new": "token"}'


def test_delete_cycle() -> None:
    runner = FakeRunner()
    # 1. Item exists during pre-delete lookup
    runner.responses.append(FakeProcess(0, stdout=b"existing-secret"))
    # 2. Clear command succeeds
    runner.responses.append(FakeProcess(0, b"", b""))
    # 3. Post-delete lookup verifies item is gone (exit code 1)
    runner.responses.append(FakeProcess(1, b"", b""))

    vault = SecretToolVault(runner=runner)
    assert vault.delete("mswap:slot1") is True

    assert runner.calls[1]["cmd"] == [
        "secret-tool",
        "clear",
        "service",
        "mswap",
        "username",
        "slot1",
        "mswap",
        "1",
    ]


def test_delete_missing_returns_false() -> None:
    runner = FakeRunner()
    # Item does not exist on initial lookup
    runner.responses.append(FakeProcess(1, stdout=b"", stderr=b""))

    vault = SecretToolVault(runner=runner)
    assert vault.delete("mswap:nonexistent") is False
    # Clear should not even be called if item is not found
    assert len(runner.calls) == 1


def test_delete_fails_if_item_still_exists_after_clear() -> None:
    runner = FakeRunner()
    # 1. Exists before clear
    runner.responses.append(FakeProcess(0, stdout=b"secret"))
    # 2. Clear exits 0
    runner.responses.append(FakeProcess(0, b"", b""))
    # 3. Lookup still returns secret (clear failed silently)
    runner.responses.append(FakeProcess(0, stdout=b"secret"))

    vault = SecretToolVault(runner=runner)
    with pytest.raises(VaultError, match="item still exists"):
        vault.delete("mswap:slot1")


def test_search_output_parsing_and_secret_ignoring() -> None:
    fixture_search = """
[/org/freedesktop/secrets/collection/login/1]
label = mswap:slot2
secret = ya29.LEAKED_SECRET_MUST_BE_DISCARDED_123
attribute.mswap = 1
attribute.service = mswap
attribute.username = slot2

[/org/freedesktop/secrets/collection/login/2]
label = mswap:slot1
secret = 1//ANOTHER_SECRET_THAT_MUST_NEVER_BE_KEPT
attribute.mswap = 1
attribute.service = mswap
attribute.username = slot1

[/org/freedesktop/secrets/collection/login/3]
label = other
attribute.service = other_service
attribute.username = other_user
"""
    # Verify the parser directly ignores secret lines
    targets = _parse_search_output(fixture_search, "mswap:")
    assert targets == ["mswap:slot1", "mswap:slot2"]

    # Verify via vault.list()
    runner = FakeRunner()
    runner.responses.append(FakeProcess(0, stdout=fixture_search.encode("utf-8")))
    vault = SecretToolVault(runner=runner)
    res = vault.list("mswap:")
    assert res == ["mswap:slot1", "mswap:slot2"]

    # Search command must search by mswap attribute
    assert runner.calls[0]["cmd"] == ["secret-tool", "search", "--all", "mswap", "1"]


def test_list_invalid_prefix_rejected() -> None:
    vault = SecretToolVault()
    with pytest.raises(VaultError, match="only alphanumeric characters and"):
        vault.list('mswap:"evil')


def test_blob_size_checks() -> None:
    vault = SecretToolVault()
    with pytest.raises(VaultError, match="cannot be empty"):
        vault.write("mswap:empty", b"", "user")

    with pytest.raises(VaultError, match="too large"):
        vault.write("mswap:oversized", b"x" * 2561, "user")


def test_missing_secret_tool_raises_vault_error_with_hint() -> None:
    runner = FakeRunner()
    runner.responses.append(FileNotFoundError("secret-tool not found"))
    vault = SecretToolVault(runner=runner)

    with pytest.raises(VaultError, match="No Secret Service available") as exc:
        vault.read("mswap:slot1")
    assert "Install gnome-keyring + libsecret-tools" in exc.value.hint


def test_dbus_autolaunch_error_mapped_to_vault_error() -> None:
    runner = FakeRunner()
    dbus_err = b"Cannot autolaunch D-Bus without X11 $DISPLAY"
    runner.responses.append(FakeProcess(1, stdout=b"", stderr=dbus_err))
    vault = SecretToolVault(runner=runner)

    with pytest.raises(VaultError, match="No Secret Service available") as exc:
        vault.read("mswap:slot1")
    assert "MSWAP_VAULT=file" in exc.value.hint


def test_get_vault_linux_experimental_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("MSWAP_VAULT", "native")
    monkeypatch.delenv("MSWAP_ACK_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("MSWAP_DEMO", raising=False)
    reset_linux_warned()
    reset_memory_vault()

    v = get_vault()
    assert isinstance(v, SecretToolVault)
    captured = capsys.readouterr()
    assert "Linux support is experimental. See docs/platforms.md." in captured.err

    # Second call in same process should NOT print notice again
    _ = get_vault()
    captured2 = capsys.readouterr()
    assert captured2.err == ""


def test_get_vault_linux_acknowledged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("MSWAP_VAULT", "native")
    monkeypatch.setenv("MSWAP_ACK_EXPERIMENTAL", "1")
    monkeypatch.delenv("MSWAP_DEMO", raising=False)
    reset_linux_warned()
    reset_memory_vault()

    v = get_vault()
    assert isinstance(v, SecretToolVault)
    captured = capsys.readouterr()
    assert captured.err == ""
