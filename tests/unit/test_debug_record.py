"""Unit and integration tests for `mswap debug record` and the shape-only redaction."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from mswap.agy.api import FETCH_MODELS_URL, LOAD_CODE_ASSIST_URL, QUOTA_SUMMARY_URL
from mswap.agy.paths import agy_exe
from mswap.cli.commands.debug import run as run_debug
from mswap.cli.context import AppContext
from mswap.cli.parser import build_parser
from mswap.core.debug_record import KEEP_KEYS, scan_text, shape
from mswap.core.errors import ApiError, NotSignedIn, UnsafeOperation, UsageError
from mswap.core.store import live_target
from mswap.util.http import json_response
from mswap.vault.memory import MemoryVault
from tests.conftest import make_blob

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "api"
GOLDEN = Path(__file__).resolve().parent.parent / "golden"

# (capture file, URL, fixture file)
ENDPOINT_FILES = [
    ("quota_summary.json", QUOTA_SUMMARY_URL, "quota_summary.json"),
    ("load_code_assist.json", LOAD_CODE_ASSIST_URL, "load_code_assist.json"),
    ("fetch_available_models.json", FETCH_MODELS_URL, "fetch_available_models.json"),
]


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _seed(ctx: AppContext, vault: MemoryVault) -> None:
    """Sign in with a fresh fake login and seed the cached OAuth client."""
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
    expiry = (ctx.clock.now() + timedelta(hours=2)).isoformat()
    vault.write(live_target(), make_blob(1, expiry=expiry), "antigravity")


def _route_all(ctx: AppContext) -> None:
    for _, url, fixture in ENDPOINT_FILES:
        ctx.http.add("POST", url, json_response(_fixture(fixture)))  # type: ignore[attr-defined]  # FakeHttp


def _args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["debug", *argv])


def _out(ctx: AppContext) -> str:
    return str(ctx.out.getvalue())  # type: ignore[attr-defined]  # tests use StringIO


def _golden(request: pytest.FixtureRequest, name: str, actual: str) -> None:
    path = GOLDEN / name
    if request.config.getoption("--update-golden"):
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"Golden file {path} missing. Run with --update-golden."
    assert actual == path.read_text(encoding="utf-8")


# --- shape() ---------------------------------------------------------------------------------


def test_shape_replaces_strings_with_length_placeholders() -> None:
    assert shape({"a": "hello", "b": ["xy", "z"]}) == {"a": "<str:5>", "b": ["<str:2>", "<str:1>"]}


def test_shape_keeps_numbers_bools_and_null() -> None:
    assert shape({"n": 1, "f": 0.25, "t": True, "x": None}) == {
        "n": 1,
        "f": 0.25,
        "t": True,
        "x": None,
    }


@pytest.mark.parametrize("key", sorted(KEEP_KEYS))
def test_shape_keeps_allow_listed_values_verbatim(key: str) -> None:
    assert shape({key: "gemini-5h"}) == {key: "gemini-5h"}


def test_shape_does_not_keep_other_keys_even_if_similar() -> None:
    assert shape({"description": "Weekly", "Id": "x"}) == {
        "description": "<str:6>",
        "Id": "<str:1>",
    }


def test_shape_keeps_strings_in_a_list_under_an_allow_listed_key() -> None:
    assert shape({"status": ["OK", "LOW"], "other": ["OK"]}) == {
        "status": ["OK", "LOW"],
        "other": ["<str:2>"],
    }


def test_shape_nested_allow_list_applies_to_the_inner_key_only() -> None:
    assert shape({"id": {"name": "secretish", "id": "free-tier"}}) == {
        "id": {"name": "<str:9>", "id": "free-tier"}
    }


@pytest.mark.parametrize(
    "value",
    [
        "alice@example.com",
        "ya29.FAKE-access-1",
        "1//FAKE-refresh-1",
        "GOCSPX-FAKEFAKEFAKEFAKEFAKEFAKE0",
        "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl",
    ],
)
def test_shape_never_keeps_a_secret_shaped_value_even_under_an_allow_listed_key(
    value: str,
) -> None:
    result = shape({"id": value, "displayName": f"x {value} y"})
    assert result == {"id": f"<str:{len(value)}>", "displayName": f"<str:{len(value) + 4}>"}


# --- scan_text() -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("ya29.FAKE-access-1", "access_token"),
        ("1//FAKE-refresh-1", "refresh_token"),
        ("GOCSPX-FAKEFAKEFAKEFAKEFAKEFAKE0", "client_secret"),
        ("eyJhbGciOi.eyJzdWIiOi.c2ln", "jwt"),
        ('{"refresh_token": "abc"}', "token_field"),
        ("someone@example.com", "email"),
        ("a.b+c@example.com", "email"),
    ],
)
def test_scan_text_flags_each_pattern(text: str, kind: str) -> None:
    assert kind in scan_text(text)


@pytest.mark.parametrize("text", ["", "<str:17>", "gemini-5h", '{"remainingFraction": 0.5}'])
def test_scan_text_clean(text: str) -> None:
    assert scan_text(text) == []


# --- the command -----------------------------------------------------------------------------


def test_record_writes_golden_shapes_and_env(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    _seed(ctx, vault)
    _route_all(ctx)
    out = tmp_path / "capture"

    assert run_debug(ctx, _args("record", "--out", str(out))) == 0

    assert sorted(p.name for p in out.iterdir()) == [
        "env.json",
        "fetch_available_models.json",
        "load_code_assist.json",
        "quota_summary.json",
    ]
    for name, _, _ in ENDPOINT_FILES:
        _golden(request, f"debug_{name}", (out / name).read_text(encoding="utf-8"))
    assert _out(ctx) == f"Saved to {out}. Safe to attach to a GitHub issue.\n"


def test_record_golden_keeps_the_allow_listed_labels(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, vault)
    _route_all(ctx)
    out = tmp_path / "capture"
    run_debug(ctx, _args("record", "--out", str(out)))

    quota = json.loads((out / "quota_summary.json").read_text(encoding="utf-8"))
    bucket = quota["groups"][0]["buckets"][0]
    assert bucket["bucketId"] == "gemini-weekly"
    assert bucket["window"] == "weekly"
    assert bucket["displayName"] == "Weekly Limit Remaining"
    assert bucket["remainingFraction"] == 0.9910955
    assert bucket["resetTime"].startswith("<str:")
    assert bucket["description"].startswith("<str:")
    plan = json.loads((out / "load_code_assist.json").read_text(encoding="utf-8"))
    assert plan["paidTier"] == {"id": "g1-pro-tier"}
    assert plan["cloudaicompanionProject"].startswith("<str:")


def test_record_env_json_fields(ctx: AppContext, vault: MemoryVault, tmp_path: Path) -> None:
    import platform

    import mswap
    from mswap.agy.install import os_arch

    _seed(ctx, vault)
    _route_all(ctx)
    out = tmp_path / "capture"
    run_debug(ctx, _args("record", "--out", str(out)))

    env = json.loads((out / "env.json").read_text(encoding="utf-8"))
    assert env == {
        "agy_version": "1.2.12",
        "mswap_version": mswap.__version__,
        "os_arch": os_arch(),
        "python_version": platform.python_version(),
        "vault_backend": "MemoryVault",
    }


def test_record_output_holds_no_token_email_or_secret(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, vault)
    _route_all(ctx)
    out = tmp_path / "capture"
    run_debug(ctx, _args("record", "--out", str(out)))

    for path in out.iterdir():
        text = path.read_text(encoding="utf-8")
        assert scan_text(text) == [], path.name
        for needle in ("ya29", "1//", "GOCSPX", "@", "FAKE", "example.com"):
            assert needle not in text, (path.name, needle)


def test_record_sends_the_agy_user_agent_and_never_writes_the_vault(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, vault)
    _route_all(ctx)
    calls_before = len(vault.calls)
    run_debug(ctx, _args("record", "--out", str(tmp_path / "capture")))

    assert all(c[0] == "read" for c in vault.calls[calls_before:])
    requests = ctx.http.requests  # type: ignore[attr-defined]  # FakeHttp
    assert [r["url"] for r in requests] == [u for _, u, _ in ENDPOINT_FILES]
    assert all(r["headers"]["User-Agent"].startswith("antigravity/1.2.12 ") for r in requests)


def test_record_default_out_dir_is_a_timestamped_folder_in_the_data_dir(
    ctx: AppContext, vault: MemoryVault
) -> None:
    _seed(ctx, vault)
    _route_all(ctx)
    run_debug(ctx, _args("record"))

    assert (ctx.store.root / "debug-20261002-120000" / "env.json").exists()


def test_record_json_output(ctx: AppContext, vault: MemoryVault, tmp_path: Path) -> None:
    _seed(ctx, vault)
    _route_all(ctx)
    ctx.json = True
    out = tmp_path / "capture"
    run_debug(ctx, _args("record", "--out", str(out)))

    payload = json.loads(_out(ctx))
    assert payload["schema"] == 1
    assert payload["ok"] is True
    assert payload["command"] == "debug"
    assert payload["data"]["dir"] == str(out)
    assert payload["data"]["files"] == [
        "env.json",
        "fetch_available_models.json",
        "load_code_assist.json",
        "quota_summary.json",
    ]


def test_record_refuses_and_deletes_when_something_secret_shaped_remains(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, vault)
    # Dict KEYS are not shape-reduced, so an email-shaped key must trip the final scan.
    leaky = {"groups": [], "alice@example.com": 1}
    ctx.http.add("POST", QUOTA_SUMMARY_URL, json_response(leaky))  # type: ignore[attr-defined]
    ctx.http.add("POST", LOAD_CODE_ASSIST_URL, json_response(_fixture("load_code_assist.json")))  # type: ignore[attr-defined]
    ctx.http.add("POST", FETCH_MODELS_URL, json_response(_fixture("fetch_available_models.json")))  # type: ignore[attr-defined]
    out = tmp_path / "capture"

    with pytest.raises(UnsafeOperation) as exc:
        run_debug(ctx, _args("record", "--out", str(out)))

    assert "email" in exc.value.message
    assert "alice" not in exc.value.message
    assert not out.exists()
    assert "Saved to" not in _out(ctx)


def test_record_refusal_keeps_a_pre_existing_empty_folder_but_empties_it(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, vault)
    ctx.http.add("POST", QUOTA_SUMMARY_URL, json_response({"ya29.FAKE-key-1": 1}))  # type: ignore[attr-defined]
    ctx.http.add("POST", LOAD_CODE_ASSIST_URL, json_response({}))  # type: ignore[attr-defined]
    ctx.http.add("POST", FETCH_MODELS_URL, json_response({}))  # type: ignore[attr-defined]
    out = tmp_path / "mine"
    out.mkdir()

    with pytest.raises(UnsafeOperation):
        run_debug(ctx, _args("record", "--out", str(out)))

    assert out.is_dir()
    assert list(out.iterdir()) == []


def test_record_refuses_a_non_empty_out_dir_and_leaves_it_alone(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, vault)
    out = tmp_path / "mine"
    out.mkdir()
    (out / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(UsageError) as exc:
        run_debug(ctx, _args("record", "--out", str(out)))

    assert "--out" in (exc.value.hint or "")
    assert (out / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert ctx.http.requests == []  # type: ignore[attr-defined]  # FakeHttp


def test_record_requires_a_signed_in_agy(ctx: AppContext, tmp_path: Path) -> None:
    with pytest.raises(NotSignedIn):
        run_debug(ctx, _args("record", "--out", str(tmp_path / "capture")))
    assert not (tmp_path / "capture").exists()


def test_record_notes_a_single_failed_endpoint_without_the_server_message(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, vault)
    ctx.http.add(
        "POST", QUOTA_SUMMARY_URL, json_response({"error": {"message": "bob@example.com"}}, 404)
    )  # type: ignore[attr-defined]
    ctx.http.add("POST", LOAD_CODE_ASSIST_URL, json_response(_fixture("load_code_assist.json")))  # type: ignore[attr-defined]
    ctx.http.add("POST", FETCH_MODELS_URL, json_response(_fixture("fetch_available_models.json")))  # type: ignore[attr-defined]
    out = tmp_path / "capture"

    assert run_debug(ctx, _args("record", "--out", str(out))) == 0

    quota = json.loads((out / "quota_summary.json").read_text(encoding="utf-8"))
    assert quota == {"error": {"kind": "ApiError", "status": 404}}


def test_record_fails_and_writes_nothing_when_every_endpoint_fails(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path
) -> None:
    _seed(ctx, vault)
    for url in (QUOTA_SUMMARY_URL, LOAD_CODE_ASSIST_URL, FETCH_MODELS_URL):
        ctx.http.add("POST", url, json_response({}, 500))  # type: ignore[attr-defined]
    out = tmp_path / "capture"

    with pytest.raises(ApiError):
        run_debug(ctx, _args("record", "--out", str(out)))

    assert not out.exists()


def test_debug_without_an_action_is_a_usage_error(ctx: AppContext) -> None:
    with pytest.raises(UsageError) as exc:
        run_debug(ctx, _args())
    assert "mswap debug record" in (exc.value.hint or "")


def test_debug_record_is_registered_in_the_parser_and_the_help_text() -> None:
    from mswap.cli import HELP_TEXT

    ns = build_parser().parse_args(["debug", "record", "--out", "somewhere"])
    assert (ns.command, ns.debug_action, ns.out) == ("debug", "record", "somewhere")
    assert "mswap debug record" in HELP_TEXT


def test_record_cleans_up_when_a_write_fails_midway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mswap.cli.commands import debug as debug_cmd

    real_write = Path.write_text
    calls: list[str] = []

    def flaky(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        calls.append(self.name)
        if len(calls) == 2:
            raise OSError("disk full")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky)
    out = tmp_path / "capture"
    out.mkdir()

    with pytest.raises(OSError, match="disk full"):
        debug_cmd._write_and_verify(out, True, {"a.json": "{}\n", "b.json": "{}\n"})

    assert not out.exists()


def test_record_reports_unknown_agy_version_when_agy_is_not_found(
    ctx: AppContext, vault: MemoryVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mswap.cli.commands import debug as debug_cmd
    from mswap.core.errors import AgyNotFound

    _seed(ctx, vault)
    _route_all(ctx)

    def missing() -> Path:
        raise AgyNotFound("agy not found")

    monkeypatch.setattr(debug_cmd, "agy_exe", missing)
    out = tmp_path / "capture"
    run_debug(ctx, _args("record", "--out", str(out)))

    env = json.loads((out / "env.json").read_text(encoding="utf-8"))
    assert env["agy_version"] == "unknown"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2.12", "1.2.12"),
        ("1.2.12-rc1+build5", "1.2.12-rc1+build5"),
        ("agy 1.2.12", "unknown"),
        ("1.2.12 alice@example.com", "unknown"),
        ("0.0.0", "0.0.0"),
        ("", "unknown"),
    ],
)
def test_record_env_only_keeps_a_plain_agy_version(
    ctx: AppContext,
    vault: MemoryVault,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: str,
) -> None:
    from mswap.cli.commands import debug as debug_cmd

    _seed(ctx, vault)
    _route_all(ctx)
    monkeypatch.setattr(debug_cmd, "agy_version", lambda _exe, _cache: raw)
    out = tmp_path / "capture"
    run_debug(ctx, _args("record", "--out", str(out)))

    env = json.loads((out / "env.json").read_text(encoding="utf-8"))
    assert env["agy_version"] == expected


def test_shape_hides_identifier_sized_numbers_but_keeps_quota_figures() -> None:
    # A 12-digit project number and an epoch timestamp are identifiers, not shape.
    assert shape({"project": 1071006060591, "at": 1_790_000_000, "big": 4.5e12}) == {
        "project": "<int:13>",
        "at": "<int:10>",
        "big": "<num>",
    }
    assert shape({"remaining": 0.5, "count": 999_999_999, "neg": -5, "ok": True}) == {
        "remaining": 0.5,
        "count": 999_999_999,
        "neg": -5,
        "ok": True,
    }
