"""Media-Hoarder as a cullr source.

Media-Hoarder (https://github.com/theMK2k/Media-Hoarder) is a desktop app that
scans media directories and keeps everything it learns in one SQLite file. It
has no HTTP API and no delete function of its own, so this module differs from
the *arr clients in two ways:

    * reading is a read-only SQLite connection, not an HTTP request
    * deleting removes the file from disk directly, because there is no service
      to ask

That second point is why deletion here is off unless it is explicitly turned on
with --mh-allow-delete. Removing a file this way has no *arr bookkeeping behind
it, a UNC path has no recycle bin, and Media-Hoarder's own database keeps the
row until you rescan.

The database is opened read-only and never written to. Media-Hoarder may be
running at the same time.
"""

from __future__ import annotations

import os
import platform
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

DB_NAME = "media-hoarder.db"

#: Where the installed and portable builds keep their data directory.
_DB_DIRS = {
    "Windows": [
        r"{localappdata}\Programs\media-hoarder\resources\data",
        r"{appdata}\media-hoarder\resources\data",
        r"{localappdata}\media-hoarder\resources\data",
    ],
    "Darwin": [
        "/Applications/Media Hoarder.app/Contents/Resources/data",
        "~/Applications/Media Hoarder.app/Contents/Resources/data",
        "~/Library/Application Support/media-hoarder/resources/data",
    ],
    "Linux": [
        "~/.config/media-hoarder/resources/data",
        "/opt/media-hoarder/resources/data",
        "/usr/lib/media-hoarder/resources/data",
    ],
}


class MHError(Exception):
    """Anything that went wrong reading or deleting through this source."""


def _candidate_dirs() -> list[Path]:
    out: list[Path] = []
    for tpl in _DB_DIRS.get(platform.system(), _DB_DIRS["Linux"]):
        p = tpl.format(
            localappdata=os.environ.get("LOCALAPPDATA", ""),
            appdata=os.environ.get("APPDATA", ""),
        )
        if "{" in p or p.strip() in ("", "\\", "/"):
            continue
        out.append(Path(p).expanduser())
    # A portable build keeps its data next to the executable, so also try the
    # working directory.
    out.append(Path.cwd() / "resources" / "data")
    return out


def discover() -> Optional[str]:
    """Return the path to a local media-hoarder.db, or None."""
    for d in _candidate_dirs():
        db = d / DB_NAME
        try:
            if db.is_file():
                return str(db)
        except OSError:
            continue
    return None


