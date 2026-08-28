"""Thin *arr (Radarr / Sonarr) API client and library normalisation.

Everything the UI needs is flattened into one record shape so movies and series
can share a grid, sort and filter path.
"""

from __future__ import annotations

import json
import shutil
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import Config, Instance


class ArrError(RuntimeError):
    pass


class Arr:
    """Minimal client for a single Radarr/Sonarr instance."""

    def __init__(self, inst: Instance, timeout: int = 300):
        self.inst = inst
        self.timeout = timeout

    # ---------------------------------------------------------- transport

    def request(self, path: str, method: str = "GET", timeout: Optional[int] = None):
        url = self.inst.url.rstrip("/") + path
        req = urllib.request.Request(
            url,
            headers={"X-Api-Key": self.inst.key, "Accept": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                body = r.read()
                if not body:
                    return r.status, None
                try:
                    return r.status, json.loads(body)
                except json.JSONDecodeError:
                    return r.status, body
        except urllib.error.HTTPError as e:
            raise ArrError(f"{self.inst.name} {method} {path} -> HTTP {e.code} {e.reason}") from e
        except urllib.error.URLError as e:
            raise ArrError(f"{self.inst.name} unreachable at {self.inst.url}: {e.reason}") from e

    def ping(self) -> dict:
        _, data = self.request("/api/v3/system/status", timeout=20)
        return data or {}

    def cover(self, item_id: int, size: str = "poster-500.jpg") -> Optional[tuple[bytes, str]]:
        url = f"{self.inst.url.rstrip('/')}/api/v3/mediacover/{int(item_id)}/{size}"
        req = urllib.request.Request(url, headers={"X-Api-Key": self.inst.key})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(), r.headers.get("Content-Type", "image/jpeg")
        except Exception:
            return None

    # ------------------------------------------------------- downsizing

    def profiles(self) -> list[dict]:
        """Quality profiles, with the qualities each one actually allows."""
        _, data = self.request("/api/v3/qualityprofile", timeout=60)
        out = []
        for p in data or []:
            names = []
            for item in p.get("items", []):
                if not item.get("allowed"):
                    continue
                if item.get("quality"):
                    names.append(item["quality"]["name"])
                elif item.get("name"):
                    names.append(item["name"])
            out.append({
                "id": p["id"], "name": p.get("name", ""),
                "allowed": names,
                "upgradeAllowed": bool(p.get("upgradeAllowed")),
            })
        return out

    def releases(self, movie_id: int) -> list[dict]:
        """Interactive search. Returns every candidate, rejected ones included."""
        _, data = self.request(f"/api/v3/release?movieId={int(movie_id)}", timeout=240)
        out = []
        for r in data or []:
            q = ((r.get("quality") or {}).get("quality") or {})
            out.append({
                "guid": r.get("guid"), "indexerId": r.get("indexerId"),
                "indexer": r.get("indexer", ""), "title": r.get("title", ""),
                "size": r.get("size", 0) or 0,
                "quality": q.get("name", "?"),
                "resolution": q.get("resolution", 0),
                "source": q.get("source", ""),
                "age": r.get("ageDays", r.get("age", 0)),
                "protocol": r.get("protocol", ""),
                "releaseGroup": r.get("releaseGroup") or "",
                "languages": [l.get("name", "") for l in (r.get("languages") or [])],
                "approved": bool(r.get("approved")),
                "downloadAllowed": bool(r.get("downloadAllowed")),
                "rejections": [str(x) for x in (r.get("rejections") or [])],
                "customFormatScore": r.get("customFormatScore", 0),
            })
        out.sort(key=lambda x: x["size"])
        return out

    def set_profile(self, movie_id: int, profile_id: int) -> int:
        """Re-point a movie at a different quality profile (PUT the whole object)."""
        _, movie = self.request(f"/api/v3/movie/{int(movie_id)}", timeout=60)
        if not isinstance(movie, dict):
            raise ArrError(f"movie {movie_id} not found")
        movie["qualityProfileId"] = int(profile_id)
        body = json.dumps(movie).encode("utf-8")
        req = urllib.request.Request(
            self.inst.url.rstrip("/") + f"/api/v3/movie/{int(movie_id)}",
            data=body, method="PUT",
            headers={"X-Api-Key": self.inst.key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status
        except urllib.error.HTTPError as e:
            raise ArrError(f"set profile {profile_id} on movie {movie_id}: "
                           f"HTTP {e.code} {e.reason}") from e
        except urllib.error.URLError as e:
            raise ArrError(f"set profile {profile_id} on movie {movie_id}: "
                           f"unreachable at {self.inst.url}: {e.reason}") from e

    def grab(self, guid: str, indexer_id: int) -> int:
        """Push a specific release to the download client."""
        body = json.dumps({"guid": guid, "indexerId": int(indexer_id)}).encode("utf-8")
        req = urllib.request.Request(
            self.inst.url.rstrip("/") + "/api/v3/release",
            data=body, method="POST",
            headers={"X-Api-Key": self.inst.key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status
        except urllib.error.HTTPError as e:
            raise ArrError(f"grab failed: HTTP {e.code} {e.reason}") from e
        except urllib.error.URLError as e:
            raise ArrError(f"grab failed: unreachable at {self.inst.url}: {e.reason}") from e

    def delete_file(self, movie_file_id: int) -> int:
        status, _ = self.request(f"/api/v3/moviefile/{int(movie_file_id)}",
                                 method="DELETE", timeout=120)
        return status

    def movie_file_id(self, movie_id: int) -> Optional[int]:
        _, m = self.request(f"/api/v3/movie/{int(movie_id)}", timeout=60)
        return (m or {}).get("movieFileId") or None

    def delete(self, kind: str, item_id: int, delete_files: bool, exclude: bool) -> int:
        seg = "movie" if kind == "movie" else "series"
        q = urllib.parse.urlencode({
            "deleteFiles": str(bool(delete_files)).lower(),
            ("addImportExclusion" if seg == "movie" else "addImportListExclusion"):
                str(bool(exclude)).lower(),
        })
        status, _ = self.request(f"/api/v3/{seg}/{int(item_id)}?{q}", method="DELETE", timeout=180)
        return status


# ------------------------------------------------------------------ shaping


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    return float(xs[n // 2]) if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _quality_of(mf: dict) -> str:
    return (((mf.get("quality") or {}).get("quality") or {}).get("name")) or "Unknown"


def _drive_of(path: str) -> str:
    if not path:
        return "?"
    p = str(path)
    if len(p) > 1 and p[1] == ":":
        return p[0].upper()
    parts = [x for x in p.replace("\\", "/").split("/") if x]
    return "/" + parts[0] if parts else "/"


def shape_movies(raw: Iterable[dict]) -> list[dict]:
    have = [m for m in raw if m.get("hasFile")]
    tiers: dict[str, list[int]] = {}
    for m in have:
        tiers.setdefault(_quality_of(m.get("movieFile") or {}), []).append(m.get("sizeOnDisk", 0))
    med = {q: _median(v) for q, v in tiers.items()}

    out = []
    for m in have:
        mf = m.get("movieFile") or {}
        mi = mf.get("mediaInfo") or {}
        q = _quality_of(mf)
        size = m.get("sizeOnDisk", 0) or 0
        runtime = m.get("runtime") or 0
        imdb = (m.get("ratings") or {}).get("imdb") or {}
        out.append({
            "id": m["id"], "kind": "movie",
            "title": m.get("title", ""), "year": m.get("year") or 0,
            "drive": _drive_of(m.get("path") or ""),
            "size": size, "quality": q, "runtime": runtime,
            "rating": round(imdb.get("value") or 0, 1), "votes": imdb.get("votes") or 0,
            "codec": mi.get("videoCodec") or "", "hdr": mi.get("videoDynamicRangeType") or "",
            "monitored": bool(m.get("monitored")),
            "bloat": round(size / med[q], 2) if med.get(q) else 0,
            "gph": round(size / 2**30 / (runtime / 60), 2) if runtime else 0,
            "added": (mf.get("dateAdded") or "")[:10],
            "genres": m.get("genres") or [], "studio": m.get("studio") or "",
            "overview": m.get("overview") or "", "cert": m.get("certification") or "",
            "lang": (m.get("originalLanguage") or {}).get("name", ""),
            "tmdbId": m.get("tmdbId") or 0, "imdbId": m.get("imdbId") or "",
            "path": mf.get("path") or m.get("path", ""),
        })
    out.sort(key=lambda x: -x["size"])
    return out


def shape_series(raw: Iterable[dict]) -> list[dict]:
    out = []
    for s in raw:
        st = s.get("statistics") or {}
        size = st.get("sizeOnDisk", 0) or 0
        if not size:
            continue
        eps = st.get("episodeFileCount", 0) or 0
        runtime = (s.get("runtime") or 0) * max(eps, 1)
        r = s.get("ratings") or {}
        out.append({
            "id": s["id"], "kind": "series",
            "title": s.get("title", ""), "year": s.get("year") or 0,
            "drive": _drive_of(s.get("path") or ""),
            "size": size, "quality": f"{eps} eps", "runtime": runtime,
            "rating": round(r.get("value") or 0, 1), "votes": r.get("votes") or 0,
            "codec": "", "hdr": "",
            "monitored": bool(s.get("monitored")),
            "bloat": 0,
            "gph": round(size / 2**30 / (runtime / 60), 2) if runtime else 0,
            "added": (s.get("added") or "")[:10],
            "genres": s.get("genres") or [], "studio": s.get("network") or "",
            "overview": s.get("overview") or "", "cert": s.get("certification") or "",
            "lang": "", "tmdbId": s.get("tvdbId") or 0, "imdbId": s.get("imdbId") or "",
            "seasons": len(s.get("seasons") or []), "episodes": eps,
            "status": s.get("status") or "",
            "path": s.get("path", ""),
        })
    out.sort(key=lambda x: -x["size"])
    return out


# ------------------------------------------------------------------ library


class Library:
    """Caches the shaped library and reports disk usage for its roots."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._data: dict[str, list[dict]] = {"movie": [], "series": []}
        self._errors: dict[str, str] = {}
        self._at = 0.0

    def client(self, name: str) -> Optional[Arr]:
        inst = self.cfg.get(name)
        return Arr(inst, self.cfg.timeout) if inst else None

    def _fetch(self) -> None:
        self._errors = {}
        for name, path, shaper, key in (
            ("radarr", "/api/v3/movie", shape_movies, "movie"),
            ("sonarr", "/api/v3/series", shape_series, "series"),
        ):
            c = self.client(name)
            if not c:
                self._data[key] = []
                continue
            try:
                _, raw = c.request(path)
                self._data[key] = shaper(raw or [])
            except ArrError as e:
                self._errors[name] = str(e)
                self._data[key] = []
        self._at = time.time()

    def data(self, force: bool = False) -> dict:
        with self._lock:
            stale = (time.time() - self._at) > self.cfg.cache_ttl
            if force or stale or not self._at:
                self._fetch()
            return {
                "movies": self._data["movie"],
                "series": self._data["series"],
                "disks": self.disks(),
                "errors": self._errors,
                "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

    def invalidate(self) -> None:
        with self._lock:
            self._at = 0.0

    # ------------------------------------------------------------- disks

    def _roots(self) -> list[str]:
        if self.cfg.drives:
            return list(self.cfg.drives)
        seen: set[str] = set()
        for items in self._data.values():
            for it in items:
                d = it.get("drive") or ""
                if d and d not in seen:
                    seen.add(d)
        if seen and all(len(s) == 1 for s in seen):        # windows letters
            return sorted(f"{s}:\\" for s in seen)
        roots = sorted(seen)
        return roots or ["/"]

    def disks(self) -> dict:
        out: dict[str, dict] = {}
        for root in self._roots():
            label = root[0].upper() if len(root) > 1 and root[1] == ":" else root
            try:
                usage = shutil.disk_usage(root)
            except OSError:
                continue
            out[label] = {"free": usage.free, "total": usage.total, "used": usage.used}
        return out
