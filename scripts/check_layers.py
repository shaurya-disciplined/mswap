"""Enforce architectural layer boundaries in src/mswap."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

LAYER_RANKS: dict[str, int] = {
    "util": 0,
    "vault": 1,
    "agy": 2,
    "core": 3,
    "ui": 4,
    "cli": 5,
}

EXEMPT_MODULES: set[str] = {
    "mswap.core.errors",
    "mswap.core.models",
}


def _resolve_relative_import(current_pkg: list[str], level: int, module: str | None) -> str:
    """Resolve a relative import to an absolute module name."""
    if level > len(current_pkg):
        return ""
    base = current_pkg[: len(current_pkg) - level + 1]
    if module:
        return ".".join([*base, module])
    return ".".join(base)


def check_layers(src_dir: Path) -> int:
    """Scan all Python modules in src_dir and verify import layering rules."""
    violations: list[str] = []

    for path in sorted(src_dir.rglob("*.py")):
        rel_parts = path.relative_to(src_dir).parts
        if not rel_parts:
            continue

        # Determine layer of the current file
        if len(rel_parts) == 1:
            # Top-level module (e.g. __init__.py, __main__.py)
            module_layer = "cli" if rel_parts[0] == "__main__.py" else None
        else:
            module_layer = rel_parts[0]

        if module_layer is None or module_layer not in LAYER_RANKS:
            continue

        current_rank = LAYER_RANKS[module_layer]
        current_pkg = ["mswap", *list(rel_parts[:-1])]

        try:
            content = path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(path))
        except (OSError, SyntaxError) as err:
            violations.append(f"{path}:1: parse error: {err}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if not name.startswith("mswap."):
                        continue
                    if name in EXEMPT_MODULES or any(
                        name.startswith(f"{m}.") for m in EXEMPT_MODULES
                    ):
                        continue
                    parts = name.split(".")
                    if len(parts) >= 2:
                        target_layer = parts[1]
                        if target_layer in LAYER_RANKS:
                            target_rank = LAYER_RANKS[target_layer]
                            if target_rank > current_rank:
                                violations.append(
                                    f"{path}:{node.lineno}: layer violation: "
                                    f"{module_layer} (rank {current_rank}) imports "
                                    f"{name} (rank {target_rank})"
                                )

            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    base_module = _resolve_relative_import(current_pkg, node.level, node.module)
                else:
                    base_module = node.module or ""

                if not base_module.startswith("mswap"):
                    continue

                for alias in node.names:
                    full_name = f"{base_module}.{alias.name}" if base_module else alias.name
                    if full_name in EXEMPT_MODULES or any(
                        full_name.startswith(f"{m}.") for m in EXEMPT_MODULES
                    ):
                        continue
                    if base_module in EXEMPT_MODULES or any(
                        base_module.startswith(f"{m}.") for m in EXEMPT_MODULES
                    ):
                        continue

                    parts = full_name.split(".")
                    if len(parts) >= 2:
                        target_layer = parts[1]
                        if target_layer in LAYER_RANKS:
                            target_rank = LAYER_RANKS[target_layer]
                            if target_rank > current_rank:
                                violations.append(
                                    f"{path}:{node.lineno}: layer violation: "
                                    f"{module_layer} (rank {current_rank}) imports "
                                    f"{full_name} (rank {target_rank})"
                                )

    if violations:
        for v in violations:
            print(v)
        return 1

    print("layers ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for layers checker."""
    args = sys.argv[1:] if argv is None else argv
    src_dir = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "src" / "mswap"
    return check_layers(src_dir)


if __name__ == "__main__":
    sys.exit(main())
