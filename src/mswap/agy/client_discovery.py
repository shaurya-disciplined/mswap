"""Discovery and caching of agy's OAuth client details from the binary."""

from __future__ import annotations

import json
import mmap
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mswap.agy.install import agy_version
from mswap.agy.paths import agy_exe, data_dir
from mswap.core.errors import AgyNotFound, CorruptState
from mswap.util.http import Http, UrllibHttp

CLIENT_ID_RE = rb"\d{6,}-[a-z0-9]{32}\.apps\.googleusercontent\.com"
SECRET_RE = rb"GOCSPX-[A-Za-z0-9_-]{28}"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - OAuth endpoint URL
PREFERRED_PREFIX = "1071006060591-"


@dataclass(frozen=True)
class OAuthClient:
    """Discovered OAuth client credentials for agy."""

    client_id: str
    client_secret: str


def _config_file() -> Path:
    client_path = data_dir() / "client.json"
    if client_path.exists():
        return client_path
    legacy_path = data_dir() / "config.json"
    if legacy_path.exists():
        return legacy_path
    return client_path


def _exe_sig(exe: Path) -> str:
    try:
        real_exe = exe.resolve()
        st = real_exe.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_client_secret(secret: str) -> None:
    """Save the validated client secret to the config cache."""
    cfg_file = _config_file()
    cfg = _load_json(cfg_file)
    cfg["client_secret"] = secret
    _save_json(cfg_file, cfg)


def discover(
    exe: Path,
    cache_file: Path,
    http: Http,
    sample_refresh_token: str | None = None,
    *,
    force: bool = False,
) -> OAuthClient:
    """Discover agy's OAuth client from binary or cache per §A15."""
    resolved_cache = cache_file
    if not resolved_cache.exists():
        legacy = cache_file.with_name("config.json")
        if legacy.exists():
            resolved_cache = legacy
    cache = _load_json(resolved_cache)
    cached_cid = cache.get("client_id")
    cached_secret = cache.get("client_secret")
    cached_secrets = cache.get("secrets", [])

    if not force and cached_cid and cached_secret:
        if not exe.exists():
            return OAuthClient(client_id=str(cached_cid), client_secret=str(cached_secret))
        sig = _exe_sig(exe)
        if sig and cache.get("exe_sig") == sig:
            return OAuthClient(client_id=str(cached_cid), client_secret=str(cached_secret))

    if not exe.exists():
        if cached_cid and cached_secret:
            return OAuthClient(client_id=str(cached_cid), client_secret=str(cached_secret))
        if cached_cid and cached_secrets:
            return OAuthClient(client_id=str(cached_cid), client_secret=str(cached_secrets[0]))
        raise AgyNotFound(f"agy not found at {exe}", hint="Install agy, or set MSWAP_AGY_EXE.")

    real_exe = exe.resolve()
    with real_exe.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        raw_ids = [b.decode("utf-8") for b in re.findall(CLIENT_ID_RE, m)]
        raw_secrets = [b.decode("utf-8") for b in re.findall(SECRET_RE, m)]

    ordered_ids: list[str] = []
    for cid in raw_ids:
        if cid.startswith(PREFERRED_PREFIX) and cid not in ordered_ids:
            ordered_ids.append(cid)
    for cid in raw_ids:
        if cid not in ordered_ids:
            ordered_ids.append(cid)

    ordered_secrets: list[str] = []
    for sec in raw_secrets:
        if sec not in ordered_secrets:
            ordered_secrets.append(sec)

    if not ordered_ids or not ordered_secrets:
        raise CorruptState("couldn't find agy's OAuth client in agy.exe (did agy change?)")

    chosen_id: str = ordered_ids[0]
    chosen_secret: str | None = None

    if sample_refresh_token:
        for cid in ordered_ids:
            for sec in ordered_secrets:
                resp = http.request(
                    "POST",
                    TOKEN_URL,
                    form={
                        "client_id": cid,
                        "client_secret": sec,
                        "refresh_token": sample_refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                if resp.status == 200:
                    chosen_id = cid
                    chosen_secret = sec
                    break
            if chosen_secret is not None:
                break

    if chosen_secret is None:
        if not force and cached_secret and cached_secret in ordered_secrets:
            chosen_secret = str(cached_secret)
        else:
            chosen_secret = ordered_secrets[0]

    version = agy_version(exe, cache)
    save_data = {
        "exe_sig": _exe_sig(exe),
        "client_id": chosen_id,
        "client_secret": chosen_secret,
        "agy_version": version,
        "secrets": ordered_secrets,
    }
    _save_json(cache_file, save_data)
    return OAuthClient(client_id=chosen_id, client_secret=chosen_secret)


def rediscover(
    exe: Path | None = None,
    cache_file: Path | None = None,
    http: Http | None = None,
    sample_refresh_token: str | None = None,
) -> OAuthClient:
    """Force a rescan of agy's OAuth client and clear the cached secret."""
    target_exe = exe or agy_exe()
    target_cache = cache_file or _config_file()
    if not target_cache.exists():
        legacy = target_cache.with_name("config.json")
        if legacy.exists():
            target_cache = legacy
    target_http = http or UrllibHttp()

    if target_cache.exists():
        cache = _load_json(target_cache)
        cache.pop("client_secret", None)
        _save_json(target_cache, cache)

    return discover(
        target_exe,
        target_cache,
        target_http,
        sample_refresh_token=sample_refresh_token,
        force=True,
    )


def load_client(
    force: bool = False,
    *,
    exe_path: Path | None = None,
    config_path: Path | None = None,
    http: Http | None = None,
    sample_refresh_token: str | None = None,
) -> dict[str, Any]:
    """Load agy OAuth client configuration, scanning agy binary if needed."""
    exe = exe_path or agy_exe()
    cfg_file = config_path or _config_file()
    h = http or UrllibHttp()
    client = discover(exe, cfg_file, h, sample_refresh_token=sample_refresh_token, force=force)
    cfg = _load_json(cfg_file)
    cfg["client_id"] = client.client_id
    cfg["client_secret"] = client.client_secret
    if "version" not in cfg and "agy_version" in cfg:
        cfg["version"] = cfg["agy_version"]
    return cfg
