"""Unit tests for `mswap completions` and the hidden `mswap __complete` helper."""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mswap.cli import main
from mswap.cli.completions import SHELLS, build_tree, flatten, generate
from mswap.cli.context import AppContext
from mswap.cli.parser import build_parser
from mswap.core.models import Account

FAKE_SELECTORS = ["1", "alice@example.com", "work", "2", "bob@example.com"]


def _account(slot: int, email: str, alias: str | None = None) -> Account:
    now = datetime(2026, 10, 2, tzinfo=UTC)
    return Account(
        slot=slot, email=email, fp=f"{slot:016x}", added_at=now, updated_at=now, alias=alias
    )


def _parser_names(parser: argparse.ArgumentParser) -> tuple[set[str], set[str]]:
    """Independent walk of an argparse parser: (all subcommand spellings, all option strings)."""
    commands: set[str] = set()
    flags: set[str] = set()

    def walk(p: argparse.ArgumentParser) -> None:
        for action in p._actions:
            flags.update(action.option_strings)
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    if not name.startswith("__"):
                        commands.add(name)
                        walk(sub)

    walk(parser)
    return commands, flags


def _has_word(script: str, word: str, shell: str = "bash") -> bool:
    """True if `word` appears as a whole word (fish spells options `-s k` and `-l name`)."""
    if shell == "fish" and word.startswith("-"):
        word = f"-l {word[2:]}" if word.startswith("--") else f"-s {word[1:]}"
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", script) is not None


# --------------------------------------------------------------------------- tree


def test_tree_reads_commands_aliases_and_nesting() -> None:
    root = build_tree(build_parser())
    by_key = {n.key: n for n in flatten(root)}

    assert by_key["list"].aliases == ("ls",)
    assert {"schedule install", "schedule remove", "schedule status"} <= set(by_key)
    assert by_key["schedule"].child_words == ("install", "remove", "status")
    assert not any(k.startswith("__") for k in by_key)


@pytest.mark.parametrize("command", ["switch", "remove", "alias", "disable", "enable"])
def test_selector_positionals_detected(command: str) -> None:
    node = {n.key: n for n in flatten(build_tree(build_parser()))}[command]

    assert node.positionals()[0] == ("selector", ())


def test_choices_and_value_flags_detected() -> None:
    nodes = {n.key: n for n in flatten(build_tree(build_parser()))}

    assert nodes["hook"].positionals() == (("words", ("install", "remove", "status")),)
    assert nodes["completions"].positionals() == (("words", SHELLS),)
    values = dict(nodes["auto"].value_flags())
    assert values["--strategy"].choices == ("best", "consume-first")
    assert values["--interval"].choices == ()


# ------------------------------------------------------------------ script contents


@pytest.mark.parametrize("shell", SHELLS)
def test_script_contains_every_command_and_flag(shell: str) -> None:
    commands, flags = _parser_names(build_parser())
    script = generate(shell)

    assert commands >= {"add", "list", "ls", "switch", "schedule", "install", "completions"}
    assert [c for c in sorted(commands) if not _has_word(script, c, shell)] == []
    assert [f for f in sorted(flags) if not _has_word(script, f, shell)] == []


