"""Stability contract (1.x): golden snapshots of every `--json` output and every command's help.

The goldens under `tests/golden/json/` and `tests/golden/help/` are the frozen public surface.
A test fails when the surface changes, unless the run sets `MSWAP_ALLOW_CONTRACT_CHANGE=1`, which
rewrites the goldens. Rewriting is refused when a JSON field was removed, renamed or changed type
while `jsonout.SCHEMA_VERSION` still matches the old golden: that needs a schema bump.
See docs/stability.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from mswap.agy.api import FETCH_MODELS_URL, LOAD_CODE_ASSIST_URL, QUOTA_SUMMARY_URL
from mswap.agy.paths import agy_exe
from mswap.agy.tokens import fingerprint
from mswap.cli import HELP_TEXT, main
from mswap.cli.context import AppContext
from mswap.cli.parser import build_parser
from mswap.core.models import Account, Bucket, Pool, Quarantine, QuotaSnapshot
from mswap.core.store import live_target, slot_target
from mswap.core.usage_cache import UsageCache
from mswap.ui import jsonout
from mswap.util.http import FakeHttp, json_response
from tests.conftest import make_blob

GOLDEN = Path(__file__).resolve().parent.parent / "golden"
JSON_GOLDEN = GOLDEN / "json"
HELP_GOLDEN = GOLDEN / "help"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "api"
DOCS = Path(__file__).resolve().parent.parent.parent / "docs"
ALLOW_ENV = "MSWAP_ALLOW_CONTRACT_CHANGE"
PASSPHRASE = "correct-horse-battery-staple"

# Commands that accept --json but print plain text only (documented in docs/json-schema.md).
NO_JSON_COMMANDS = {"watch", "completions", "schedule"}


# --- the golden guard --------------------------------------------------------------------------


def _json_values(text: str) -> list[Any]:
    """Parse one or more concatenated JSON documents (a golden may hold an event stream)."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        value, pos = decoder.raw_decode(text, pos)
        values.append(value)
    return values


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _walk(value: Any, path: str, out: dict[str, set[str]]) -> None:
    out.setdefault(path, set()).add(_kind(value))
    if isinstance(value, dict):
        for key, child in value.items():
            _walk(child, f"{path}.{key}", out)
    elif isinstance(value, list):
        for child in value:
            _walk(child, f"{path}[]", out)


