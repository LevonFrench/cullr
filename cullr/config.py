"""Configuration resolution for cullr.

Precedence, highest first:

    1. command-line flags
    2. environment variables      (CULLR_RADARR_URL, RADARR_API_KEY, ...)
    3. config file                (--config, ./cullr.json, ~/.config/cullr/config.json)
    4. auto-discovery             (reads the *arr config.xml on this machine)

Nothing is ever written back to the config file, and API keys are never logged.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from . import mediahoarder

APP = "cullr"
VERSION = "1.1.1"

# ---------------------------------------------------------------- discovery

#: Where each *arr keeps config.xml, per platform. First hit wins.
_ARR_DIRS = {
    "Windows": [
        r"{programdata}\{name}",
        r"{localappdata}\{name}",
        r"{appdata}\{name}",
    ],
    "Darwin": [
        "~/.config/{lname}",
        "~/Library/Application Support/{name}",
        "/usr/local/var/{lname}",
    ],
    "Linux": [
        "/config",                       # linuxserver.io containers
        "~/.config/{name}",
        "/var/lib/{lname}",
        "/opt/{name}",
        "/data/{lname}",
    ],
}

_DEFAULT_PORTS = {"radarr": 7878, "sonarr": 8989}


def _candidate_dirs(name: str) -> list[Path]:
    out: list[Path] = []
    for tpl in _ARR_DIRS.get(platform.system(), _ARR_DIRS["Linux"]):
        p = tpl.format(
            name=name.capitalize(),
            lname=name.lower(),
            localappdata=os.environ.get("LOCALAPPDATA", ""),
            appdata=os.environ.get("APPDATA", ""),
            programdata=os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
        )
        if "{" in p or p.strip() in ("", "\\", "/"):
            continue
        out.append(Path(p).expanduser())
    return out


def discover(name: str) -> tuple[Optional[str], Optional[str]]:
    """Return (url, api_key) for a locally-installed *arr, or (None, None)."""
    for d in _candidate_dirs(name):
        cfg = d / "config.xml"
        try:
            if not cfg.is_file():
                continue
            xml = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        def tag(t: str) -> Optional[str]:
            m = re.search(rf"<{t}>(.*?)</{t}>", xml, re.I | re.S)
            return m.group(1).strip() if m else None

        key = tag("ApiKey")
        if not key:
            continue
        port = tag("Port") or str(_DEFAULT_PORTS.get(name, 80))
        base = (tag("UrlBase") or "").strip("/")
        scheme = "https" if (tag("EnableSsl") or "").lower() == "true" else "http"
        url = f"{scheme}://127.0.0.1:{port}" + (f"/{base}" if base else "")
        return url, key
    return None, None


# ---------------------------------------------------------------- model


@dataclass
class Instance:
    name: str                       # "radarr" | "sonarr"
    url: Optional[str] = None
    key: Optional[str] = None
    enabled: bool = True

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.url and self.key)

    def redacted(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "key": ("set" if self.key else None),
            "enabled": self.enabled,
            "ready": self.ready,
        }


@dataclass
class MHConfig:
    """Media-Hoarder source. It has no API, so this is a path, not a URL."""
    db: Optional[str] = None
    enabled: bool = True
    #: allow cullr to delete Media-Hoarder files straight off disk
    allow_delete: bool = False

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.db)

    def redacted(self) -> dict:
        return {
            "name": "mediahoarder",
            "db": self.db,
            "enabled": self.enabled,
            "ready": self.ready,
            "allow_delete": self.allow_delete,
        }


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8420
    radarr: Instance = field(default_factory=lambda: Instance("radarr"))
    sonarr: Instance = field(default_factory=lambda: Instance("sonarr"))
    mediahoarder: MHConfig = field(default_factory=MHConfig)

    #: refuse every mutating request; the UI hides its delete controls
    read_only: bool = False
    #: accept delete requests, log them, but never call the *arr API
    dry_run: bool = False
    #: append every deletion to this JSONL file (None disables the audit log)
    audit: Optional[str] = "cullr-deletions.jsonl"
    #: seconds to reuse a cached library snapshot
    cache_ttl: int = 300
    #: request timeout when talking to the *arr APIs
    timeout: int = 300
    #: open a browser once the server is listening
    open_browser: bool = False
    #: extra roots to report free space for; empty means "infer from library"
    drives: list[str] = field(default_factory=list)

    def instances(self) -> list[Instance]:
        return [self.radarr, self.sonarr]

    def get(self, name: str) -> Optional[Instance]:
        inst = getattr(self, name, None)
        return inst if isinstance(inst, Instance) and inst.ready else None

    def summary(self) -> dict:
        d = asdict(self)
        d["radarr"] = self.radarr.redacted()
        d["sonarr"] = self.sonarr.redacted()
        d["mediahoarder"] = self.mediahoarder.redacted()
        d["version"] = VERSION
        return d


# ---------------------------------------------------------------- loading


def _config_paths(explicit: Optional[str]) -> list[Path]:
    if explicit:
        return [Path(explicit).expanduser()]
    home = Path.home()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    out = [Path.cwd() / "cullr.json"]
    if xdg:
        out.append(Path(xdg) / APP / "config.json")
    out += [
        home / ".config" / APP / "config.json",
        home / f".{APP}.json",
    ]
    return out


def _from_file(path: Optional[str]) -> dict:
    for p in _config_paths(path):
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                data["_source"] = str(p)
                return data
        except (OSError, json.JSONDecodeError) as e:
            print(f"{APP}: ignoring config {p}: {e}", file=sys.stderr)
    return {}


def _env(*names: str) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    return None


def build(args) -> tuple[Config, list[str]]:
    """Resolve a Config from flags + env + file + discovery. Returns (cfg, notes)."""
    notes: list[str] = []
    raw = _from_file(getattr(args, "config", None))
    if raw.get("_source"):
        notes.append(f"config file: {raw['_source']}")

    cfg = Config()
    cfg.host = getattr(args, "host", None) or _env("CULLR_HOST") or raw.get("host", cfg.host)
    cfg.port = int(getattr(args, "port", None) or _env("CULLR_PORT") or raw.get("port", cfg.port))
    cfg.cache_ttl = int(raw.get("cache_ttl", cfg.cache_ttl))
    cfg.timeout = int(raw.get("timeout", cfg.timeout))
    cfg.drives = raw.get("drives", []) or []

    for flag in ("read_only", "dry_run", "open_browser"):
        setattr(cfg, flag, bool(getattr(args, flag, False) or raw.get(flag, False)))

    if getattr(args, "no_audit", False) or raw.get("audit") is False:
        cfg.audit = None
    elif raw.get("audit"):
        cfg.audit = raw["audit"]

    for name in ("radarr", "sonarr"):
        inst: Instance = getattr(cfg, name)
        block = raw.get(name) or {}
        up, uk = name.upper(), name.upper()

        inst.url = (getattr(args, f"{name}_url", None)
                    or _env(f"CULLR_{up}_URL", f"{up}_URL")
                    or block.get("url"))
        inst.key = (getattr(args, f"{name}_key", None)
                    or _env(f"CULLR_{uk}_API_KEY", f"{uk}_API_KEY")
                    or block.get("key") or block.get("api_key"))

        if getattr(args, f"no_{name}", False) or block.get("enabled") is False:
            inst.enabled = False
            continue

        if not (inst.url and inst.key):
            durl, dkey = discover(name)
            inst.url = inst.url or durl
            inst.key = inst.key or dkey
            if inst.ready:
                notes.append(f"{name}: auto-discovered at {inst.url}")
        elif inst.ready:
            notes.append(f"{name}: configured at {inst.url}")

        if not inst.ready and inst.enabled:
            notes.append(f"{name}: not configured (no url/key found) — skipping")
            inst.enabled = False

    mh = cfg.mediahoarder
    mhblock = raw.get("mediahoarder") or {}
    mh.db = (getattr(args, "mh_db", None)
             or _env("CULLR_MH_DB")
             or mhblock.get("db"))
    if getattr(args, "no_mediahoarder", False) or mhblock.get("enabled") is False:
        mh.enabled = False
    else:
        if not mh.db:
            mh.db = mediahoarder.discover()
            if mh.db:
                notes.append(f"mediahoarder: auto-discovered at {mh.db}")
        elif mh.ready:
            notes.append(f"mediahoarder: configured at {mh.db}")
        if not mh.ready:
            mh.enabled = False

    mh.allow_delete = bool(getattr(args, "mh_allow_delete", False)
                           or mhblock.get("allow_delete", False))
    if mh.ready and mh.allow_delete:
        notes.append("mediahoarder: file deletion ENABLED (files are removed from disk)")
    elif mh.ready:
        notes.append("mediahoarder: read-only (pass --mh-allow-delete to permit deletion)")

    if cfg.read_only:
        notes.append("READ-ONLY mode: deletion is disabled")
    if cfg.dry_run:
        notes.append("DRY-RUN mode: deletions are logged but not executed")
    return cfg, notes
