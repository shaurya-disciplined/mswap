"""Doctor command diagnosing installation, accounts, storage, and network health.

Owns environment diagnostics, health verification, and --repair recovery.
Must never log or leak credential tokens or client secrets.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import mmap
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from mswap.agy.api import AgyApi
from mswap.agy.client_discovery import (
    CLIENT_ID_RE,
    SECRET_RE,
    _config_file,
    _exe_sig,
    _load_json,
)
from mswap.agy.install import agy_version, os_arch, user_agent
from mswap.agy.paths import agy_exe
from mswap.agy.tokens import TokenService, validate_blob
from mswap.cli.context import AppContext
from mswap.cli.update_notice import maybe_print_update_notice
from mswap.core.errors import CorruptState, TokenDead
from mswap.core.identity import find_by_fp
from mswap.core.models import Account
from mswap.core.store import live_target, slot_target, vault_prefix
from mswap.core.switcher import recover


@dataclass(frozen=True)
class Check:
    """Individual health check diagnosis result."""

    id: str
    status: Literal["ok", "warn", "fail"]
    message: str
    hint: str | None = None


def check_agy_installed(exe: Path, cache: dict[str, Any]) -> Check:
    """Verify that the Antigravity CLI executable exists."""
    if exe.exists():
        version = agy_version(exe, cache)
        return Check(
            id="agy.installed",
            status="ok",
            message=f"agy {version} at {exe}",
        )
    return Check(
        id="agy.installed",
        status="fail",
        message=f"agy not found at {exe}",
        hint="Install agy, or set MSWAP_AGY_EXE.",
    )


def check_agy_login(ctx: AppContext) -> Check:
    """Verify that the live agy credential is valid."""
    live = ctx.vault.read(live_target())
    if live is None:
        return Check(
            id="agy.login",
            status="warn",
            message="agy is signed out",
            hint="Run `agy` to sign in.",
        )
    try:
        validate_blob(live)
        return Check(
            id="agy.login",
            status="ok",
            message="agy is signed in",
        )
    except Exception:
        return Check(
            id="agy.login",
            status="fail",
            message="agy's login entry is damaged",
            hint="Sign in again in agy to restore the login entry.",
        )


def check_agy_client(exe: Path, cache_file: Path) -> Check:
    """Verify that OAuth client details are valid in cache or binary."""
    resolved_cache = cache_file
    if not resolved_cache.exists():
        legacy = cache_file.with_name("config.json")
        if legacy.exists():
            resolved_cache = legacy
    cache = _load_json(resolved_cache) if resolved_cache.exists() else {}
    cached_id = cache.get("client_id")
    raw_secrets_list = cache.get("secrets")
    first_secret = (
        raw_secrets_list[0]
        if isinstance(raw_secrets_list, list) and len(raw_secrets_list) > 0
        else None
    )
    cached_secret = cache.get("client_secret") or first_secret
    cached_sig = cache.get("exe_sig")

    if cached_id and cached_secret:
        if not exe.exists():
            return Check(
                id="agy.client",
                status="ok",
                message="OAuth client configuration valid",
            )
        sig = _exe_sig(exe)
        if sig and cached_sig == sig:
            return Check(
                id="agy.client",
                status="ok",
                message="OAuth client configuration valid",
            )

    if exe.exists():
        with contextlib.suppress(Exception):
            with exe.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
                raw_ids = re.findall(CLIENT_ID_RE, m)
                raw_secrets = re.findall(SECRET_RE, m)
            if len(raw_ids) >= 1 and len(raw_secrets) >= 1:
                return Check(
                    id="agy.client",
                    status="ok",
                    message="OAuth client configuration valid",
                )

    return Check(
        id="agy.client",
        status="fail",
        message="OAuth client details missing or unreadable",
        hint="agy may have changed. Please open an issue with `mswap debug record` output.",
    )


def check_agy_sessions(ctx: AppContext) -> Check:
    """Verify whether any agy processes are currently running."""
    procs_fn = getattr(ctx, "procs", None)
    procs = procs_fn() if callable(procs_fn) else []
    n = len(procs)
    if n == 0:
        return Check(
            id="agy.sessions",
            status="ok",
            message="agy isn't running",
        )
    return Check(
        id="agy.sessions",
        status="warn",
        message=f"{n} agy session(s) running",
    )


def check_store_readable(ctx: AppContext) -> tuple[Check, list[Account] | None]:
    """Verify that accounts.json loads correctly."""
    try:
        accounts = ctx.store.load()
        n = len(accounts)
        return (
            Check(
                id="store.readable",
                status="ok",
                message=f"{n} accounts",
            ),
            accounts,
        )
    except CorruptState as e:
        return (
            Check(
                id="store.readable",
                status="fail",
                message=e.message,
                hint=e.hint,
            ),
            None,
        )
    except Exception as e:
        return (
            Check(
                id="store.readable",
                status="fail",
                message=f"Failed to load accounts: {e}",
                hint="Check accounts.json in your mswap data directory.",
            ),
            None,
        )


def check_store_slots(ctx: AppContext, accounts: list[Account] | None) -> Check:
    """Verify that every saved account has its slot credential in the vault."""
    if accounts is None:
        return Check(
            id="store.slots",
            status="fail",
            message="Cannot verify account slots because accounts file could not be read",
            hint="Fix accounts.json first.",
        )
    for acc in sorted(accounts, key=lambda a: a.slot):
        target = slot_target(acc.slot)
        if ctx.vault.read(target) is None:
            return Check(
                id="store.slots",
                status="fail",
                message=f"Account {acc.slot}'s saved login is missing",
                hint="Sign in as it in agy and run `mswap add`.",
            )
    return Check(
        id="store.slots",
        status="ok",
        message="all accounts have saved logins",
    )


def check_store_orphans(ctx: AppContext, accounts: list[Account] | None) -> Check:
    """Check for slot credentials in the vault that have no account metadata."""
    prefix = vault_prefix()
    entries = ctx.vault.list(f"{prefix}slot")
    known_slots = {a.slot for a in accounts} if accounts is not None else set()
    pattern = re.compile(rf"^{re.escape(prefix)}slot(\d+)$")
    orphans: list[str] = []
    for entry in sorted(entries):
        m = pattern.match(entry)
        if m:
            slot_num = int(m.group(1))
            if slot_num not in known_slots:
                orphans.append(f"slot{slot_num}")
    if orphans:
        return Check(
            id="store.orphans",
            status="warn",
            message=f"Found saved logins mswap no longer tracks: {', '.join(orphans)}",
            hint="Run `mswap doctor --repair` to remove them.",
        )
    return Check(
        id="store.orphans",
        status="ok",
        message="no orphan saved logins",
    )


def check_store_active(ctx: AppContext, accounts: list[Account] | None) -> Check:
    """Verify whether the active live login matches a saved account."""
    live = ctx.vault.read(live_target())
    if live is None:
        return Check(
            id="store.active",
            status="warn",
            message="agy is signed out",
            hint="Run `agy` to sign in.",
        )
    if accounts is None:
        return Check(
            id="store.active",
            status="warn",
            message="Cannot verify active account because accounts file could not be read",
        )
    active = find_by_fp(accounts, live)
    if active is not None:
        return Check(
            id="store.active",
            status="ok",
            message=f"Active: {active.slot} {active.email}",
        )
    return Check(
        id="store.active",
        status="warn",
        message="agy is signed in to an unsaved account",
        hint="`mswap add`",
    )


def check_journal(ctx: AppContext) -> Check:
    """Verify that the switch transaction journal is clean."""
    try:
        st = ctx.journal.state()
        if st is None or st.get("state") in ("committed", "failed"):
            return Check(
                id="journal",
                status="ok",
                message="switch journal clean",
            )
        return Check(
            id="journal",
            status="fail",
            message="An interrupted switch was found",
            hint="Run `mswap doctor --repair`.",
        )
    except Exception:
        return Check(
            id="journal",
            status="fail",
            message="An interrupted switch was found",
            hint="Run `mswap doctor --repair`.",
        )


def check_data_writable(ctx: AppContext) -> Check:
    """Verify that the mswap data directory is writable."""
    try:
        d = ctx.store.root
        d.mkdir(parents=True, exist_ok=True)
        test_file = d / ".mswap_doctor_tmp"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return Check(
            id="data.writable",
            status="ok",
            message="data directory is writable",
        )
    except Exception as e:
        return Check(
            id="data.writable",
            status="fail",
            message=f"data directory not writable: {e}",
            hint="Check file permissions on your mswap data directory.",
        )


def check_win_launchers() -> Check:
    """Check for Smart App Control blocked launchers on Windows."""
    if sys.platform != "win32" and not os.environ.get("MSWAP_TEST_FORCE_WINDOWS"):
        return Check(
            id="win.launchers",
            status="ok",
            message="no blocked launchers",
        )

    dirs_to_check: list[Path] = []
    path_env = os.environ.get("PATH", "")
    for p in path_env.split(os.pathsep):
        if p.strip():
            d = Path(p.strip())
            if d.is_dir() and d not in dirs_to_check:
                dirs_to_check.append(d)

    tools_bin_env = os.environ.get("MSWAP_TOOLS_BIN")
    extra_bin = (
        Path(tools_bin_env) if tools_bin_env else (Path("E:/") / "Apps" / "uv-tools" / "bin")
    )
    if extra_bin.is_dir() and extra_bin not in dirs_to_check:
        dirs_to_check.append(extra_bin)

    for d in dirs_to_check:
        try:
            has_launcher = any(
                (d / name).exists() for name in ("mswap.exe", "mswap.cmd", "cswap.exe", "cswap.cmd")
            )
            if has_launcher or d == extra_bin:
                blocked = list(d.glob("*.sac-blocked"))
                if blocked:
                    return Check(
                        id="win.launchers",
                        status="warn",
                        message="Smart App Control blocked a launcher",
                        hint="Run `mswap shim install`.",
                    )
        except OSError:
            continue

    return Check(
        id="win.launchers",
        status="ok",
        message="no blocked launchers",
    )


def check_autopilot_writeback(ctx: AppContext) -> Check:
    """Verify whether agy write-back of old credentials was detected recently."""
    path = ctx.store.root / "autopilot.json"
    if not path.exists():
        return Check(
            id="autopilot.writeback",
            status="ok",
            message="no write-back detected",
        )
    with contextlib.suppress(Exception):
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("writeback_suspected_at")
        if raw:
            dt = datetime.fromisoformat(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            now = ctx.clock.now()
            if (now - dt).total_seconds() <= 86400:
                return Check(
                    id="autopilot.writeback",
                    status="warn",
                    message="agy write-back suspected recently",
                    hint=(
                        "A running agy may have restored its old login after a switch. "
                        "Restart agy after switching, or use `mswap switch --resume`."
                    ),
                )
    return Check(
        id="autopilot.writeback",
        status="ok",
        message="no write-back detected",
    )


def check_net_refresh(ctx: AppContext, accounts: list[Account] | None) -> Check:
    """Online check: refresh access tokens for all non-quarantined accounts."""
    if not accounts:
        return Check(
            id="net.refresh",
            status="ok",
            message="no accounts to refresh",
        )
    token_service = TokenService(ctx)
    for acc in sorted(accounts, key=lambda a: a.slot):
        if acc.quarantined is not None:
            continue
        try:
            token_service.fresh_for_slot(acc)
        except TokenDead as e:
            return Check(
                id="net.refresh",
                status="fail",
                message=f"Account {acc.slot} ({acc.email})'s saved login is dead",
                hint=e.hint or "Sign in as it in agy, then run `mswap add`.",
            )
        except Exception as e:
            msg = getattr(e, "message", str(e))
            hint = getattr(e, "hint", None)
            return Check(
                id="net.refresh",
                status="fail",
                message=f"Account {acc.slot} refresh failed: {msg}",
                hint=hint,
            )
    return Check(
        id="net.refresh",
        status="ok",
        message="all accounts refreshed or fresh",
    )


def check_net_quota(ctx: AppContext, accounts: list[Account] | None) -> Check:
    """Online check: reachability of Google Quota API."""
    live = ctx.vault.read(live_target())
    active = find_by_fp(accounts, live) if (accounts and live) else None
    target_acc = active if (active and active.quarantined is None) else None
    if target_acc is None and accounts:
        target_acc = next(
            (a for a in sorted(accounts, key=lambda a: a.slot) if a.quarantined is None), None
        )

    token_service = TokenService(ctx)
    try:
        if target_acc is not None:
            token = token_service.fresh_for_slot(target_acc)
        elif live is not None:
            token = token_service.fresh_for_live(live)
        else:
            return Check(
                id="net.quota",
                status="fail",
                message="no account available to check quota",
                hint="Sign in to agy and run `mswap add`.",
            )
    except Exception as e:
        return Check(
            id="net.quota",
            status="fail",
            message=getattr(e, "message", str(e)),
            hint=getattr(e, "hint", None),
        )

    cache_file = _config_file()
    cache: dict[str, Any] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    version = agy_version(agy_exe(), cache)
    ua = user_agent(version, os_arch())
    api = AgyApi(ctx.http, ua)

    try:
        api.quota_summary(token)
        return Check(
            id="net.quota",
            status="ok",
            message="quota API reachable",
        )
    except Exception as e:
        return Check(
            id="net.quota",
            status="fail",
            message=getattr(e, "message", str(e)),
            hint=getattr(e, "hint", None),
        )


def repair(ctx: AppContext) -> list[str]:
    """Execute automatic repairs per §A7: recovery and orphan removal."""
    repaired: list[str] = []
    rec_msg = recover(ctx)
    if rec_msg:
        repaired.append(rec_msg)

    prefix = vault_prefix()
    entries = ctx.vault.list(f"{prefix}slot")
    try:
        accounts = ctx.store.load()
        known_slots = {a.slot for a in accounts}
    except Exception:
        known_slots = set()

    pattern = re.compile(rf"^{re.escape(prefix)}slot(\d+)$")
    for entry in sorted(entries):
        m = pattern.match(entry)
        if m:
            slot_num = int(m.group(1))
            if slot_num not in known_slots:
                ctx.vault.delete(entry)
                repaired.append(f"Deleted orphan saved login: slot{slot_num}")

    return repaired


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    """Execute the doctor command."""
    repaired_actions: list[str] = []
    if getattr(args, "repair", False):
        repaired_actions = repair(ctx)
        if not ctx.json:
            for act in repaired_actions:
                print(f"✓ {act}", file=ctx.out)

    exe = agy_exe()
    cache_file = _config_file()
    cache = _load_json(cache_file) if cache_file.exists() else {}

    checks: list[Check] = []
    checks.append(check_agy_installed(exe, cache))
    checks.append(check_agy_login(ctx))
    checks.append(check_agy_client(exe, cache_file))
    checks.append(check_agy_sessions(ctx))

    chk_store, accounts = check_store_readable(ctx)
    checks.append(chk_store)
    checks.append(check_store_slots(ctx, accounts))
    checks.append(check_store_orphans(ctx, accounts))
    checks.append(check_store_active(ctx, accounts))
    checks.append(check_journal(ctx))
    checks.append(check_data_writable(ctx))
    checks.append(check_win_launchers())
    checks.append(check_autopilot_writeback(ctx))

    if getattr(args, "online", False):
        checks.append(check_net_refresh(ctx, accounts))
        checks.append(check_net_quota(ctx, accounts))

    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    problems = fails + warns
    is_ok = fails == 0

    if ctx.json:
        checks_data = [
            {
                "id": c.id,
                "status": c.status,
                "message": c.message,
                "hint": c.hint,
            }
            for c in checks
        ]
        payload: dict[str, Any] = {
            "schema": 1,
            "ok": is_ok,
            "command": "doctor",
            "data": {
                "checks": checks_data,
                "ok": is_ok,
            },
            "checks": checks_data,
        }
        if repaired_actions:
            payload["data"]["repaired"] = repaired_actions
            payload["repaired"] = repaired_actions
        print(json.dumps(payload, ensure_ascii=False), file=ctx.out)
        return 1 if fails > 0 else 0

    theme = ctx.theme
    for c in checks:
        glyph: str
        if c.status == "ok":
            glyph = str(theme.ok)
        elif c.status == "warn":
            glyph = theme.warn("!")
        else:
            glyph = str(theme.err)
        print(f"{glyph} {c.message}", file=ctx.out)
        if c.hint:
            print(f"  → {theme.dim(c.hint)}", file=ctx.out)

    if problems == 0:
        print("All good.", file=ctx.out)
    else:
        word = "problem" if problems == 1 else "problems"
        print(f"{problems} {word} found.", file=ctx.out)

    maybe_print_update_notice(ctx)
    return 1 if fails > 0 else 0