def _shape(values: list[Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for value in values:
        _walk(value, "$", out)
    return out


def breaking_changes(old_text: str, new_text: str) -> list[str]:
    """List JSON paths in `old_text` that `new_text` removed or changed the type of.

    Additions are not breaking. A path that is `null` on one side is not counted as a type
    change, because every optional field is documented as nullable.
    """
    old, new = _shape(_json_values(old_text)), _shape(_json_values(new_text))
    problems: list[str] = []
    for path, old_kinds in sorted(old.items()):
        if path not in new:
            problems.append(f"removed {path}")
            continue
        old_real, new_real = old_kinds - {"null"}, new[path] - {"null"}
        if old_real and new_real and not old_real <= new_real:
            problems.append(f"type of {path}: {sorted(old_real)} -> {sorted(new_real)}")
    return problems


def check_golden(path: Path, actual: str, *, is_json: bool) -> None:
    """Compare `actual` to the golden at `path`; rewrite it only when the override is set."""
    allow = os.environ.get(ALLOW_ENV) == "1"
    if not path.exists():
        if not allow:
            pytest.fail(f"Golden {path.name} is missing. Run with {ALLOW_ENV}=1 to create it.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
        return
    expected = path.read_text(encoding="utf-8")
    if expected == actual:
        return
    if not allow:
        pytest.fail(
            f"The 1.x public contract changed ({path.name}). If this is deliberate, rerun with "
            f"{ALLOW_ENV}=1, commit the new golden, and read docs/stability.md: removing or "
            "renaming a JSON field needs jsonout.SCHEMA_VERSION to be bumped."
        )
    if is_json:
        problems = breaking_changes(expected, actual)
        old_schema = next((v.get("schema") for v in _json_values(expected) if "schema" in v), None)
        if problems and old_schema == jsonout.SCHEMA_VERSION:
            pytest.fail(
                f"{path.name} drops or retypes fields but SCHEMA_VERSION is still "
                f"{jsonout.SCHEMA_VERSION}. Bump it first.\n" + "\n".join(problems)
            )
    path.write_text(actual, encoding="utf-8", newline="\n")


# --- the guard itself --------------------------------------------------------------------------

OLD = '{"schema": 1, "ok": true, "data": {"slot": 1, "alias": null, "pools": [{"key": "a"}]}}'


def test_breaking_changes_ignores_additions_and_key_order() -> None:
    new = (
        '{"schema": 1, "ok": true, "data": '
        '{"extra": 2, "pools": [{"key": "a", "n": 1}], "alias": "w", "slot": 1}}'
    )
    assert breaking_changes(OLD, new) == []


def test_breaking_changes_flags_removal_and_rename() -> None:
    new = '{"schema": 1, "ok": true, "data": {"slot_number": 1, "alias": null, "pools": []}}'
    problems = breaking_changes(OLD, new)
    assert "removed $.data.slot" in problems
    assert "removed $.data.pools[].key" in problems


def test_breaking_changes_flags_type_change_but_not_null_to_value() -> None:
    new = '{"schema": 1, "ok": true, "data": {"slot": "1", "alias": "w", "pools": [{"key": "a"}]}}'
    assert breaking_changes(OLD, new) == ["type of $.data.slot: ['number'] -> ['string']"]


def test_breaking_changes_reads_event_streams() -> None:
    old = '{"event": "hold", "reason": "x"}\n{"event": "switch", "to_slot": 2}'
    assert breaking_changes(old, '{"event": "hold"}') == [
        "removed $.reason",
        "removed $.to_slot",
    ]


def test_check_golden_fails_on_change_without_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    golden = tmp_path / "x.json"
    golden.write_text('{"schema": 1}\n', encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="public contract changed"):
        check_golden(golden, '{"schema": 1, "new": 1}\n', is_json=True)
    assert golden.read_text(encoding="utf-8") == '{"schema": 1}\n'


def test_check_golden_missing_fails_without_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    with pytest.raises(pytest.fail.Exception, match="missing"):
        check_golden(tmp_path / "new.json", "{}\n", is_json=True)


def test_check_golden_override_allows_additions_and_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ALLOW_ENV, "1")
    golden = tmp_path / "x.json"
    golden.write_text('{"schema": 1, "a": 1}\n', encoding="utf-8")
    check_golden(golden, '{"schema": 1, "a": 1, "b": 2}\n', is_json=True)
    assert '"b"' in golden.read_text(encoding="utf-8")


def test_check_golden_override_refuses_removal_without_schema_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ALLOW_ENV, "1")
    golden = tmp_path / "x.json"
    old = f'{{"schema": {jsonout.SCHEMA_VERSION}, "a": 1}}\n'
    golden.write_text(old, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="Bump it first"):
        check_golden(golden, f'{{"schema": {jsonout.SCHEMA_VERSION}}}\n', is_json=True)
    assert golden.read_text(encoding="utf-8") == old


def test_check_golden_override_allows_removal_after_schema_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ALLOW_ENV, "1")
    golden = tmp_path / "x.json"
    golden.write_text(f'{{"schema": {jsonout.SCHEMA_VERSION - 1}, "a": 1}}\n', encoding="utf-8")
    check_golden(golden, f'{{"schema": {jsonout.SCHEMA_VERSION}}}\n', is_json=True)
    assert golden.read_text(encoding="utf-8") == f'{{"schema": {jsonout.SCHEMA_VERSION}}}\n'


