"""Unit tests for the `mswap export` and `mswap import` commands."""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import sys
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mswap.agy.tokens import fingerprint
from mswap.cli.commands.export_ import run as run_export
from mswap.cli.commands.import_ import run as run_import
from mswap.cli.context import AppContext
from mswap.cli.parser import build_parser
from mswap.core.bundle import decrypt_bundle, encrypt_bundle
from mswap.core.errors import CorruptState, UsageError
from mswap.core.models import Account, Quarantine, account_to_json
from mswap.core.store import live_target, slot_target
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob

PASSPHRASE = "correct-horse-battery-staple"


def _account(ctx: AppContext, slot: int, **kw: Any) -> Account:
    blob = make_blob(slot)
    now = ctx.clock.now()
    acc = Account(
        slot=slot,
        email=f"user{slot}@example.com",
        fp=fingerprint(blob),
        added_at=now,
        updated_at=now,
        **kw,
    )
    ctx.vault.write(slot_target(slot), blob, acc.email)
    return acc


def _seed(ctx: AppContext, *accounts: Account) -> None:
    ctx.store.save(list(accounts))


def _args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


def _export(ctx: AppContext, path: Path, *extra: str) -> int:
    ctx.env["MSWAP_EXPORT_PASSPHRASE"] = PASSPHRASE
    return run_export(ctx, _args("export", str(path), *extra))


def _import(ctx: AppContext, path: Path, *extra: str) -> int:
    ctx.env["MSWAP_EXPORT_PASSPHRASE"] = PASSPHRASE
    return run_import(ctx, _args("import", str(path), *extra))


def _out(ctx: AppContext) -> str:
    return str(ctx.out.getvalue())  # type: ignore[attr-defined]  # tests use StringIO


def _writes(vault: MemoryVault) -> int:
    return len([c for c in vault.calls if c[0] == "write"])


def _account_json(ctx: AppContext, slot: int) -> dict[str, Any]:
    now = ctx.clock.now()
    acc = Account(
        slot=slot,
        email=f"user{slot}@example.com",
        fp=fingerprint(make_blob(slot)),
        added_at=now,
        updated_at=now,
    )
    return dict(account_to_json(acc))


def test_parser_export_import_arguments() -> None:
    exp = build_parser().parse_args(["export", "f.json", "--accounts", "1,b@example.com"])
    assert (exp.command, exp.file, exp.accounts) == ("export", "f.json", "1,b@example.com")
    imp = build_parser().parse_args(["import", "f.json", "--force"])
    assert (imp.command, imp.file, imp.force) == ("import", "f.json", True)


def test_passphrase_is_never_an_argv_option() -> None:
    parser = build_parser()
    with pytest.raises(UsageError, match="unrecognized arguments"):
        parser.parse_args(["export", "f.json", "--passphrase", "hunter2hunter2"])
    assert "passphrase" not in vars(parser.parse_args(["export", "f.json"]))


def test_export_import_round_trip(ctx: AppContext, vault: MemoryVault, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1, alias="work"), _account(ctx, 2))
    path = tmp_path / "out" / "bundle.json"

    assert _export(ctx, path) == 0
    assert "Exported 2 account(s)" in _out(ctx)
    assert "Anyone with this file AND the passphrase can use these accounts." in _out(ctx)

    # Wipe the local state, then import into the empty store.
    for slot in (1, 2):
        vault.delete(slot_target(slot))
    ctx.store.save([])
    assert _import(ctx, path) == 0
    assert "Imported 2, updated 0, skipped 0." in _out(ctx)

    restored = ctx.store.load()
    assert [(a.slot, a.email, a.alias) for a in restored] == [
        (1, "user1@example.com", "work"),
        (2, "user2@example.com", None),
    ]
    assert vault.read(slot_target(1)) == make_blob(1)
    assert vault.read(slot_target(2)) == make_blob(2)
    assert restored[0].fp == fingerprint(make_blob(1))


def test_export_file_has_no_plaintext_secrets(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    text = path.read_text(encoding="utf-8")
    assert "FAKE-refresh" not in text
    assert "user1@example.com" not in text
    assert json.loads(text)["format"] == "mswap-export"


def test_export_selected_accounts_only(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1), _account(ctx, 2), _account(ctx, 3))
    path = tmp_path / "bundle.json"
    assert _export(ctx, path, "--accounts", "user2@example.com,3,2") == 0
    plain = decrypt_bundle(json.loads(path.read_text(encoding="utf-8")), PASSPHRASE)
    assert [a["slot"] for a in plain["accounts"]] == [2, 3]
    assert "Exported 2 account(s)" in _out(ctx)


