"""Scan tracked git files to prevent committing private or personal data."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

RULE_WINDOWS_USER_PATH = "windows_user_path"
RULE_LOCAL_APPS_PATH = "local_apps_path"
RULE_REAL_EMAIL = "real_email"
RULE_PRIVATE_FILE = "private_file"
RULE_DENY_LIST = "deny_list"

RE_WINDOWS_USER_PATH = re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+")
RE_LOCAL_APPS_PATH = re.compile(r"[A-Za-z]:[\\/]Apps[\\/]")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

ALLOWED_EMAIL_SUFFIXES = (
    "@example.com",
    "@users.noreply.github.com",
)


def get_tracked_files(repo_root: Path) -> list[str]:
    """Return relative paths of all files tracked by git."""
    git_bin = shutil.which("git") or "git"
    result = subprocess.run(  # noqa: S603 - git ls-files uses trusted binary on local repo
        [git_bin, "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def load_deny_list(repo_root: Path) -> list[str]:
    """Load case-insensitive denial terms if present."""
    denylist_file = repo_root / ".agent" / "privacy-denylist.txt"
    if not denylist_file.is_file():
        return []
    try:
        content = denylist_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    terms: list[str] = []
    for line in content.splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            terms.append(term.lower())
    return terms


def check_privacy(repo_root: Path) -> int:
    """Run privacy checks on all tracked files in the repository."""
    try:
        tracked_files = get_tracked_files(repo_root)
    except (subprocess.CalledProcessError, FileNotFoundError) as err:
        print(f"Error determining tracked files: {err}", file=sys.stderr)
        return 1

    deny_terms = load_deny_list(repo_root)
    violations: list[str] = []

    for rel_path in tracked_files:
        norm_path = rel_path.replace("\\", "/")
        path_obj = Path(rel_path)

        # Rule: private_file
        if (
            norm_path.startswith(".agent/")
            or path_obj.name == "AGENTS.md"
            or path_obj.name == "GEMINI.md"
        ):
            violations.append(f"{rel_path}:1: {RULE_PRIVATE_FILE}")
            continue

        full_path = repo_root / rel_path
        if not full_path.is_file():
            continue

        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary or non-utf8 tracked file; skip line-by-line inspection
            continue
        except OSError as err:
            violations.append(f"{rel_path}:1: read_error ({err})")
            continue

        for line_idx, line in enumerate(content.splitlines(), start=1):
            if RE_WINDOWS_USER_PATH.search(line):
                violations.append(f"{rel_path}:{line_idx}: {RULE_WINDOWS_USER_PATH}")

            if RE_LOCAL_APPS_PATH.search(line):
                violations.append(f"{rel_path}:{line_idx}: {RULE_LOCAL_APPS_PATH}")

            for email_match in RE_EMAIL.finditer(line):
                email = email_match.group(0).lower()
                if not any(email.endswith(suffix) for suffix in ALLOWED_EMAIL_SUFFIXES):
                    violations.append(f"{rel_path}:{line_idx}: {RULE_REAL_EMAIL}")
                    break

            if deny_terms:
                lower_line = line.lower()
                for term in deny_terms:
                    if term in lower_line:
                        violations.append(f"{rel_path}:{line_idx}: {RULE_DENY_LIST}")
                        break

    if violations:
        for v in violations:
            print(v)
        return 1

    print("privacy checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for privacy checker."""
    args = sys.argv[1:] if argv is None else argv
    repo_root = Path(args[0]).resolve() if args else Path(__file__).resolve().parent.parent
    return check_privacy(repo_root)


if __name__ == "__main__":
    sys.exit(main())