# --- JSON snapshots ----------------------------------------------------------------------------


def _snapshot(ctx: AppContext) -> QuotaSnapshot:
    now = ctx.clock.now()
    return QuotaSnapshot(
        fetched_at=now,
        pools=(
            Pool(
                key="gemini",
                name="Gemini",
                buckets=(
                    Bucket(window="5h", remaining=0.99, reset_at=now + timedelta(hours=4)),
                    Bucket(window="weekly", remaining=0.99, reset_at=now + timedelta(days=5)),
                ),
            ),
            Pool(
                key="3p",
                name="Claude & GPT",
                buckets=(
                    Bucket(window="5h", remaining=1.0, reset_at=None),
                    Bucket(window="weekly", remaining=1.0, reset_at=None),
                ),
            ),
        ),
    )


def _seed(
    ctx: AppContext, *, count: int = 2, alias: bool = False, quarantine: bool = False
) -> list[Account]:
    """Save `count` fake accounts, make slot 1 the live login and cache fresh usage for all."""
    now = ctx.clock.now()
    expiry = (now + timedelta(hours=2)).isoformat()
    accounts: list[Account] = []
    cache = UsageCache(ctx.store.root / "usage.json")
    for slot in range(1, count + 1):
        blob = make_blob(slot, expiry=expiry)
        ctx.vault.write(slot_target(slot), blob, f"user{slot}@example.com")
        extra: dict[str, Any] = {}
        if alias and slot == 1:
            extra["alias"] = "work"
        if quarantine and slot == 2:
            extra["quarantined"] = Quarantine(reason="invalid_grant", at=now)
        acc = Account(
            slot=slot,
            email=f"user{slot}@example.com",
            fp=fingerprint(blob),
            added_at=now,
            updated_at=now,
            plan="g1-pro-tier" if slot == 1 else None,
            **extra,
        )
        accounts.append(acc)
        cache.put_snapshot(acc.fp, _snapshot(ctx), now)
    ctx.store.save(accounts)
    ctx.vault.write(live_target(), make_blob(1, expiry=expiry), "antigravity")
    return accounts


def _seed_client(ctx: AppContext) -> None:
    exe = agy_exe()
    ctx.store.root.mkdir(parents=True, exist_ok=True)
    (ctx.store.root / "client.json").write_text(
        json.dumps(
            {
                "exe_sig": f"{exe.stat().st_size}:{int(exe.stat().st_mtime)}"
                if exe.exists()
                else "dummy:1",
                "client_id": "1071006060591-testclient.apps.googleusercontent.com",
                "client_secret": "GOCSPX-FAKEFAKEFAKEFAKEFAKEFAKE0",
                "agy_version": "1.2.12",
            }
        ),
        encoding="utf-8",
    )


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _http(ctx: AppContext) -> FakeHttp:
    assert isinstance(ctx.http, FakeHttp)
    return ctx.http


def _setup_none(ctx: AppContext) -> None:
    del ctx


def _setup_two(ctx: AppContext) -> None:
    _seed(ctx)


def _setup_two_alias(ctx: AppContext) -> None:
    _seed(ctx, alias=True, quarantine=True)


def _setup_add(ctx: AppContext) -> None:
    _seed_client(ctx)
    ctx.vault.write(live_target(), make_blob(1), "antigravity")
    _http(ctx).add(
        "POST", "https://oauth2.googleapis.com/token", json_response(_fixture("token_refresh.json"))
    )
    _http(ctx).add(
        "GET",
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json_response(_fixture("userinfo.json")),
    )


def _setup_switched_once(ctx: AppContext) -> None:
    _seed(ctx)
    assert main(["switch", "2", "--force"], ctx=ctx) == 0
    ctx.out.seek(0)  # type: ignore[attr-defined]  # StringIO in tests
    ctx.out.truncate()  # type: ignore[attr-defined]


