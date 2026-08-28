"""cullr command line entry point."""

from __future__ import annotations

import argparse
import sys

from . import config as conf
from .client import Arr, ArrError, Library
from .config import VERSION
from .server import serve


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="cullr",
        description="Fast visual triage for reclaiming disk space from Radarr and Sonarr.",
        epilog="Config precedence: flags > env vars > config file > auto-discovery.",
    )
    p.add_argument("--version", action="version", version=f"cullr {VERSION}")

    net = p.add_argument_group("server")
    net.add_argument("--host", help="bind address (default 127.0.0.1)")
    net.add_argument("--port", type=int, help="bind port (default 8420)")
    net.add_argument("-o", "--open", dest="open_browser", action="store_true",
                     help="open a browser once listening")

    src = p.add_argument_group("sources")
    src.add_argument("--radarr-url", help="e.g. http://127.0.0.1:7878")
    src.add_argument("--radarr-key", help="Radarr API key")
    src.add_argument("--sonarr-url", help="e.g. http://127.0.0.1:8989")
    src.add_argument("--sonarr-key", help="Sonarr API key")
    src.add_argument("--no-radarr", action="store_true", help="ignore Radarr")
    src.add_argument("--no-sonarr", action="store_true", help="ignore Sonarr")
    src.add_argument("-c", "--config", help="path to a cullr.json config file")

    safe = p.add_argument_group("safety")
    safe.add_argument("--read-only", dest="read_only", action="store_true",
                      help="browse only; the server refuses every delete")
    safe.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="accept deletes and log them, but never call the *arr API")
    safe.add_argument("--no-audit", action="store_true",
                      help="do not append deletions to cullr-deletions.jsonl")

    p.add_argument("--check", action="store_true",
                   help="verify connectivity and print a library summary, then exit")
    return p.parse_args(argv)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024 or unit == "PB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def check(cfg) -> int:
    rc = 0
    for name in ("radarr", "sonarr"):
        inst = cfg.get(name)
        if not inst:
            print(f"  {name:<7} not configured")
            continue
        try:
            info = Arr(inst, cfg.timeout).ping()
            print(f"  {name:<7} OK   v{info.get('version', '?')}  at {inst.url}")
        except ArrError as e:
            print(f"  {name:<7} FAIL {e}")
            rc = 1

    lib = Library(cfg)
    try:
        data = lib.data(force=True)
    except Exception as e:
        print(f"  library fetch failed: {e}")
        return 1

    mv, sr = data["movies"], data["series"]
    print(f"\n  {len(mv)} movies  {human(sum(x['size'] for x in mv))}")
    print(f"  {len(sr)} series  {human(sum(x['size'] for x in sr))}")
    for err in data.get("errors", {}).values():
        print(f"  warning: {err}")

    disks = data["disks"]
    if disks:
        print("\n  disks")
        for label, d in sorted(disks.items()):
            pct = 100 * d["free"] / d["total"] if d["total"] else 0
            flag = "  <-- low" if d["free"] < 50 * 2**30 else ""
            print(f"    {label:<4} {human(d['free']):>9} free of {human(d['total']):>9}"
                  f"  ({pct:4.1f}%){flag}")
    return rc


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg, notes = conf.build(args)

    print(f"cullr {VERSION}")
    for n in notes:
        print(f"  {n}")

    if not any(cfg.get(n) for n in ("radarr", "sonarr")):
        print("\n  No Radarr or Sonarr instance could be reached.\n"
              "  Pass --radarr-url/--radarr-key, set RADARR_API_KEY, or write a cullr.json.\n"
              "  See README.md for the config format.", file=sys.stderr)
        return 2

    if args.check:
        return check(cfg)

    try:
        serve(cfg)
    except OSError as e:
        print(f"\n  cannot bind {cfg.host}:{cfg.port} — {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
