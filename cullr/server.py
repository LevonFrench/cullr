"""HTTP server for cullr.

Binds to localhost by default. There is no authentication, because the only
credentials involved are your own *arr API keys and they never leave the box.
If you bind to a routable address, put it behind something that does authn.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import socketserver
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .client import ArrError, Library
from .config import VERSION, Config

STATIC = Path(__file__).parent / "static"


def _audit(cfg: Config, record: dict) -> None:
    if not cfg.audit:
        return
    try:
        with open(cfg.audit, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = f"cullr/{VERSION}"
    cfg: Config
    lib: Library

    def log_message(self, fmt, *args):  # quiet by default
        if os.environ.get("CULLR_ACCESS_LOG"):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------ helpers

    def _send(self, code: int, body, ctype="application/json", extra=None):
        if ctype == "application/json" and not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _static(self, name: str):
        base = STATIC.resolve()
        p = (STATIC / name).resolve()
        if (p != base and not str(p).startswith(str(base) + os.sep)) or not p.is_file():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype)

    # ------------------------------------------------------------ routes

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path.rstrip("/") or "/"

        if path == "/":
            return self._static("index.html")

        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])

        if path == "/api/config":
            s = self.cfg.summary()
            return self._send(200, {
                "version": VERSION,
                "read_only": s["read_only"], "dry_run": s["dry_run"],
                "audit": bool(s["audit"]),
                "sources": {k: s[k]["ready"] for k in ("radarr", "sonarr")},
            })

        if path == "/api/data":
            try:
                return self._send(200, self.lib.data(force="force" in q))
            except Exception as e:
                return self._send(500, {"error": str(e)})

        if path.startswith("/poster/"):
            bits = path.split("/")
            if len(bits) < 4:
                return self._send(404, {"error": "bad poster path"})
            kind, ident = bits[2], bits[3]
            name = "radarr" if kind == "movie" else "sonarr"
            client = self.lib.client(name)
            if not client:
                return self._send(404, {"error": "source unavailable"})
            try:
                got = client.cover(int(ident), q.get("size", ["poster-500.jpg"])[0])
            except (ValueError, ArrError):
                got = None
            if not got:
                return self._send(404, {"error": "no poster"})
            data, ctype = got
            return self._send(200, data, ctype, {"Cache-Control": "max-age=86400"})

        if path == "/api/profiles":
            c = self.lib.client("radarr")
            if not c:
                return self._send(404, {"error": "radarr not configured"})
            try:
                return self._send(200, {"profiles": c.profiles()})
            except ArrError as e:
                return self._send(502, {"error": str(e)})

        if path == "/api/releases":
            c = self.lib.client("radarr")
            if not c:
                return self._send(404, {"error": "radarr not configured"})
            try:
                mid = int(q.get("id", ["0"])[0])
            except ValueError:
                return self._send(400, {"error": "bad id"})
            try:
                return self._send(200, {"releases": c.releases(mid)})
            except ArrError as e:
                return self._send(502, {"error": str(e)})

        if path == "/api/export":
            fmt = q.get("format", ["csv"])[0]
            kind = q.get("kind", ["movie"])[0]
            try:
                rows = self.lib.data()["movies" if kind == "movie" else "series"]
            except Exception as e:
                return self._send(500, {"error": str(e)})
            ids = {x for x in q.get("ids", [""])[0].split(",") if x}
            if ids:
                rows = [r for r in rows if str(r["id"]) in ids]
            if fmt == "json":
                return self._send(200, rows, "application/json",
                                  {"Content-Disposition": "attachment; filename=cullr.json"})
            cols = ["title", "year", "drive", "size", "quality", "rating", "votes",
                    "studio", "monitored", "added", "path"]
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
            return self._send(200, buf.getvalue(), "text/csv; charset=utf-8",
                              {"Content-Disposition": "attachment; filename=cullr.csv"})

        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0:
                raise ValueError("negative content-length")
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad json"})

        if path == "/api/refresh":
            self.lib.invalidate()
            return self._send(200, {"ok": True})

        if path == "/api/downsize":
            # Swap a movie onto a smaller-target quality profile and (optionally)
            # grab a specific release, so the big file gets replaced rather than
            # simply vanishing. Radarr will not import a downgrade while the old
            # profile still says the existing file meets cutoff, so the profile
            # change has to happen first.
            if self.cfg.read_only:
                return self._send(403, {"error": "server is running in read-only mode"})

            c = self.lib.client("radarr")
            if not c:
                return self._send(404, {"error": "radarr not configured"})

            movie_id = payload.get("id")
            profile_id = payload.get("profileId")
            guid = payload.get("guid")
            indexer_id = payload.get("indexerId")
            drop_first = bool(payload.get("deleteFirst"))

            snapshot = self.lib.data()
            ref = next((r for r in snapshot["movies"] if str(r["id"]) == str(movie_id)), {})
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": "downsize",
                "kind": "movie", "id": movie_id, "title": ref.get("title", ""),
                "year": ref.get("year", 0),
                "fromSize": ref.get("size", 0), "fromQuality": ref.get("quality", ""),
                "toSize": payload.get("size") if isinstance(payload.get("size"), (int, float)) else 0,
                "toRelease": payload.get("release", ""),
                "profileId": profile_id, "deleteFirst": drop_first,
                "dry_run": self.cfg.dry_run,
            }

            if self.cfg.dry_run:
                rec["ok"] = True
                _audit(self.cfg, rec)
                return self._send(200, {"ok": True, "dryRun": True, **rec})

            steps = []
            try:
                if profile_id:
                    c.set_profile(int(movie_id), int(profile_id))
                    steps.append("profile")
                if drop_first:
                    fid = c.movie_file_id(int(movie_id))
                    if fid:
                        c.delete_file(fid)
                        steps.append("file-deleted")
                if guid and indexer_id is not None:
                    c.grab(guid, indexer_id)
                    steps.append("grabbed")
            except (ArrError, ValueError, TypeError) as e:
                rec["ok"] = False
                rec["error"] = str(e)
                rec["steps"] = steps
                _audit(self.cfg, rec)
                return self._send(502, {"ok": False, "error": str(e), "steps": steps})

            rec["ok"] = True
            rec["steps"] = steps
            _audit(self.cfg, rec)
            self.lib.invalidate()
            return self._send(200, {"ok": True, "steps": steps,
                                    "saved": max(0, rec["fromSize"] - rec["toSize"])})

        if path == "/api/delete":
            if self.cfg.read_only:
                return self._send(403, {"error": "server is running in read-only mode"})

            items = payload.get("items") or []
            exclude = bool(payload.get("exclude"))
            delete_files = bool(payload.get("deleteFiles", True))
            if not isinstance(items, list) or not items:
                return self._send(400, {"error": "no items"})

            # Look details up server-side rather than trusting the client to send
            # them, so the audit trail is complete no matter what called us.
            snapshot = self.lib.data()
            known = {}
            for key in ("movies", "series"):
                for row in snapshot[key]:
                    known[(row["kind"], str(row["id"]))] = row

            results = []
            for it in items:
                kind = "movie" if it.get("kind") == "movie" else "series"
                name = "radarr" if kind == "movie" else "sonarr"
                ref = known.get((kind, str(it.get("id")))) or {}
                rec = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "kind": kind, "id": it.get("id"),
                    "title": it.get("title") or ref.get("title", ""),
                    "year": ref.get("year", 0),
                    "size": it.get("size") if isinstance(it.get("size"), (int, float)) and it.get("size")
                            else ref.get("size", 0),
                    "path": it.get("path") or ref.get("path", ""),
                    "quality": ref.get("quality", ""),
                    "deleteFiles": delete_files, "exclude": exclude,
                    "dry_run": self.cfg.dry_run,
                }
                base = {"id": rec["id"], "kind": kind,
                        "title": rec["title"], "size": rec["size"]}

                if self.cfg.dry_run:
                    rec["ok"] = True
                    results.append({**base, "ok": True, "dryRun": True})
                    _audit(self.cfg, rec)
                    continue

                client = self.lib.client(name)
                if not client:
                    rec["ok"] = False
                    rec["error"] = f"{name} not configured"
                    results.append({**base, "ok": False, "error": rec["error"]})
                    _audit(self.cfg, rec)
                    continue

                try:
                    code = client.delete(kind, int(it["id"]), delete_files, exclude)
                    ok = code in (200, 202, 204)
                    rec["ok"] = ok
                    rec["code"] = code
                    results.append({**base, "ok": ok, "code": code})
                except (ArrError, KeyError, ValueError) as e:
                    rec["ok"] = False
                    rec["error"] = str(e)
                    results.append({**base, "ok": False, "error": str(e)})
                _audit(self.cfg, rec)

            self.lib.invalidate()
            return self._send(200, {
                "results": results,
                "ok": sum(1 for r in results if r["ok"]),
                "failed": sum(1 for r in results if not r["ok"]),
                "freed": sum(r.get("size", 0) for r in results if r.get("ok")),
                "dryRun": self.cfg.dry_run,
            })

        return self._send(404, {"error": "not found"})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(cfg: Config) -> None:
    lib = Library(cfg)
    handler = type("BoundHandler", (Handler,), {"cfg": cfg, "lib": lib})

    with Server((cfg.host, cfg.port), handler) as httpd:
        url = f"http://{'127.0.0.1' if cfg.host in ('0.0.0.0', '') else cfg.host}:{cfg.port}"
        print(f"  listening on {url}")
        if cfg.open_browser:
            threading.Timer(0.6, __import__("webbrowser").open, args=(url,)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  stopped")