def _setup_auto(ctx: AppContext) -> None:
    accounts = _seed(ctx)
    now = ctx.clock.now()
    cache = UsageCache(ctx.store.root / "usage.json")
    low = QuotaSnapshot(
        fetched_at=now,
        pools=(
            Pool(
                key="gemini",
                name="Gemini",
                buckets=(Bucket(window="5h", remaining=0.05, reset_at=now + timedelta(hours=3)),),
            ),
        ),
    )
    cache.put_snapshot(accounts[0].fp, low, now)


def _setup_config_set(ctx: AppContext) -> None:
    del ctx


def _setup_export(ctx: AppContext) -> None:
    _seed(ctx)
    ctx.env["MSWAP_EXPORT_PASSPHRASE"] = PASSPHRASE  # type: ignore[index]  # plain dict in tests


def _setup_import(ctx: AppContext) -> None:
    _seed(ctx)
    ctx.env["MSWAP_EXPORT_PASSPHRASE"] = PASSPHRASE  # type: ignore[index]
    bundle = ctx.store.root.parent / "bundle.mswap"
    assert main(["export", str(bundle)], ctx=ctx) == 0
    ctx.out.seek(0)  # type: ignore[attr-defined]
    ctx.out.truncate()  # type: ignore[attr-defined]
    ctx.store.save([])


def _setup_hook(ctx: AppContext) -> None:
    del ctx


def _setup_hook_installed(ctx: AppContext) -> None:
    assert main(["hook", "install"], ctx=ctx) == 0
    ctx.out.seek(0)  # type: ignore[attr-defined]
    ctx.out.truncate()  # type: ignore[attr-defined]


