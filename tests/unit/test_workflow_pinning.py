"""Supply-chain guard: every GitHub Action is pinned to a full commit SHA (threat model: T-4)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+){0,2}$")
RISKY = re.compile(
    r"\$\{\{\s*github\.(event\.[\w.]*(name|title|body|ref|label|message)"
    r"|head_ref|ref_name|ref)\s*\}\}"
)


def _uses_lines(path: Path) -> list[str]:
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(?:-\s+)?uses:\s*(\S.*)$", line)
        if match and not match.group(1).startswith("./"):
            found.append(match.group(1).strip())
    return found


def interpolated_event_text(text: str) -> list[str]:
    """Return script lines (under `run:`) that splice attacker-shaped event text in via `${{ }}`."""
    hits: list[str] = []
    run_indent: int | None = None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        key = re.match(r"^(?:-\s+)?run:\s*(.*)$", stripped)
        if key:
            run_indent = indent
            inline = key.group(1)
            if inline and not inline.startswith(("|", ">")) and RISKY.search(line):
                hits.append(stripped)
            continue
        if run_indent is not None:
            if stripped and indent <= run_indent:
                run_indent = None
            elif RISKY.search(line):
                hits.append(stripped)
    return hits


def test_workflows_exist() -> None:
    assert WORKFLOWS, "expected .github/workflows/*.yml"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit_sha_with_a_version_comment(workflow: Path) -> None:
    for ref in _uses_lines(workflow):
        assert PINNED.match(ref), f"{workflow.name}: '{ref}' must be owner/repo@<40-hex> # vX.Y.Z"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_declares_least_privilege_token(workflow: Path) -> None:
    assert re.search(r"^permissions:", workflow.read_text(encoding="utf-8"), re.MULTILINE)


def test_interpolation_detector_flags_the_unsafe_pattern() -> None:
    bad = (
        "steps:\n"
        "  - run: |\n"
        '      TAG="${{ github.event.release.tag_name }}"\n'
        "  - run: echo ${{ github.head_ref }}\n"
    )
    assert interpolated_event_text(bad) == [
        'TAG="${{ github.event.release.tag_name }}"',
        "- run: echo ${{ github.head_ref }}",
    ]
    ok = 'steps:\n  - env:\n      TAG: ${{ github.ref_name }}\n    run: |\n      echo "$TAG"\n'
    assert interpolated_event_text(ok) == []


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_event_text_is_interpolated_into_a_shell_script(workflow: Path) -> None:
    """Tag, branch and title text goes through `env:`, never `${{ }}` inside `run:`."""
    assert interpolated_event_text(workflow.read_text(encoding="utf-8")) == []


def test_dependabot_keeps_actions_fresh() -> None:
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert "package-ecosystem: github-actions" in text
    assert "interval: weekly" in text
