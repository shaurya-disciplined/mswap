"""Check test fixtures and golden files for accidental secret leakage."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "access_token": re.compile(r"ya29\.(?!FAKE)[\w-]{10,}"),
    "refresh_token": re.compile(r"1//(?!FAKE)[\w-]{10,}"),
    "client_secret": re.compile(r"GOCSPX-(?!FAKE)[\w-]{10,}"),
    "jwt": re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}


def check_fixtures(dirs: list[Path]) -> int:
    """Scan fixture directories and fail if any secret-shaped strings match."""
    violations: list[str] = []
    file_count = 0

    for base_dir in dirs:
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.rglob("*")):
            if not path.is_file():
                continue
            file_count += 1
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as err:
                violations.append(f"{path}:1: read_error ({err})")
                continue

            for line_idx, line in enumerate(content.splitlines(), start=1):
                for name, pattern in PATTERNS.items():
                    if pattern.search(line):
                        violations.append(f"{path}:{line_idx}: {name}")

    if violations:
        for v in violations:
            print(v)
        return 1

    print(f"fixtures clean ({file_count} files)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for fixture checker."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        target_dirs = [Path(arg) for arg in args]
    else:
        repo_root = Path(__file__).resolve().parent.parent
        target_dirs = [repo_root / "tests" / "fixtures", repo_root / "tests" / "golden"]
    return check_fixtures(target_dirs)


if __name__ == "__main__":
    sys.exit(main())