def _setup_doctor(ctx: AppContext) -> None:
    exe = Path(os.environ["MSWAP_AGY_EXE"])
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"dummy-agy-binary-bytes")
    os.environ["PATH"] = str(exe.parent)
    os.environ["MSWAP_TOOLS_BIN"] = str(exe.parent.parent / "tools_bin")
    _seed_client(ctx)
    (ctx.store.root / "config.json").write_text(
        (ctx.store.root / "client.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _seed(ctx)


def _setup_doctor_repair(ctx: AppContext) -> None:
    _setup_doctor(ctx)
    ctx.vault.write(
        f"{os.environ['MSWAP_VAULT_PREFIX']}slot99", make_blob(99), "orphan@example.com"
    )


def _setup_debug(ctx: AppContext) -> None:
    _seed_client(ctx)
    expiry = (ctx.clock.now() + timedelta(hours=2)).isoformat()
    ctx.vault.write(live_target(), make_blob(1, expiry=expiry), "antigravity")
    for url, name in (
        (QUOTA_SUMMARY_URL, "quota_summary.json"),
        (LOAD_CODE_ASSIST_URL, "load_code_assist.json"),
        (FETCH_MODELS_URL, "fetch_available_models.json"),
    ):
        _http(ctx).add("POST", url, json_response(_fixture(name)))


def _setup_shim(ctx: AppContext) -> None:
    del ctx


# (golden name, setup, argv template with {tmp} for a scratch dir, expected exit code)
SCENARIOS: list[tuple[str, Callable[[AppContext], None], list[str], int]] = [
    ("add", _setup_add, ["add", "--alias", "personal", "--json"], 0),
    ("list", _setup_two_alias, ["list", "--json"], 0),
    ("list_empty", _setup_none, ["list", "--json"], 0),
    ("switch", _setup_two, ["switch", "2", "--force", "--json"], 0),
    ("switch_already_active", _setup_two, ["switch", "1", "--json"], 0),
    ("remove", _setup_two, ["remove", "2", "--yes", "--json"], 0),
    ("alias", _setup_two, ["alias", "1", "work", "--json"], 0),
    ("alias_clear", _setup_two_alias, ["alias", "1", "--clear", "--json"], 0),
    ("disable", _setup_two, ["disable", "2", "--json"], 0),
    ("enable", _setup_two_alias, ["enable", "2", "--json"], 0),
    ("current", _setup_two, ["current", "--json"], 0),
    ("status", _setup_two, ["status", "--json"], 0),
    ("log", _setup_switched_once, ["log", "--json"], 0),
    ("auto", _setup_auto, ["auto", "--once", "--dry-run", "--json"], 0),
    ("config_get", _setup_config_set, ["config", "get", "autopilot.threshold", "--json"], 0),
    ("config_set", _setup_config_set, ["config", "set", "autopilot.threshold", "85", "--json"], 0),
    ("config_unset", _setup_config_set, ["config", "unset", "autopilot.threshold", "--json"], 0),
    ("config_path", _setup_config_set, ["config", "path", "--json"], 0),
    ("config_list", _setup_config_set, ["config", "list", "--json"], 0),
    ("export", _setup_export, ["export", "{tmp}/accounts.mswap", "--json"], 0),
    ("import", _setup_import, ["import", "{tmp}/bundle.mswap", "--json"], 0),
    ("hook_install", _setup_hook, ["hook", "install", "--json"], 0),
    ("hook_status", _setup_hook_installed, ["hook", "status", "--json"], 0),
    ("hook_remove", _setup_hook_installed, ["hook", "remove", "--json"], 0),
    ("shim_install", _setup_shim, ["shim", "install", "--dir", "{tmp}/shimbin", "--json"], 0),
    ("doctor", _setup_doctor, ["doctor", "--json"], 0),
    ("doctor_repair", _setup_doctor_repair, ["doctor", "--repair", "--json"], 0),
    ("debug_record", _setup_debug, ["debug", "record", "--out", "{tmp}/rec", "--json"], 0),
    ("error_unknown_account", _setup_two, ["switch", "7", "--json"], 64),
    ("error_not_signed_in", _setup_none, ["current", "--json"], 4),
]


def _normalise(value: Any, scrubs: list[str]) -> Any:
    """Replace the per-run scratch directory in strings so goldens hold no machine paths."""
    if isinstance(value, dict):
        return {k: _normalise(v, scrubs) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v, scrubs) for v in value]
    if isinstance(value, str):
        text = value.replace(sys.executable, "<PYTHON>")
        for scrub in scrubs:
            text = text.replace(scrub, "<TMP>")
        if "<TMP>" in text:
            text = text.replace("\\\\", "/").replace("\\", "/")
        return text.replace("agy.exe", "agy")  # the fake agy binary is named per platform
    return value


def _render_json_golden(stdout: str, tmp_path: Path) -> str:
    scrubs = sorted(
        {
            str(tmp_path),
            str(tmp_path.resolve()),
            json.dumps(str(tmp_path))[1:-1],
            tmp_path.as_posix(),
            tmp_path.resolve().as_posix(),
        },
        key=len,
        reverse=True,
    )
    values = [_normalise(v, scrubs) for v in _json_values(stdout)]
    for value in values:
        for event in value.get("data", {}).get("events", []):
            event["at"] = "<TIMESTAMP>"  # the audit log stamps real wall-clock time
    return "".join(json.dumps(v, indent=2, ensure_ascii=False) + "\n" for v in values)


@pytest.mark.parametrize(
    ("name", "setup", "argv", "expected_rc"), SCENARIOS, ids=[s[0] for s in SCENARIOS]
)
def test_json_output_matches_golden(
    name: str,
    setup: Callable[[AppContext], None],
    argv: list[str],
    expected_rc: int,
    ctx: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    orig_env = dict(os.environ)
    try:
        monkeypatch.setenv("MSWAP_AGY_STATE", str(tmp_path / "agy-state"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
        monkeypatch.setenv("HOME", str(tmp_path / "profile"))
        monkeypatch.setenv("MSWAP_EXPORT_PASSPHRASE", PASSPHRASE)
        setup(ctx)
        real_argv = [a.replace("{tmp}", str(tmp_path)) for a in argv]

        rc = main(real_argv, ctx=ctx)

        assert rc == expected_rc
        # Results go to ctx.out; top-level error JSON is printed to the real stdout.
        stdout = str(ctx.out.getvalue()) + capsys.readouterr().out  # type: ignore[attr-defined]  # StringIO in tests
        assert stdout.strip(), "a --json run must print its result on stdout"
        check_golden(
            JSON_GOLDEN / f"{name}.json", _render_json_golden(stdout, tmp_path), is_json=True
        )
    finally:
        os.environ.clear()
        os.environ.update(orig_env)


def test_every_json_golden_is_written_by_a_scenario() -> None:
    names = {f"{s[0]}.json" for s in SCENARIOS}
    on_disk = {p.name for p in JSON_GOLDEN.glob("*.json")}
    assert on_disk == names, f"stale or missing goldens: {sorted(on_disk ^ names)}"


def _top_level_commands() -> list[str]:
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return [name for name in sub.choices if name != "ls"]


def test_every_command_with_json_output_has_a_golden() -> None:
    covered = {s[2][0] for s in SCENARIOS}
    expected = set(_top_level_commands()) - NO_JSON_COMMANDS
    assert covered == expected, f"commands without a JSON golden: {sorted(expected ^ covered)}"


def test_json_goldens_use_the_current_schema_version() -> None:
    for path in JSON_GOLDEN.glob("*.json"):
        for value in _json_values(path.read_text(encoding="utf-8")):
            assert value["schema"] == jsonout.SCHEMA_VERSION, path.name


def test_json_goldens_hold_no_secrets_or_machine_paths() -> None:
    forbidden = re.compile(r"ya29\.|1//|GOCSPX-|[A-Za-z]:[\\/]|/Users/|/home/|/tmp/|/var/")
    for path in JSON_GOLDEN.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"<TMP>[^\"]*", "", text)
        assert not forbidden.search(text), path.name


def test_docs_json_schema_covers_every_golden_command_and_field() -> None:
    """docs/json-schema.md must name every command and every top-level `data` field we freeze."""
    doc = (DOCS / "json-schema.md").read_text(encoding="utf-8")
    missing: list[str] = []
    for path in sorted(JSON_GOLDEN.glob("*.json")):
        for value in _json_values(path.read_text(encoding="utf-8")):
            command = value.get("command")
            if command and f"`{command}" not in doc:
                missing.append(f"{path.name}: command {command}")
            data = value.get("data")
            if isinstance(data, dict):
                for key in data:
                    if f'"{key}"' not in doc and f"`{key}`" not in doc:
                        missing.append(f"{path.name}: field {key}")
            if (
                value.get("event")
                and f'"{value["event"]}"' not in doc
                and value["event"] not in doc
            ):
                missing.append(f"{path.name}: event {value['event']}")
    assert not missing, "docs/json-schema.md is out of step with the goldens:\n" + "\n".join(
        missing
    )


def test_docs_stability_covers_contract() -> None:
    """docs/stability.md must define stable surfaces, unstable surfaces, and deprecation policy."""
    doc = (DOCS / "stability.md").read_text(encoding="utf-8")
    assert "CLI Grammar" in doc or "CLI grammar" in doc
    assert "Exit Codes" in doc or "exit codes" in doc
    assert "JSON Output Contract" in doc or "JSON schema" in doc
    assert "Data File Locations" in doc or "data file locations" in doc
    assert "Vault Target Names" in doc or "vault target" in doc
    assert "Human" in doc
    assert "Internal Python Modules" in doc or "internal modules" in doc
    assert "Deprecation Policy" in doc or "deprecation policy" in doc


# --- help snapshots ----------------------------------------------------------------------------
#
# argparse's own `--help` layout differs between Python 3.11 and 3.14 (metavar placement, usage
# wrapping), and CI runs all of them. So the freeze is taken from the parser definition instead:
# every flag, positional, choice, default and help string, in a layout this file controls.


def _describe_action(action: argparse.Action) -> str:
    if action.option_strings:
        names = ", ".join(action.option_strings)
        kind = "option"
    else:
        names = action.metavar or action.dest
        kind = "argument"
    parts = [f"  {kind} {names}"]
    parts.append(f"    action: {type(action).__name__}")
    if action.nargs is not None:
        parts.append(f"    nargs: {action.nargs}")
    if action.metavar and action.option_strings:
        parts.append(f"    metavar: {action.metavar}")
    if action.type is not None:
        parts.append(f"    type: {getattr(action.type, '__name__', action.type)}")
    if action.choices is not None:
        parts.append(f"    choices: {', '.join(str(c) for c in action.choices)}")
    if action.default is not argparse.SUPPRESS:
        parts.append(f"    default: {action.default!r}")
    parts.append(f"    help: {action.help}")
    return "\n".join(parts)


def _describe_parser(title: str, parser: argparse.ArgumentParser, summary: str | None) -> str:
    lines = [title]
    if summary:
        lines.append(f"summary: {summary}")
    leaves = [a for a in parser._actions if not isinstance(a, argparse._HelpAction)]
    leaves = [a for a in leaves if not isinstance(a, argparse._SubParsersAction)]
    positional = [a for a in leaves if not a.option_strings]
    options = sorted((a for a in leaves if a.option_strings), key=lambda a: a.option_strings[-1])
    lines.extend(_describe_action(a) for a in positional + options)
    return "\n".join(lines) + "\n"


def _iter_parsers(
    parser: argparse.ArgumentParser, prefix: str
) -> list[tuple[str, argparse.ArgumentParser, str | None, list[str]]]:
    """Return (golden name, parser, summary, aliases) for every sub-command, depth first."""
    found: list[tuple[str, argparse.ArgumentParser, str | None, list[str]]] = []
    sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if sub is None:
        return found
    helps = {a.dest: a.help for a in sub._choices_actions}
    seen: dict[int, str] = {}
    for name, child in sub.choices.items():
        if id(child) in seen:
            continue
        seen[id(child)] = name
        aliases = [n for n, c in sub.choices.items() if c is child and n != name]
        golden = f"{prefix}{name}"
        found.append((golden, child, helps.get(name), aliases))
        found.extend(_iter_parsers(child, f"{golden}_"))
    return found


def _help_goldens() -> dict[str, str]:
    parser = build_parser()
    goldens: dict[str, str] = {}
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    goldens["mswap"] = ansi.sub("", HELP_TEXT)
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    root = _describe_parser("mswap (global options)", parser, None)
    goldens["mswap_global"] = root + f"commands: {', '.join(sub.choices)}\n"
    for golden, child, summary, aliases in _iter_parsers(parser, ""):
        title = "mswap " + golden.replace("_", " ")
        text = _describe_parser(title, child, summary)
        if aliases:
            text = text.replace("\n", f"\naliases: {', '.join(aliases)}\n", 1)
        goldens[golden] = text
    return goldens


HELP_GOLDENS = _help_goldens()


@pytest.mark.parametrize("name", sorted(HELP_GOLDENS))
def test_help_matches_golden(name: str) -> None:
    check_golden(HELP_GOLDEN / f"{name}.txt", HELP_GOLDENS[name], is_json=False)


def test_every_help_golden_is_still_a_command() -> None:
    on_disk = {p.stem for p in HELP_GOLDEN.glob("*.txt")}
    assert on_disk == set(HELP_GOLDENS), f"stale or missing: {sorted(on_disk ^ set(HELP_GOLDENS))}"


def test_help_text_lists_every_top_level_command() -> None:
    for command in _top_level_commands():
        assert re.search(rf"mswap (\w+\|)?{command}\b", HELP_TEXT), command
