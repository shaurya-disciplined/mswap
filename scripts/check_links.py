"""Verify that all relative file links in README and docs resolve to existing files.

Used in local verification and CI quality jobs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Match markdown links: [text](target) and ![alt](target)
MD_LINK_RE = re.compile(r"!?\[.*?\]\(([^)]+)\)")
HTML_SRC_RE = re.compile(r"""(?:src|href)=["']([^"']+)["']""")

IGNORED_DIRS = {".git", ".venv", ".agent", "__pycache__", "dist", "build"}


def check_markdown_file(md_file: Path) -> list[str]:
    """Check that all relative links in a markdown file resolve on disk."""
    errors: list[str] = []
    base_dir = md_file.parent

    try:
        content = md_file.read_text(encoding="utf-8")
    except Exception as e:
        return [f"Could not read {md_file}: {e}"]

    lines = content.splitlines()
    in_code_block = False
    for line_num, line in enumerate(lines, start=1):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        targets: list[str] = []

        # Find markdown links
        for match in MD_LINK_RE.finditer(line):
            raw_url = match.group(1).strip()
            # If formatted like `url "title"`, extract just the url
            clean_url = raw_url.split()[0] if raw_url else ""
            targets.append(clean_url)

        # Find HTML src/href tags
        for match in HTML_SRC_RE.finditer(line):
            targets.append(match.group(1).strip())

        for target in targets:
            if not target:
                continue
            # Ignore external protocols and anchor-only links
            if target.startswith(("http://", "https://", "mailto:", "ftp:")) or target.startswith(
                "#"
            ):
                continue

            # Strip in-page anchor
            file_part = target.split("#", 1)[0]
            if not file_part:
                continue

            target_path = (base_dir / file_part).resolve()
            if not target_path.exists():
                errors.append(
                    f"{md_file}:{line_num}: broken relative link '{target}' -> '{target_path}'"
                )

    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    all_errors: list[str] = []

    # Check root README.md and all docs/*.md files
    md_files: list[Path] = []
    for path in repo_root.rglob("*.md"):
        parts = path.relative_to(repo_root).parts
        if any(ignored in parts for ignored in IGNORED_DIRS):
            continue
        md_files.append(path)

    for md_file in sorted(md_files):
        errs = check_markdown_file(md_file)
        all_errors.extend(errs)

    if all_errors:
        print("Broken relative links found:", file=sys.stderr)
        for err in all_errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    print("links ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
