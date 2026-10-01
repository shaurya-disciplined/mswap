"""Discovery and caching of agy's OAuth client details from the binary."""

from __future__ import annotations

import json
import mmap
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from mswap.agy.paths import agy_exe
from mswap.core.errors import MswapError

CLIENT_ID_RE = rb"1071006060591-[a-z0-9]{32}\.apps\.googleusercontent\.com"
SECRET_RE = rb"GOCSPX-[A-Za-z0-9_-]{28}"


def _config_file() -> Path:
    home = Path(os.environ["MSWAP_HOME"]) if "MSWAP_HOME" in os.environ else Path.home() / ".mswap"
    return home / "config.json"


def _exe_sig(exe: Path) -> str:
    st = exe.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


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


def load_client(
    force: bool = False,
    *,
    exe_path: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Load agy OAuth client configuration, scanning agy binary if needed."""
    exe = exe_path or agy_exe()
    cfg_file = config_path or _config_file()
    cfg = _load_json(cfg_file)

    if not force and cfg:
        if not exe.exists() and cfg.get("client_id"):
            return cfg
        if exe.exists() and cfg.get("exe_sig") == _exe_sig(exe):
            return cfg

    if not exe.exists():
        raise MswapError(f"agy not found at {exe}")

    with exe.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        match = re.search(CLIENT_ID_RE, m)
        client_id = match.group().decode("utf-8") if match else None
        secrets = sorted({bytes(s).decode("utf-8") for s in re.findall(SECRET_RE, m)})

    if not client_id or not secrets:
        raise MswapError("couldn't find agy's OAuth client in agy.exe (did agy change?)")

    try:
        out = subprocess.run(  # noqa: S603 - executing trusted local agy executable
            [str(exe), "--version"], capture_output=True, text=True, timeout=15
        )
        version = out.stdout.strip() or cfg.get("version", "1.2.12")
    except (OSError, subprocess.TimeoutExpired):
        version = cfg.get("version", "1.2.12")

    cfg = {
        "exe_sig": _exe_sig(exe),
        "client_id": client_id,
        "secrets": secrets,
        "client_secret": cfg.get("client_secret"),
        "version": version,
    }
    _save_json(cfg_file, cfg)
    return cfg