def test_export_excludes_client_json(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    (ctx.store.root / "client.json").write_text('{"client_secret": "GOCSPX-FAKE"}', "utf-8")
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    plain = decrypt_bundle(json.loads(path.read_text(encoding="utf-8")), PASSPHRASE)
    assert "GOCSPX-FAKE" not in json.dumps(plain)


def test_export_json_output(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    ctx_json = dataclasses.replace(ctx, json=True)
    assert _export(ctx_json, tmp_path / "b.json") == 0
    payload = json.loads(_out(ctx_json))
    assert payload["ok"] is True
    assert payload["data"]["count"] == 1


def test_export_no_accounts(ctx: AppContext, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="No accounts saved yet"):
        _export(ctx, tmp_path / "b.json")


def test_export_unknown_selector(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    with pytest.raises(UsageError):
        _export(ctx, tmp_path / "b.json", "--accounts", "nobody@example.com")
    assert not (tmp_path / "b.json").exists()


def test_export_blank_selector_list(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    with pytest.raises(UsageError, match="No valid account selectors"):
        _export(ctx, tmp_path / "b.json", "--accounts", " , ")


def test_export_missing_slot_blob(ctx: AppContext, vault: MemoryVault, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    vault.delete(slot_target(1))
    with pytest.raises(CorruptState, match="missing"):
        _export(ctx, tmp_path / "b.json")


def test_export_short_passphrase_rejected(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    ctx.env["MSWAP_EXPORT_PASSPHRASE"] = "short"
    with pytest.raises(UsageError, match="at least 12"):
        run_export(ctx, _args("export", str(tmp_path / "b.json")))
    assert not (tmp_path / "b.json").exists()


def test_export_import_never_touch_live_target(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, _account(ctx, 1), _account(ctx, 2))
    vault.write(live_target(), make_blob(1), "antigravity")
    before = len(vault.calls)
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    ctx.store.save([_account(ctx, 1)])
    _import(ctx, path, "--force")
    assert [c for c in vault.calls[before:] if c[1] == live_target()] == []
    assert vault.read(live_target()) == make_blob(1)


def test_import_skips_existing_email(ctx: AppContext, vault: MemoryVault, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    # Local copy changes after export; a plain import must leave it alone.
    vault.write(slot_target(1), make_blob(9), "user1@example.com")
    assert _import(ctx, path) == 0
    assert "Imported 0, updated 0, skipped 1." in _out(ctx)
    assert vault.read(slot_target(1)) == make_blob(9)


def test_import_force_overwrites_existing(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    vault.write(slot_target(1), make_blob(9), "user1@example.com")
    assert _import(ctx, path, "--force") == 0
    assert "Imported 0, updated 1, skipped 0." in _out(ctx)
    assert vault.read(slot_target(1)) == make_blob(1)
    assert ctx.store.load()[0].fp == fingerprint(make_blob(1))


def test_import_replaces_quarantined_without_force(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    good = _account(ctx, 1)
    _seed(ctx, good)
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    dead = dataclasses.replace(
        good, quarantined=Quarantine(reason="invalid_grant", at=ctx.clock.now())
    )
    ctx.store.save([dead])
    vault.write(slot_target(1), make_blob(9), "user1@example.com")

    assert _import(ctx, path) == 0
    assert "Imported 0, updated 1, skipped 0." in _out(ctx)
    assert ctx.store.load()[0].quarantined is None
    assert vault.read(slot_target(1)) == make_blob(1)


def test_import_quarantined_bundle_copy_does_not_replace_quarantined(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    dead = _account(ctx, 1, quarantined=Quarantine(reason="revoked", at=ctx.clock.now()))
    _seed(ctx, dead)
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    vault.write(slot_target(1), make_blob(9), "user1@example.com")
    _import(ctx, path)
    assert "skipped 1" in _out(ctx)
    assert vault.read(slot_target(1)) == make_blob(9)


def test_import_new_account_takes_next_free_slot(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, _account(ctx, 3, alias="work"))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    # Local store now holds different accounts in slots 1 and 2.
    other = dataclasses.replace(_account(ctx, 1), email="other@example.com")
    ctx.store.save([other, _account(ctx, 2)])

    assert _import(ctx, path) == 0
    assert "Imported 1, updated 0, skipped 0." in _out(ctx)
    by_email = {a.email: a for a in ctx.store.load()}
    assert by_email["user3@example.com"].slot == 3
    assert vault.read(slot_target(3)) == make_blob(3)
    assert by_email["user3@example.com"].alias == "work"


def test_import_fills_lowest_free_slot(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 5))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    ctx.store.save([_account(ctx, 1), _account(ctx, 3)])
    _import(ctx, path)
    assert {a.email: a.slot for a in ctx.store.load()}["user5@example.com"] == 2


def test_import_alias_conflict_drops_alias(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1, alias="work"))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    mine = dataclasses.replace(_account(ctx, 2), alias="work")
    ctx.store.save([mine])
    _import(ctx, path)
    imported = next(a for a in ctx.store.load() if a.email == "user1@example.com")
    assert imported.alias is None


def test_import_mixed_summary(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1), _account(ctx, 2), _account(ctx, 3))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    q = Quarantine(reason="invalid_grant", at=ctx.clock.now())
    ctx.store.save(
        [
            dataclasses.replace(_account(ctx, 1), quarantined=q),  # replaced
            _account(ctx, 2),  # skipped
        ]  # user3 is new
    )
    assert _import(ctx, path) == 0
    assert "Imported 1, updated 1, skipped 1." in _out(ctx)


def test_import_json_output(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    ctx.store.save([])
    ctx_json = dataclasses.replace(ctx, json=True, out=StringIO())
    assert _import(ctx_json, path) == 0
    assert json.loads(_out(ctx_json))["data"] == {"imported": 1, "updated": 0, "skipped": 0}


def test_import_wrong_passphrase_changes_nothing(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    ctx.store.save([])
    ctx.env["MSWAP_EXPORT_PASSPHRASE"] = "not-the-right-passphrase"
    writes = _writes(vault)
    with pytest.raises(UsageError, match=r"Wrong passphrase or damaged file."):
        run_import(ctx, _args("import", str(path)))
    assert _writes(vault) == writes
    assert ctx.store.load() == []


def test_import_tampered_file_fails(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    bundle = json.loads(path.read_text(encoding="utf-8"))
    raw = bytearray(base64.b64decode(bundle["ciphertext"]))
    raw[5] ^= 0x01
    bundle["ciphertext"] = base64.b64encode(raw).decode("ascii")
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(UsageError, match=r"Wrong passphrase or damaged file."):
        _import(ctx, path)


def test_import_missing_file(ctx: AppContext, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="Export file not found"):
        _import(ctx, tmp_path / "nope.json")


def test_import_not_json(ctx: AppContext, tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(UsageError, match=r"Wrong passphrase or damaged file."):
        _import(ctx, path)


@pytest.mark.parametrize(
    "bad_entry",
    [
        "a string",
        {"email": "x@example.com"},
        {"email": "x@example.com", "blob": "!!!not-base64!!!"},
        {"email": "x@example.com", "blob": base64.b64encode(b"{}").decode("ascii")},
        # valid blob but no added_at: account metadata cannot be parsed
        {"email": "x@example.com", "blob": base64.b64encode(make_blob(1)).decode("ascii")},
    ],
)
def test_import_malformed_entry_is_all_or_nothing(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path, bad_entry: object
) -> None:
    good = {**_account_json(ctx, 1), "blob": base64.b64encode(make_blob(1)).decode("ascii")}
    path = tmp_path / "bundle.json"
    bundle = encrypt_bundle({"exported_at": "x", "accounts": [good, bad_entry]}, PASSPHRASE)
    path.write_text(json.dumps(bundle), encoding="utf-8")
    writes = _writes(vault)
    with pytest.raises(UsageError, match=r"Wrong passphrase or damaged file."):
        _import(ctx, path)
    assert _writes(vault) == writes
    assert ctx.store.load() == []


def test_import_slot_limit(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    full = [
        dataclasses.replace(_account(ctx, s), email=f"full{s}@example.com") for s in range(1, 100)
    ]
    ctx.store.save(full)
    with pytest.raises(UsageError, match="Account limit reached"):
        _import(ctx, path)


def test_import_does_not_store_blob_in_accounts_json(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    ctx.store.save([])
    _import(ctx, path)
    text = (ctx.store.root / "accounts.json").read_text(encoding="utf-8")
    assert "blob" not in text
    assert "FAKE-refresh" not in text


def test_missing_crypto_extra_export_and_import(ctx: AppContext, tmp_path: Path) -> None:
    _seed(ctx, _account(ctx, 1))
    with patch.dict(sys.modules, {"cryptography.hazmat.primitives.ciphers.aead": None}):
        with pytest.raises(UsageError) as exc_export:
            _export(ctx, tmp_path / "b.json")
        with pytest.raises(UsageError) as exc_import:
            _import(ctx, tmp_path / "b.json")
    for exc in (exc_export, exc_import):
        assert exc.value.message == "Export needs the optional crypto package."
        assert exc.value.hint == 'Install with: uv tool install "mswap[export]"'
    assert not (tmp_path / "b.json").exists()


def test_export_file_is_owner_only_on_posix(ctx: AppContext, tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits only")
    _seed(ctx, _account(ctx, 1))
    path = tmp_path / "bundle.json"
    _export(ctx, path)
    assert path.stat().st_mode & 0o777 == 0o600