@pytest.mark.parametrize("shell", SHELLS)
def test_script_hides_internal_commands(shell: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSWAP_DEMO", "1")

    script = generate(shell)

    assert "__demo-seed" not in script
    assert script.count("__complete") == script.count("__complete selectors")


@pytest.mark.parametrize("shell", SHELLS)
def test_script_is_deterministic_lf_only_and_private(shell: str) -> None:
    script = generate(shell)

    assert script == generate(shell)
    assert script.endswith("\n")
    assert "\r" not in script
    assert "\\Users\\" not in script


def _synthetic_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mswap")
    sub = parser.add_subparsers(dest="command")
    frob = sub.add_parser("frobnicate", aliases=["fb"], help="frobnicate the [thing]: it's fun")
    frob.add_argument("--zap-level", choices=["low", "high"], help="zap it")
    frob.add_argument("-k", action="store_true", help="keep going")
    frob.add_argument("selector", help="who")
    deep = sub.add_parser("deep", help="nested")
    deep_sub = deep.add_subparsers(dest="deep_action")
    deep_sub.add_parser("bottom", help="the bottom").add_argument("--depth", type=int)
    return parser


@pytest.mark.parametrize("shell", SHELLS)
def test_nothing_is_hard_coded(shell: str) -> None:
    script = generate(shell, _synthetic_parser())

    expected = ("frobnicate", "fb", "--zap-level", "low", "high", "-k", "deep", "bottom", "--depth")
    for word in expected:
        assert _has_word(script, word, shell), word
    assert not _has_word(script, "doctor", shell)
    assert not _has_word(script, "--refresh", shell)


# ----------------------------------------------------------------- syntax checks


def _runs(*cmd: str) -> bool:
    exe = shutil.which(cmd[0])
    if exe is None:
        return False
    try:
        done = subprocess.run([exe, *cmd[1:]], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _bash_ok() -> bool:
    return _runs("bash", "-c", "true")


def _powershell() -> str | None:
    for name in ("pwsh", "powershell"):
        if _runs(name, "-NoProfile", "-Command", "exit 0"):
            return name
    return None


def _run(cmd: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    exe = shutil.which(cmd[0])
    assert exe is not None
    return subprocess.run(
        [exe, *cmd[1:]],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


@pytest.mark.skipif(not _bash_ok(), reason="bash not available")
def test_bash_syntax() -> None:
    done = _run(["bash", "-n"], stdin=generate("bash"))

    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(not _runs("zsh", "--version"), reason="zsh not available")
def test_zsh_syntax(tmp_path: Path) -> None:
    path = tmp_path / "_mswap"
    path.write_text(generate("zsh"), encoding="utf-8", newline="\n")

    done = _run(["zsh", "-n", str(path)])

    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(not _runs("fish", "--version"), reason="fish not available")
def test_fish_syntax(tmp_path: Path) -> None:
    path = tmp_path / "mswap.fish"
    path.write_text(generate("fish"), encoding="utf-8", newline="\n")

    done = _run(["fish", "--no-execute", str(path)])

    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(_powershell() is None, reason="PowerShell not available")
def test_powershell_syntax(tmp_path: Path) -> None:
    ps = _powershell()
    assert ps is not None
    path = tmp_path / "mswap.ps1"
    path.write_text(generate("powershell"), encoding="utf-8", newline="\n")
    check = (
        "$e = $null; $t = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$t, [ref]$e); "
        "if ($e.Count -gt 0) { $e | ForEach-Object { $_.Message }; exit 1 }"
    )

    done = _run([ps, "-NoProfile", "-Command", check])

    assert done.returncode == 0, done.stdout + done.stderr


# ------------------------------------------------------------ behaviour of scripts

# (words typed so far, the last one being completed) -> expected completions
BEHAVIOUR: list[tuple[list[str], list[str]]] = [
    (["mswap", "sw"], ["switch"]),
    (["mswap", "--json", "sw"], ["switch"]),
    (["mswap", "ls", "--r"], ["--refresh"]),
    (["mswap", "switch", "--f"], ["--force"]),
    (["mswap", "switch", ""], FAKE_SELECTORS),
    (["mswap", "switch", "--wait-timeout", "5", ""], FAKE_SELECTORS),
    (["mswap", "remove", "a"], ["alice@example.com"]),
    (["mswap", "auto", "--strategy", ""], ["best", "consume-first"]),
    (["mswap", "auto", "--focus", "b"], ["both"]),
    (["mswap", "schedule", ""], ["install", "remove", "status"]),
    (["mswap", "schedule", "install", "--e"], ["--every"]),
    (["mswap", "hook", ""], ["install", "remove", "status"]),
    (["mswap", "config", ""], ["get", "list", "path", "set", "unset"]),
    (["mswap", "completions", "p"], ["powershell"]),
    (["mswap", "alias", "work", ""], []),
]


@pytest.mark.skipif(not _bash_ok(), reason="bash not available")
def test_bash_completes_as_expected() -> None:
    lines = [
        "mswap() { printf '%s\\n' " + " ".join(FAKE_SELECTORS) + "; }",
        '_t() { COMP_WORDS=("$@"); COMP_CWORD=$(($# - 1)); _mswap; echo "${COMPREPLY[*]}"; }',
    ]
    lines += ["_t " + " ".join(f"'{w}'" for w in words) for words, _ in BEHAVIOUR]
    lines.append("_t mswap ''")
    done = _run(["bash"], stdin=generate("bash") + "\n" + "\n".join(lines) + "\n")

    assert done.returncode == 0, done.stderr
    got = [line.split() for line in done.stdout.splitlines()]
    assert [sorted(g) for g in got[: len(BEHAVIOUR)]] == [sorted(e) for _, e in BEHAVIOUR]
    assert set(got[-1]) == set(build_tree(build_parser()).child_words)


@pytest.mark.skipif(_powershell() is None, reason="PowerShell not available")
def test_powershell_completes_as_expected(tmp_path: Path) -> None:
    ps = _powershell()
    assert ps is not None
    script_path = tmp_path / "mswap.ps1"
    script_path.write_text(generate("powershell"), encoding="utf-8", newline="\n")
    # Cases with no candidates make PowerShell fall back to file names, so they are skipped.
    cases = [(" ".join(words), expected) for words, expected in BEHAVIOUR if expected]
    driver = [
        "function mswap { " + "; ".join(f"'{s}'" for s in FAKE_SELECTORS) + " }",
        f"Get-Content -Raw '{script_path}' | Out-String | Invoke-Expression",
        "function t($line) {",
        "    $r = TabExpansion2 $line $line.Length",
        "    ($r.CompletionMatches | ForEach-Object { $_.CompletionText }) -join ' '",
        "}",
        *[f"t '{line}'" for line, _ in cases],
    ]
    driver_path = tmp_path / "drive.ps1"
    driver_path.write_text("\n".join(driver) + "\n", encoding="utf-8")

    done = _run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver_path)])

    assert done.returncode == 0, done.stderr
    got = [line.split() for line in done.stdout.splitlines()]
    assert [sorted(g) for g in got] == [sorted(e) for _, e in cases]


# ---------------------------------------------------------------------- commands


@pytest.mark.parametrize("shell", SHELLS)
def test_completions_command_prints_script(ctx: AppContext, shell: str) -> None:
    code = main(["completions", shell], ctx=ctx)

    assert code == 0
    assert ctx.out.getvalue() == generate(shell)
    assert ctx.err.getvalue() == ""


def test_completions_unknown_shell_is_usage_error(
    ctx: AppContext, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["completions", "tcsh"], ctx=ctx)

    assert code == 64
    assert ctx.out.getvalue() == ""
    assert "invalid choice" in capsys.readouterr().err


def test_completions_missing_shell_is_usage_error(ctx: AppContext) -> None:
    assert main(["completions"], ctx=ctx) == 64


def test_help_lists_completions_but_not_the_hidden_helper(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["help"]) == 0
    out = capsys.readouterr().out

    assert "mswap completions" in out
    assert "__complete" not in out


def test_unknown_command_error_does_not_leak_hidden_helper(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["nope"]) == 64

    assert "__complete" not in capsys.readouterr().err


# -------------------------------------------------------------------- __complete


def test_complete_selectors_lists_slots_emails_and_aliases(ctx: AppContext) -> None:
    ctx.store.save([_account(2, "bob@example.com"), _account(1, "alice@example.com", "work")])

    code = main(["__complete", "selectors"], ctx=ctx)

    assert code == 0
    assert ctx.out.getvalue().splitlines() == [
        "1",
        "alice@example.com",
        "work",
        "2",
        "bob@example.com",
    ]


def test_complete_selectors_without_accounts_file_prints_nothing(ctx: AppContext) -> None:
    assert main(["__complete", "selectors"], ctx=ctx) == 0
    assert ctx.out.getvalue() == ""


@pytest.mark.parametrize(
    "content",
    ["{not json", "[]", '{"accounts": 3}', '{"accounts": [1, "x", {"slot": "a"}]}', ""],
)
def test_complete_selectors_survives_bad_accounts_file(ctx: AppContext, content: str) -> None:
    ctx.store.root.mkdir(parents=True, exist_ok=True)
    (ctx.store.root / "accounts.json").write_text(content, encoding="utf-8")

    assert main(["__complete", "selectors"], ctx=ctx) == 0
    assert ctx.out.getvalue() == ""


def test_complete_selectors_skips_values_with_whitespace_and_dedupes(ctx: AppContext) -> None:
    ctx.store.root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 2,
        "accounts": [
            {"slot": 1, "email": "a@example.com", "alias": "a@example.com"},
            {"slot": 2, "email": "has space@example.com", "alias": None},
        ],
    }
    (ctx.store.root / "accounts.json").write_text(json.dumps(payload), encoding="utf-8")

    main(["__complete", "selectors"], ctx=ctx)

    assert ctx.out.getvalue().splitlines() == ["1", "a@example.com", "2"]


def test_complete_reads_accounts_json_only(ctx: AppContext) -> None:
    ctx.store.save([_account(1, "alice@example.com")])
    before = sorted(p.name for p in ctx.store.root.iterdir())

    main(["__complete", "selectors"], ctx=ctx)

    assert sorted(p.name for p in ctx.store.root.iterdir()) == before == ["accounts.json"]


def test_complete_unknown_query_is_usage_error(
    ctx: AppContext, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["__complete", "bogus"], ctx=ctx)

    assert code == 64
    assert "Unknown completion query" in capsys.readouterr().err


def test_completions_output_uses_lf_even_when_stream_translates_newlines(
    ctx: AppContext,
) -> None:
    raw = io.BytesIO()
    ctx.out = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")

    assert main(["completions", "bash"], ctx=ctx) == 0
    ctx.out.flush()

    assert b"\r" not in raw.getvalue()
    assert raw.getvalue().decode("utf-8") == generate("bash")
