"""CLI command for ``mswap debug record [--out DIR]``.

Captures the *shape* of agy's three quota/tier API responses (strings replaced by `<str:N>`,
see core/debug_record.py) plus an env summary, so a bug report can attach real evidence without
leaking a token or an email. The live login is only read; it is never written.
Never leaves a capture on disk that still looks secret-shaped or email-shaped.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import platform
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mswap import __version__
from mswap.agy.api import (
    FETCH_MODELS_URL,
    LOAD_CODE_ASSIST_URL,
    QUOTA_SUMMARY_URL,
    AgyApi,
)
from mswap.agy.install import agy_version, os_arch, user_agent
from mswap.agy.paths import agy_exe
from mswap.agy.tokens import TokenService
from mswap.cli.context import AppContext
from mswap.core.debug_record import scan_text, shape
from mswap.core.errors import ApiError, MswapError, NotSignedIn, UnsafeOperation, UsageError
from mswap.core.store import live_target
from mswap.ui import jsonout

# (output file, URL, request body, endpoint name): the three calls named in the W6.S5 spec.
_ENDPOINTS: tuple[tuple[str, str, dict[str, Any], str], ...] = (
    ("quota_summary.json", QUOTA_SUMMARY_URL, {}, "v1internal:retrieveUserQuotaSummary"),
    (
        "load_code_assist.json",
        LOAD_CODE_ASSIST_URL,
        {"metadata": {"ideType": "ANTIGRAVITY"}},
        "v1internal:loadCodeAssist",
    ),
    ("fetch_available_models.json", FETCH_MODELS_URL, {}, "v1internal:fetchAvailableModels"),
)
ENV_FILE = "env.json"
# agy's `--version` output goes into env.json verbatim, so only a plain version string is kept.
_VERSION_TEXT = re.compile(r"^\d[\w.+-]{0,39}$")


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _agy_version_text(ctx: AppContext) -> str:
    cache: dict[str, Any] = {}
    client_file = Path(ctx.store.root) / "client.json"
    if client_file.exists():
        with contextlib.suppress(Exception):
            cache = json.loads(client_file.read_text(encoding="utf-8"))
    try:
        version = agy_version(agy_exe(), cache)
    except MswapError:
        return "unknown"
    return version if _VERSION_TEXT.match(version) else "unknown"


def _env_summary(ctx: AppContext, agy_ver: str) -> dict[str, str]:
    return {
        "mswap_version": __version__,
        "agy_version": agy_ver,
        "os_arch": os_arch(),
        "python_version": platform.python_version(),
        "vault_backend": type(ctx.vault).__name__,
    }


def _capture(ctx: AppContext, live: bytes, ua: str) -> dict[str, str]:
    """Call each endpoint and return {file name: shape-only JSON text}."""
    api = AgyApi(ctx.http, ua, clock=ctx.clock)
    service = TokenService(ctx)
    files: dict[str, str] = {}
    errors: list[ApiError] = []
    for file_name, url, body, endpoint in _ENDPOINTS:

        def call(
            token: str, url: str = url, body: dict[str, Any] = body, endpoint: str = endpoint
        ) -> Any:
            return api.raw_json(url, token, json_body=body, endpoint_name=endpoint)

        try:
            files[file_name] = _dump(shape(service.call_with_retry(live, call)))
        except ApiError as e:
            errors.append(e)
            # Only the status and error kind are recorded, never the server's message.
            files[file_name] = _dump({"error": {"status": e.status, "kind": e.kind}})
    if len(errors) == len(_ENDPOINTS):
        raise errors[0]
    return files


def _default_out_dir(ctx: AppContext) -> Path:
    stamp = ctx.clock.now().strftime("%Y%m%d-%H%M%S")
    return Path(ctx.store.root) / f"debug-{stamp}"


def _prepare_out_dir(out: Path) -> bool:
    """Create `out` if needed; return True when this call created it. Refuse a non-empty dir."""
    if out.exists():
        if not out.is_dir() or any(out.iterdir()):
            raise UsageError(
                f"{out} already exists and is not an empty folder.",
                hint="Choose another folder with --out DIR.",
            )
        return False
    out.mkdir(parents=True)
    return True


def _write_and_verify(out: Path, created: bool, files: dict[str, str]) -> None:
    """Write the capture, then scan what is on disk; delete it all and refuse on any hit."""
    written: list[Path] = []
    leaks: list[str] = []
    try:
        for name, text in files.items():
            path = out / name
            path.write_text(text, encoding="utf-8")
            written.append(path)
        for path in written:
            leaks += scan_text(path.read_text(encoding="utf-8"))
    except BaseException:
        _discard(out, created, written)
        raise
    if leaks:
        _discard(out, created, written)
        kinds = ", ".join(sorted(set(leaks)))
        raise UnsafeOperation(
            f"The capture still held something secret-shaped ({kinds}), so nothing was saved.",
            hint="Please report this on the mswap issue tracker without attaching any files.",
        )


def _discard(out: Path, created: bool, written: list[Path]) -> None:
    """Remove only the files this run wrote, and the folder if this run created it."""
    for path in written:
        with contextlib.suppress(OSError):
            path.unlink()
    if created:
        with contextlib.suppress(OSError):
            out.rmdir()


def _record(ctx: AppContext, out_arg: str | None) -> int:
    live: bytes | None = ctx.vault.read(live_target())
    if live is None:
        raise NotSignedIn(
            "agy isn't signed in on this machine.",
            hint="Sign in with `agy`, then run `mswap debug record` again.",
        )
    out = Path(out_arg) if out_arg else _default_out_dir(ctx)
    created = _prepare_out_dir(out)
    try:
        agy_ver = _agy_version_text(ctx)
        files = _capture(ctx, live, user_agent(agy_ver, os_arch()))
        files[ENV_FILE] = _dump(_env_summary(ctx, agy_ver))
    except BaseException:
        _discard(out, created, [])
        raise
    _write_and_verify(out, created, files)

    if ctx.json:
        print(jsonout.ok("debug", {"dir": str(out), "files": sorted(files)}), file=ctx.out)
        return 0
    print(f"Saved to {out}. Safe to attach to a GitHub issue.", file=ctx.out)
    return 0


_ACTIONS: dict[str, Callable[[AppContext, str | None], int]] = {"record": _record}


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the ``mswap debug`` command."""
    action: str | None = getattr(args, "debug_action", None)
    handler = _ACTIONS.get(action or "")
    if handler is None:
        raise UsageError("Missing debug action.", hint="Run `mswap debug record`.")
    return handler(ctx, getattr(args, "out", None))
