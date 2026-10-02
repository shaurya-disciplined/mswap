"""Integration tests for the update footer shown after human `list` and `doctor`."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest

from mswap import __version__
from mswap.cli import main
from mswap.cli.context import AppContext
from mswap.cli.update_notice import maybe_print_update_notice
from mswap.core.updates import PYPI_URL
from mswap.util.http import FakeHttp, json_response
from mswap.vault.memory import MemoryVault
from tests.integration.test_commands_v02 import (
    _load_fixture,
    _seed_config,
    _seed_two_accounts,
)
from tests.integration.test_doctor import _setup_healthy_environment

NOTICE = "mswap 99.0.0 is available"
UPGRADE = "uv tool upgrade mswap"


@pytest.fixture
def pypi(http: FakeHttp) -> FakeHttp:
    http.add("GET", PYPI_URL, json_response({"info": {"version": "99.0.0"}}))
    return http


@pytest.fixture
def net_on(ctx: AppContext) -> AppContext:
    ctx.env = {k: v for k, v in ctx.env.items() if k != "MSWAP_NO_NETWORK"}
    return ctx


@pytest.fixture
def active_net_on() -> Generator[None, None, None]:
    """Let the update check run inside `main()` (tests default to MSWAP_NO_NETWORK=1)."""
    from mswap.cli import context as context_mod

    active = context_mod._ACTIVE_CONTEXT
    assert active is not None
    active.env = {k: v for k, v in active.env.items() if k != "MSWAP_NO_NETWORK"}
    yield


def _out(ctx: AppContext) -> str:
    return ctx.out.getvalue()  # type: ignore[attr-defined]  # StringIO in tests


def test_notice_printed_when_newer(net_on: AppContext, pypi: FakeHttp) -> None:
    maybe_print_update_notice(net_on)
    assert _out(net_on) == f"  mswap 99.0.0 is available (you have {__version__}): {UPGRADE}\n"
    assert (net_on.store.root / "update.json").exists()


def test_no_notice_when_up_to_date(net_on: AppContext, http: FakeHttp) -> None:
    http.add("GET", PYPI_URL, json_response({"info": {"version": __version__}}))
    maybe_print_update_notice(net_on)
    assert _out(net_on) == ""


def test_notice_uses_cache_for_a_day(net_on: AppContext, pypi: FakeHttp) -> None:
    maybe_print_update_notice(net_on)
    maybe_print_update_notice(net_on)
    assert len(pypi.requests) == 1
    assert _out(net_on).count(NOTICE) == 2


def test_suppressed_by_json(net_on: AppContext, pypi: FakeHttp) -> None:
    net_on.json = True
    maybe_print_update_notice(net_on)
    assert _out(net_on) == ""
    assert pypi.requests == []


def test_suppressed_by_quiet(net_on: AppContext, pypi: FakeHttp) -> None:
    net_on.quiet = True
    maybe_print_update_notice(net_on)
    assert _out(net_on) == ""
    assert pypi.requests == []


def test_suppressed_by_env_opt_out(net_on: AppContext, pypi: FakeHttp) -> None:
    net_on.env = {**net_on.env, "MSWAP_NO_UPDATE_CHECK": "1"}
    maybe_print_update_notice(net_on)
    assert _out(net_on) == ""
    assert pypi.requests == []


def test_suppressed_by_no_network(ctx: AppContext, pypi: FakeHttp) -> None:
    assert ctx.env.get("MSWAP_NO_NETWORK") == "1"
    maybe_print_update_notice(ctx)
    assert _out(ctx) == ""
    assert pypi.requests == []


def test_suppressed_by_setting(net_on: AppContext, pypi: FakeHttp) -> None:
    net_on.store.root.mkdir(parents=True, exist_ok=True)
    (net_on.store.root / "settings.toml").write_text("[updates]\ncheck = false\n", "utf-8")
    maybe_print_update_notice(net_on)
    assert _out(net_on) == ""
    assert pypi.requests == []


def test_broken_settings_file_is_silent(net_on: AppContext, pypi: FakeHttp) -> None:
    net_on.store.root.mkdir(parents=True, exist_ok=True)
    (net_on.store.root / "settings.toml").write_text("this is [not toml", "utf-8")
    maybe_print_update_notice(net_on)
    assert _out(net_on) == ""


def test_network_failure_is_silent(net_on: AppContext) -> None:
    class Offline:
        def request(self, *args: object, **kwargs: object) -> object:
            raise OSError("offline")

    net_on.http = Offline()  # type: ignore[assignment]  # test double
    maybe_print_update_notice(net_on)
    assert _out(net_on) == ""


def test_doctor_shows_notice(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    active_net_on: None,
    pypi: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    rc = main(["doctor"])
    assert rc == 0
    lines = capsys.readouterr().out.rstrip().splitlines()
    assert lines[-2] == "All good."
    assert NOTICE in lines[-1]
    assert lines[-1].endswith(UPGRADE)


def test_doctor_json_has_no_notice(
    tmp_path: Path,
    vault: MemoryVault,
    monkeypatch: pytest.MonkeyPatch,
    active_net_on: None,
    pypi: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_healthy_environment(tmp_path, vault, monkeypatch)
    rc = main(["doctor", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    json.loads(out)
    assert NOTICE not in out
    assert pypi.requests == []


def test_list_shows_notice_after_output(
    vault: MemoryVault,
    http: FakeHttp,
    active_net_on: None,
    pypi: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_config()
    _seed_two_accounts(vault)
    http.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        json_response(_load_fixture("token_refresh.json")),
    )
    http.add(
        "POST",
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
        json_response(_load_fixture("quota_summary.json")),
    )
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.index("mswap · agy accounts") < out.index(NOTICE)
    assert out.rstrip().endswith(UPGRADE)


def test_list_json_and_status_never_check(
    vault: MemoryVault,
    http: FakeHttp,
    active_net_on: None,
    pypi: FakeHttp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_config()
    _seed_two_accounts(vault)
    main(["status"])
    main(["list", "--json", "--quiet"])
    capsys.readouterr()
    assert all(r["url"] != PYPI_URL for r in http.requests)
