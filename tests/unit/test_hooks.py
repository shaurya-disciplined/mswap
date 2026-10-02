"""Tests for agy/hooks.py, cli/commands/hook.py, and auto --from-hook.

Meteor's hooks.json contains:
- "stop-notification" with a Stop hook
- "permission-notification" with a PreToolUse hook

These MUST be preserved byte-for-byte after install and after remove.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest

from mswap.agy.hooks import HOOK_SET, _load_hooks, hooks_path, install, remove, status
from mswap.agy.tokens import fingerprint
from mswap.cli.commands.auto import run as run_auto
from mswap.cli.commands.hook import run as run_hook
from mswap.cli.context import AppContext
from mswap.core.errors import CorruptState, UsageError
from mswap.core.models import Account, Bucket, Pool, QuotaSnapshot
from mswap.core.store import live_target, slot_target
from mswap.core.usage_cache import UsageCache
from tests.conftest import make_blob

# Meteor's exact hooks.json shape (with placeholder commands, no personal paths)
METEOR_HOOKS: dict = {
    "stop-notification": {
        "Stop": [
            {
                "type": "command",
                "command": (
                    'powershell -ExecutionPolicy Bypass -File "C:\\fake\\send_notification.ps1"'
                    ' -Title "Goal Completed"'
                ),
            }
        ]
    },
    "permission-notification": {
        "PreToolUse": [
            {
                "matcher": "ask_permission",
                "hooks": [
                    {
                        "type": "command",
                        "command": (
                            'powershell -ExecutionPolicy Bypass -File "C:\\fake\\notify.ps1"'
                            ' -Title "Permission Required"'
                        ),
                    }
                ],
            }
        ]
    },
}


def _write_hooks(tmp_path: pathlib.Path, data: dict) -> pathlib.Path:
    p = tmp_path / "hooks.json"
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def _make_snapshot(rem: float, now: Any, reset_hours: int = 3) -> QuotaSnapshot:
    bucket = Bucket(window="5h", remaining=rem, reset_at=now + timedelta(hours=reset_hours))
    pool = Pool(key="gemini", name="Gemini", buckets=(bucket,))
    return QuotaSnapshot(fetched_at=now, pools=(pool,))


def _setup_accounts(ctx: AppContext) -> list[Account]:
    now = ctx.clock.now()
    accounts: list[Account] = []
    for i in (1, 2):
        blob = make_blob(i)
        fp = fingerprint(blob)
        acc = Account(
            slot=i,
            email=f"user{i}@example.com",
            fp=fp,
            added_at=now,
            updated_at=now,
        )
        accounts.append(acc)
        ctx.vault.write(slot_target(i), blob, f"user{i}@example.com")

    ctx.store.save(accounts)
    ctx.vault.write(live_target(), make_blob(1), "antigravity")
    return accounts


class TestInstall:
    """Tests for hooks.install()."""

    def test_install_creates_file_when_missing(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing hooks.json is created with only the mswap-autopilot set."""
        hooks_file = tmp_path / "hooks.json"
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        changed = install(command="echo test")

        assert changed is True
        assert hooks_file.exists()
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert HOOK_SET in data
        assert list(data.keys()) == [HOOK_SET]

    def test_install_preserves_other_sets(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Other hook sets are preserved byte-for-byte after install."""
        hooks_file = _write_hooks(tmp_path, METEOR_HOOKS)
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        install(command="echo mswap")

        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert data["stop-notification"] == METEOR_HOOKS["stop-notification"]
        assert data["permission-notification"] == METEOR_HOOKS["permission-notification"]
        assert HOOK_SET in data

    def test_install_idempotent(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Installing twice with the same command returns False on the second call."""
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        first = install(command="echo test")
        second = install(command="echo test")

        assert first is True
        assert second is False

    def test_install_updates_on_command_change(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-installing with a different command returns True (changed)."""
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        install(command="echo v1")
        changed = install(command="echo v2")

        assert changed is True
        data = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))
        assert data[HOOK_SET]["Stop"][0]["command"] == "echo v2"

    def test_install_creates_backup_first_time(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A backup hooks.json.mswap-bak is created on first install."""
        _write_hooks(tmp_path, METEOR_HOOKS)
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        install(command="echo test")

        bak = tmp_path / "hooks.json.mswap-bak"
        assert bak.exists()
        bak_data = json.loads(bak.read_text(encoding="utf-8"))
        assert bak_data == METEOR_HOOKS

    def test_install_does_not_overwrite_existing_backup(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Backup is created only on first install; subsequent installs don't overwrite it."""
        _write_hooks(tmp_path, METEOR_HOOKS)
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        install(command="echo v1")
        bak_bytes_after_first = (tmp_path / "hooks.json.mswap-bak").read_bytes()

        install(command="echo v2")
        bak_bytes_after_second = (tmp_path / "hooks.json.mswap-bak").read_bytes()

        assert bak_bytes_after_first == bak_bytes_after_second

    def test_install_corrupt_json_raises_corrupt_state(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Corrupt hooks.json raises CorruptState; nothing is written."""
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text("{not valid json", encoding="utf-8")
        original_bytes = hooks_file.read_bytes()
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        with pytest.raises(CorruptState, match="invalid JSON"):
            install(command="echo test")

        assert hooks_file.read_bytes() == original_bytes

    def test_install_atomic_write(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Install writes via a .tmp file (no .tmp left behind)."""
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install(command="echo test")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Unexpected .tmp files: {tmp_files}"

    def test_install_default_command(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling install() with no argument uses default command with sys.executable."""
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install()
        data = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))
        cmd = data[HOOK_SET]["Stop"][0]["command"]
        assert sys.executable in cmd
        assert "--from-hook" in cmd


class TestRemove:
    """Tests for hooks.remove()."""

    def test_remove_returns_false_when_not_installed(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """remove() on a hooks.json that doesn't have mswap-autopilot returns False."""
        _write_hooks(tmp_path, METEOR_HOOKS)
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        changed = remove()

        assert changed is False

    def test_remove_returns_false_when_file_missing(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """remove() when hooks.json doesn't exist returns False."""
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        changed = remove()
        assert changed is False

    def test_remove_deletes_only_mswap_set(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """remove() deletes only the mswap-autopilot set; others are untouched."""
        _write_hooks(tmp_path, METEOR_HOOKS)
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install(command="echo test")

        changed = remove()

        assert changed is True
        data = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))
        assert HOOK_SET not in data
        assert data["stop-notification"] == METEOR_HOOKS["stop-notification"]
        assert data["permission-notification"] == METEOR_HOOKS["permission-notification"]

    def test_other_sets_byte_identical_after_install_then_remove(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Round-trip install → remove leaves other sets exactly as before."""
        hooks_file = _write_hooks(tmp_path, METEOR_HOOKS)
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        install(command="echo test")
        remove()

        data_after = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert data_after == METEOR_HOOKS


class TestStatus:
    """Tests for hooks.status()."""

    def test_status_not_installed(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """status() returns installed=False when file is missing."""
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        info = status()
        assert info["installed"] is False
        assert info["command"] is None
        assert info["backup_exists"] is False

    def test_status_installed(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """status() returns installed=True and the command after install."""
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install(command="echo hook-cmd")

        info = status()

        assert info["installed"] is True
        assert info["command"] == "echo hook-cmd"

    def test_status_reports_backup_exists(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """status() reports backup_exists=True when backup file is present."""
        _write_hooks(tmp_path, METEOR_HOOKS)
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install(command="echo test")

        info = status()

        assert info["backup_exists"] is True

    def test_status_corrupt_json(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """status() on a corrupt hooks.json returns installed=False (doesn't raise)."""
        (tmp_path / "hooks.json").write_text("{invalid", encoding="utf-8")
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))

        info = status()

        assert info["installed"] is False


class TestHooksEdgeCases:
    """Edge cases for agy/hooks.py."""

    def test_hooks_path_without_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MSWAP_AGY_STATE", raising=False)
        p = hooks_path()
        assert p.name == "hooks.json"
        assert ".gemini" in str(p)

    def test_load_hooks_non_dict_json(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "hooks.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(CorruptState, match="not a JSON object"):
            _load_hooks(p)

    def test_load_hooks_os_error(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "hooks.json"
        p.write_text("{}", encoding="utf-8")
        with (
            patch.object(pathlib.Path, "read_text", side_effect=OSError("disk error")),
            pytest.raises(CorruptState, match=r"Failed to read hooks\.json"),
        ):
            _load_hooks(p)


class TestHookCliCommand:
    """Tests for cli/commands/hook.py CLI interface."""

    def test_hook_cli_missing_action(self, ctx: AppContext) -> None:
        args = argparse.Namespace(hook_action=None)
        with pytest.raises(UsageError, match="Missing hook action"):
            run_hook(ctx, args)

    def test_hook_cli_invalid_action(self, ctx: AppContext) -> None:
        args = argparse.Namespace(hook_action="bogus")
        with pytest.raises(UsageError, match="Invalid hook action"):
            run_hook(ctx, args)

    def test_hook_cli_install_human_output(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        args = argparse.Namespace(hook_action="install")
        ret = run_hook(ctx, args)
        assert ret == 0
        out = ctx.out.getvalue()
        assert "Installed hook set 'mswap-autopilot'" in out
        assert str(tmp_path / "hooks.json") in out
        assert "Your other hooks are untouched." in out

    def test_hook_cli_install_already_installed(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install()
        ctx.out.truncate(0)
        ctx.out.seek(0)
        args = argparse.Namespace(hook_action="install")
        ret = run_hook(ctx, args)
        assert ret == 0
        out = ctx.out.getvalue()
        assert "Hook set 'mswap-autopilot' is already installed." in out
        assert "Your other hooks are untouched." in out

    def test_hook_cli_install_json(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        ctx.json = True
        args = argparse.Namespace(hook_action="install")
        ret = run_hook(ctx, args)
        assert ret == 0
        payload = json.loads(ctx.out.getvalue())
        assert payload["ok"] is True
        assert payload["data"]["action"] == "install"
        assert payload["data"]["set_name"] == HOOK_SET
        assert payload["data"]["changed"] is True

    def test_hook_cli_remove_human_output(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install()
        ctx.out.truncate(0)
        ctx.out.seek(0)
        args = argparse.Namespace(hook_action="remove")
        ret = run_hook(ctx, args)
        assert ret == 0
        out = ctx.out.getvalue()
        assert "Removed hook set 'mswap-autopilot'" in out

    def test_hook_cli_remove_not_installed(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        args = argparse.Namespace(hook_action="remove")
        ret = run_hook(ctx, args)
        assert ret == 0
        out = ctx.out.getvalue()
        assert "Not installed" in out

    def test_hook_cli_remove_json(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install()
        ctx.json = True
        args = argparse.Namespace(hook_action="remove")
        ret = run_hook(ctx, args)
        assert ret == 0
        payload = json.loads(ctx.out.getvalue())
        assert payload["ok"] is True
        assert payload["data"]["action"] == "remove"
        assert payload["data"]["changed"] is True

    def test_hook_cli_status_human_output(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        args = argparse.Namespace(hook_action="status")
        ret = run_hook(ctx, args)
        assert ret == 0
        out = ctx.out.getvalue()
        assert "mswap-autopilot hook is not installed" in out

        install(command="echo mycmd")
        ctx.out.truncate(0)
        ctx.out.seek(0)
        ret = run_hook(ctx, args)
        assert ret == 0
        out = ctx.out.getvalue()
        assert "mswap-autopilot hook is installed" in out
        assert "command: echo mycmd" in out

    def test_hook_cli_status_with_backup(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_hooks(tmp_path, METEOR_HOOKS)
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        install(command="echo mycmd")
        ctx.out.truncate(0)
        ctx.out.seek(0)
        args = argparse.Namespace(hook_action="status")
        ret = run_hook(ctx, args)
        assert ret == 0
        out = ctx.out.getvalue()
        assert "backup: hooks.json.mswap-bak exists" in out

    def test_hook_cli_status_json(
        self, ctx: AppContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path))
        ctx.json = True
        args = argparse.Namespace(hook_action="status")
        ret = run_hook(ctx, args)
        assert ret == 0
        payload = json.loads(ctx.out.getvalue())
        assert payload["ok"] is True
        assert payload["data"]["installed"] is False


class TestAutoFromHook:
    """Tests for auto --from-hook execution, budget, and notify/switch actions."""

    def test_from_hook_budget_exceeded_returns_zero_immediately(
        self, ctx: AppContext, tmp_path: pathlib.Path
    ) -> None:
        """When budget is already expired (>= 8.0s), exit 0 with no action."""
        _setup_accounts(ctx)
        args = argparse.Namespace(from_hook=True)
        # Timer returning 8.5s elapsed
        ret = run_auto(ctx, args)
        assert ret == 0

    def test_from_hook_notify_mode_switch(self, ctx: AppContext, tmp_path: pathlib.Path) -> None:
        """In notify mode, when policy triggers switch, print notification message."""
        accounts = _setup_accounts(ctx)
        now = ctx.clock.now()
        cache = UsageCache(ctx.store.root / "usage.json")
        cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)
        cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

        # Write settings with hook_action = "notify"
        cfg_file = ctx.store.root / "settings.toml"
        cfg_file.write_text('[autopilot]\nhook_action = "notify"\nthreshold = 90\n')

        args = argparse.Namespace(from_hook=True)
        ret = run_auto(ctx, args)
        assert ret == 0

        out = ctx.out.getvalue()
        assert "mswap: account 2 is better now." in out
        assert "Run `mswap switch --resume` after this turn." in out

        # Live credential was NOT switched
        live = ctx.vault.read(live_target())
        assert live == make_blob(1)

    def test_from_hook_switch_mode_switch(self, ctx: AppContext, tmp_path: pathlib.Path) -> None:
        """In switch mode, when policy triggers switch, perform switch and notify."""
        accounts = _setup_accounts(ctx)
        now = ctx.clock.now()
        cache = UsageCache(ctx.store.root / "usage.json")
        cache.put_snapshot(accounts[0].fp, _make_snapshot(0.05, now), now)
        cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

        cfg_file = ctx.store.root / "settings.toml"
        cfg_file.write_text('[autopilot]\nhook_action = "switch"\nthreshold = 90\n')

        args = argparse.Namespace(from_hook=True)
        ret = run_auto(ctx, args)
        assert ret == 0

        out = ctx.out.getvalue()
        assert "mswap: switched to account 2." in out

        # Live credential WAS switched
        live = ctx.vault.read(live_target())
        assert live == make_blob(2)

    def test_from_hook_hold_prints_nothing(self, ctx: AppContext, tmp_path: pathlib.Path) -> None:
        """When policy returns hold, from-hook prints nothing."""
        accounts = _setup_accounts(ctx)
        now = ctx.clock.now()
        cache = UsageCache(ctx.store.root / "usage.json")
        cache.put_snapshot(accounts[0].fp, _make_snapshot(0.85, now), now)
        cache.put_snapshot(accounts[1].fp, _make_snapshot(0.90, now), now)

        args = argparse.Namespace(from_hook=True)
        ret = run_auto(ctx, args)
        assert ret == 0
        assert ctx.out.getvalue() == ""

    def test_from_hook_never_raises_on_exceptions(self, ctx: AppContext) -> None:
        """Exceptions are silently caught and from-hook always exits 0."""
        args = argparse.Namespace(from_hook=True)
        with patch("mswap.cli.commands.auto.load_settings", side_effect=RuntimeError("disk error")):
            ret = run_auto(ctx, args)
            assert ret == 0

    def test_from_hook_empty_accounts_returns_zero(self, ctx: AppContext) -> None:
        """When no accounts exist, from-hook exits 0 silently."""
        args = argparse.Namespace(from_hook=True)
        ret = run_auto(ctx, args)
        assert ret == 0
        assert ctx.out.getvalue() == ""

    def test_from_hook_budget_cache_only(self, ctx: AppContext) -> None:
        """When network fetch is needed and remaining budget < 5s, cache_only is True."""
        accounts = _setup_accounts(ctx)
        now = ctx.clock.now()
        cache = UsageCache(ctx.store.root / "usage.json")
        # Put stale snapshot so fetch_needed is True
        stale_time = now - timedelta(seconds=500)
        cache.put_snapshot(accounts[0].fp, _make_snapshot(0.85, stale_time), stale_time)

        args = argparse.Namespace(from_hook=True)
        # timer returns 4.0s elapsed (remaining 4.0s < 5.0s)
        from mswap.cli.commands.auto import _run_from_hook

        with patch("mswap.cli.commands.auto.tick") as mock_tick:
            mock_tick.return_value = QuotaSnapshot(fetched_at=now, pools=())
            # Return a hold decision
            from mswap.core.policy import Decision

            mock_tick.return_value = Decision(
                kind="hold",
                target_slot=None,
                reason="test",
                focus=(),
                active_pressure=None,
                target_pressure=None,
            )
            _run_from_hook(ctx, args, timer=lambda: 4.0)
            assert mock_tick.call_count == 1
            _, kwargs = mock_tick.call_args
            assert kwargs.get("cache_only") is True