# ------------------------------------------------------------------ shaping


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    return float(xs[n // 2]) if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _is_unc(p: str) -> bool:
    return p.startswith("\\\\") or p.startswith("//")


def _drive_of(source_path: str) -> str:
    """Short label for the volume a file sits on.

    A drive letter on Windows. For a UNC source the share name on its own,
    because the full \\\\host\\share form is far too wide for a drive chip. The
    full root travels alongside it as "root" so free space can still be read.
    """
    p = (source_path or "").strip()
    if not p:
        return "?"
    if len(p) > 1 and p[1] == ":":
        return p[0].upper()
    parts = [x for x in p.replace("\\", "/").split("/") if x]
    if _is_unc(p):                                        # \\host\share
        return parts[1] if len(parts) > 1 else (parts[0] if parts else "?")
    return "/" + parts[0] if parts else "/"


def _labels_for(srcs: dict) -> dict:
    r"""Chip label per source path id, kept unique.

    A share name alone is the readable label, but two hosts can both export
    "media". When that happens the colliding ones fall back to host\share, so
    each chip still measures its own volume instead of silently reporting the
    first one's free space for both.
    """
    roots = {i: _root_of(p) for i, p in srcs.items()}
    short = {i: _drive_of(p) for i, p in srcs.items()}
    clash = set()
    seen: dict = {}
    for i, name in short.items():
        if name in seen and roots[seen[name]] != roots[i]:
            clash.add(name)
        seen.setdefault(name, i)
    out = {}
    for i, name in short.items():
        if name not in clash:
            out[i] = name
            continue
        r = roots[i]
        parts = [x for x in r.replace("\\", "/").split("/") if x]
        out[i] = "\\".join(parts[-2:]) if len(parts) > 1 else name
    return out


def _root_of(source_path: str) -> str:
    """The mount point to measure free space against."""
    p = (source_path or "").strip()
    if not p:
        return ""
    if len(p) > 1 and p[1] == ":":
        return p[:2] + "\\"
    parts = [x for x in p.replace("\\", "/").split("/") if x]
    if _is_unc(p):
        return "\\\\" + "\\".join(parts[:2]) if len(parts) > 1 else p
    return p


def _first_segment(rel: str) -> str:
    """The leading directory of a relative path, whichever separator it uses."""
    parts = [x for x in re.split(r"[\\/]", rel or "") if x]
    return parts[0] if parts else ""


def _has_poster(rel: Optional[str], have: set) -> bool:
    """True when the recorded poster is actually on disk."""
    if not rel:
        return False
    return rel.replace("\\", "/").rsplit("/", 1)[-1] in have


def _join(source_path: str, relative: str) -> str:
    """Rebuild the absolute path Media-Hoarder recorded, keeping its separators."""
    base = (source_path or "").rstrip("\\/")
    rel = (relative or "").lstrip("\\/")
    if not base:
        return rel
    sep = "\\" if ("\\" in base or (len(base) > 1 and base[1] == ":")) else "/"
    return f"{base}{sep}{rel}"


class MediaHoarder:
    """Read-only view of a Media-Hoarder library, with an opt-in file deleter."""

    def __init__(self, db_path: str, allow_delete: bool = False):
        self.db_path = str(db_path)
        self.allow_delete = bool(allow_delete)
        self.dir = Path(self.db_path).parent

    # ------------------------------------------------------------ reading

    def _connect(self) -> sqlite3.Connection:
        # as_uri() percent-encodes, which matters because SQLite ends the
        # filename at the first '#' or '?'. A user directory like "bob#1" would
        # otherwise open a different path read-write instead of this one
        # read-only.
        try:
            uri = Path(self.db_path).resolve().as_uri() + "?mode=ro"
        except (OSError, ValueError) as e:
            raise MHError(f"cannot open {self.db_path}: {e}") from e
        try:
            c = sqlite3.connect(uri, uri=True, timeout=10)
        except sqlite3.Error as e:
            raise MHError(f"cannot open {self.db_path}: {e}") from e
        c.row_factory = sqlite3.Row
        return c

    def ping(self) -> dict:
        """Confirm the file is a Media-Hoarder database and report its size."""
        with self._connect() as c:
            try:
                movies = c.execute("select count(*) from tbl_Movies").fetchone()[0]
                paths = c.execute("select count(*) from tbl_SourcePaths").fetchone()[0]
            except sqlite3.Error as e:
                raise MHError(f"{self.db_path} is not a Media-Hoarder database: {e}") from e
        return {"rows": movies, "sourcePaths": paths, "path": self.db_path}

    def source_paths(self) -> dict[int, str]:
        with self._connect() as c:
            try:
                return {r["id_SourcePaths"]: r["Path"]
                        for r in c.execute("select id_SourcePaths, Path from tbl_SourcePaths")}
            except sqlite3.Error as e:
                raise MHError(f"reading source paths failed: {e}") from e

    def _genres(self, c: sqlite3.Connection) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        try:
            rows = c.execute(
                "select mg.id_Movies id, g.Name name from tbl_Movies_Genres mg "
                "join tbl_Genres g on g.id_Genres = mg.id_Genres"
            )
            for r in rows:
                out.setdefault(r["id"], []).append(r["name"])
        except sqlite3.Error:
            return {}
        return out

    def _codecs(self, c: sqlite3.Connection) -> dict[int, str]:
        """Video codec per movie, taken from the first video track."""
        out: dict[int, str] = {}
        try:
            rows = c.execute(
                "select id_Movies id, Format f from tbl_Movies_MI_Tracks "
                "where lower(type) = 'video' and Format is not null"
            )
        except sqlite3.Error:
            return out
        try:
            for r in rows:
                out.setdefault(r["id"], r["f"] or "")
        except sqlite3.Error:
            return {}
        return out

    def _cached_posters(self) -> set[str]:
        """Filenames actually present in the extras directory.

        Media-Hoarder records a poster path for almost every title but only
        writes the file once something has asked for it, so most of those paths
        point at nothing. Listing the directory once lets each item say up front
        whether it has an image, instead of the grid firing thousands of
        requests that all come back 404.
        """
        try:
            return {p.name for p in (self.dir / "extras").iterdir() if p.is_file()}
        except OSError:
            return set()

    def _read_all(self) -> tuple[dict, dict, dict, list[dict]]:
        """Pull everything items() needs in one connection.

        Every sqlite failure becomes an MHError here. Schema drift in an older
        or damaged database must not escape as a raw sqlite3.Error, because that
        would take the whole library down with it, Radarr and Sonarr included.
        """
        with self._connect() as c:
            try:
                srcs = {r["id_SourcePaths"]: r["Path"] for r in
                        c.execute("select id_SourcePaths, Path from tbl_SourcePaths")}
                genres = self._genres(c)
                codecs = self._codecs(c)
                rows = [dict(r) for r in c.execute(
                    "select id_Movies, id_SourcePaths, RelativePath, Filename, Size, Name, Name2,"
                    " IMDB_tconst, IMDB_startYear, IMDB_runtimeMinutes, IMDB_rating, IMDB_numVotes,"
                    " IMDB_plotSummary, IMDB_posterSmall_URL, MI_Quality, MI_Duration_Seconds,"
                    " Series_id_Movies_Owner, Extra_id_Movies_Owner, isRemoved, created_at"
                    " from tbl_Movies"
                )]
            except sqlite3.Error as e:
                raise MHError(f"reading the Media-Hoarder database failed: {e}") from e
        return srcs, genres, codecs, rows

    def items(self) -> dict[str, list[dict]]:
        """Return {"mh": [movies], "mhseries": [series]} in cullr's item shape."""
        have = self._cached_posters()
        srcs, genres, codecs, rows = self._read_all()
        labels = _labels_for(srcs)

        # The row that owns a series carries the title but no file and no size,
        # so it has to survive the size filter to be used as a parent below.
        live = [r for r in rows if not r.get("isRemoved")]
        sized = [r for r in live if (r["Size"] or 0) > 0]
        movies = [r for r in sized
                  if r["Series_id_Movies_Owner"] is None and r["Extra_id_Movies_Owner"] is None]
        episodes = [r for r in sized if r["Series_id_Movies_Owner"] is not None]

        tiers: dict[str, list[int]] = {}
        for r in movies:
            tiers.setdefault(r["MI_Quality"] or "Unknown", []).append(r["Size"])
        med = {q: _median(v) for q, v in tiers.items()}

        out_movies = [self._shape(r, srcs, labels, genres, codecs, med, have) for r in movies]
        out_movies.sort(key=lambda x: -x["size"])

        by_owner: dict[int, list[dict]] = {}
        for r in episodes:
            by_owner.setdefault(r["Series_id_Movies_Owner"], []).append(r)
        parents = {r["id_Movies"]: r for r in rows}
        out_series = [self._shape_series(oid, eps, parents.get(oid), srcs, labels, genres, have)
                      for oid, eps in by_owner.items()]
        out_series.sort(key=lambda x: -x["size"])

        return {"mh": out_movies, "mhseries": out_series}

    def _shape(self, r: dict, srcs: dict, labels: dict, genres: dict, codecs: dict,
               med: dict, have: set) -> dict:
        src = srcs.get(r["id_SourcePaths"], "")
        size = r["Size"] or 0
        q = r["MI_Quality"] or "Unknown"
        runtime = r["IMDB_runtimeMinutes"] or round((r["MI_Duration_Seconds"] or 0) / 60) or 0
        return {
            "id": r["id_Movies"], "kind": "mh",
            "title": r["Name"] or r["Name2"] or r["Filename"] or "",
            "year": r["IMDB_startYear"] or 0,
            "drive": labels.get(r["id_SourcePaths"], _drive_of(src)), "root": _root_of(src),
            "size": size, "quality": q, "runtime": runtime,
            "rating": round(r["IMDB_rating"] or 0, 1), "votes": r["IMDB_numVotes"] or 0,
            "codec": codecs.get(r["id_Movies"], ""), "hdr": "",
            "monitored": True,
            "bloat": round(size / med[q], 2) if med.get(q) else 0,
            "gph": round(size / 2**30 / (runtime / 60), 2) if runtime else 0,
            "added": (r["created_at"] or "")[:10],
            "genres": genres.get(r["id_Movies"], []), "studio": "",
            "overview": r["IMDB_plotSummary"] or "", "cert": "", "lang": "",
            "tmdbId": 0, "imdbId": r["IMDB_tconst"] or "",
            "poster": _has_poster(r["IMDB_posterSmall_URL"], have),
            "path": _join(src, r["RelativePath"] or r["Filename"] or ""),
        }

    def _shape_series(self, oid: int, eps: list[dict], parent: Optional[dict],
                      srcs: dict, labels: dict, genres: dict, have: set) -> dict:
        size = sum(e["Size"] or 0 for e in eps)
        secs = sum(e["MI_Duration_Seconds"] or 0 for e in eps)
        runtime = round(secs / 60)
        p = parent or {}
        src = srcs.get(p.get("id_SourcePaths") or eps[0]["id_SourcePaths"], "")
        seasons = {_first_segment(e["RelativePath"]) for e in eps if e["RelativePath"]}
        return {
            "id": oid, "kind": "mhseries",
            "title": p.get("Name") or p.get("Name2") or "(series)",
            "year": p.get("IMDB_startYear") or 0,
            "drive": labels.get(p.get("id_SourcePaths") or eps[0]["id_SourcePaths"],
                                _drive_of(src)), "root": _root_of(src),
            "size": size, "quality": f"{len(eps)} eps", "runtime": runtime,
            "rating": round(p.get("IMDB_rating") or 0, 1), "votes": p.get("IMDB_numVotes") or 0,
            "codec": "", "hdr": "", "monitored": True, "bloat": 0,
            "gph": round(size / 2**30 / (runtime / 60), 2) if runtime else 0,
            "added": (p.get("created_at") or "")[:10],
            "genres": genres.get(oid, []), "studio": "",
            "overview": p.get("IMDB_plotSummary") or "", "cert": "", "lang": "",
            "tmdbId": 0, "imdbId": p.get("IMDB_tconst") or "",
            "poster": _has_poster(p.get("IMDB_posterSmall_URL"), have),
            "seasons": len(seasons), "episodes": len(eps),
            "status": "",
            "path": _join(src, _first_segment(p.get("RelativePath") or "")),
            "files": [_join(srcs.get(e["id_SourcePaths"], ""),
                            e["RelativePath"] or e["Filename"] or "") for e in eps],
        }

    # ------------------------------------------------------------ posters

    def poster(self, item_id: int) -> Optional[tuple[bytes, str]]:
        """Return (bytes, content-type) for a locally cached poster, or None."""
        with self._connect() as c:
            try:
                row = c.execute(
                    "select IMDB_posterSmall_URL p from tbl_Movies where id_Movies = ?",
                    (int(item_id),)).fetchone()
            except sqlite3.Error:
                return None
        rel = (row["p"] if row else None) or ""
        if not rel:
            return None
        try:
            base = self.dir.resolve()
            f = (base / rel).resolve()
            # The value comes from the database, so keep it inside the data
            # directory rather than trusting it to be a tame relative path.
            if f != base and not str(f).startswith(str(base) + os.sep):
                return None
            if not f.is_file():
                return None
            data = f.read_bytes()
        except OSError:
            return None
        ext = f.suffix.lower()
        ctype = "image/png" if ext == ".png" else "image/jpeg"
        return data, ctype

    # ------------------------------------------------------------ deleting

    def _guard(self, path: str, roots: Iterable[str]) -> Path:
        """Resolve path and refuse anything outside a Media-Hoarder source path."""
        if not self.allow_delete:
            raise MHError("Media-Hoarder deletion is disabled; "
                          "start cullr with --mh-allow-delete to enable it")
        if not path:
            raise MHError("no path recorded for this item")
        # Resolve first. Comparing the raw string would let a ".." segment or a
        # symlinked season directory match a source path here and then unlink
        # something else entirely.
        try:
            target = Path(path).resolve()
        except OSError as e:
            raise MHError(f"cannot resolve {path}: {e}") from e
        t = str(target)
        for root in roots:
            if not root:
                continue
            try:
                r = str(Path(root).resolve()).rstrip("\\/")
            except OSError:
                continue
            if t == r or t.startswith(r + os.sep):
                break
        else:
            raise MHError(f"refusing to delete outside a Media-Hoarder source path: {path}")
        if not target.is_file():
            raise MHError(f"not a file (already gone?): {path}")
        return target

    def delete_file(self, path: str) -> int:
        """Delete one media file. Returns the bytes reclaimed."""
        roots = list(self.source_paths().values())
        target = self._guard(path, roots)
        size = target.stat().st_size
        try:
            target.unlink()
        except OSError as e:
            raise MHError(f"could not delete {path}: {e}") from e
        return size

    def delete_files(self, paths: Iterable[str]) -> tuple[int, list[str]]:
        """Delete several files. Returns (bytes reclaimed, error strings)."""
        roots = list(self.source_paths().values())
        freed, errors = 0, []
        for p in paths:
            try:
                target = self._guard(p, roots)
                freed += target.stat().st_size
                target.unlink()
            except (MHError, OSError) as e:
                errors.append(str(e))
        return freed, errors
