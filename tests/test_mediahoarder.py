"""Regression tests for the Media-Hoarder source.

Two things here are worth protecting. The path helpers have to survive the
mixed separators Media-Hoarder actually records, and chip labels have to stay
unique when two hosts export the same share name. Separately, the database is
opened through a percent-encoded URI so a '#' in the path cannot silently point
SQLite at some other file and open it read-write.

Everything runs against temporary directories. Nothing touches a real library.
"""

import gc
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cullr.mediahoarder import (
    MediaHoarder,
    MHError,
    _drive_of,
    _first_segment,
    _join,
    _labels_for,
    _root_of,
)

UNC_A = "\\\\hostA\\media"
UNC_B = "\\\\hostB\\media"


def _make_db(path, sources=None):
    """A minimal database with the two tables this module reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if sources is None:
        sources = [str(path.parent)]
    con = sqlite3.connect(str(path))
    try:
        con.execute("create table tbl_SourcePaths "
                    "(id_SourcePaths integer primary key, Path text)")
        con.execute("create table tbl_Movies (id_Movies integer primary key)")
        for i, src in enumerate(sources, start=1):
            con.execute("insert into tbl_SourcePaths (id_SourcePaths, Path) "
                        "values (?, ?)", (i, src))
        con.commit()
    finally:
        con.close()


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


class PathShapingTests(unittest.TestCase):
    """Pure functions, so these cases are deterministic on every platform."""

    def test_drive_of_shortens_letters_shares_and_posix_roots(self):
        self.assertEqual(_drive_of("C:\\Media"), "C")
        self.assertEqual(_drive_of("d:/media"), "D")
        self.assertEqual(_drive_of(UNC_A), "media")
        self.assertEqual(_drive_of("//hostA/media"), "media")
        self.assertEqual(_drive_of("/mnt/media"), "/mnt")
        self.assertEqual(_drive_of(""), "?")

    def test_root_of_normalises_either_separator_to_one_share_root(self):
        self.assertEqual(_root_of("C:\\Media\\Films"), "C:\\")
        self.assertEqual(_root_of("\\\\hostA\\media\\tv"), UNC_A)
        # The same volume written with forward slashes has to land on the same
        # root, otherwise free space gets measured twice.
        self.assertEqual(_root_of("//hostA/media/tv"), UNC_A)
        self.assertEqual(_root_of("/mnt/media"), "/mnt/media")
        self.assertEqual(_root_of(""), "")

    def test_colliding_share_names_get_distinct_labels(self):
        labels = _labels_for({1: UNC_A, 2: UNC_B})
        self.assertEqual(labels[1], "hostA\\media")
        self.assertEqual(labels[2], "hostB\\media")
        self.assertNotEqual(labels[1], labels[2])

    def test_one_volume_written_two_ways_keeps_the_short_label(self):
        labels = _labels_for({1: UNC_A, 2: "//hostA/media/tv"})
        self.assertEqual(labels, {1: "media", 2: "media"})

    def test_distinct_share_names_are_left_short(self):
        labels = _labels_for({1: "C:\\Media", 2: UNC_B})
        self.assertEqual(labels, {1: "C", 2: "media"})

    def test_first_segment_accepts_either_separator(self):
        self.assertEqual(_first_segment("Season 01\\ep.mkv"), "Season 01")
        self.assertEqual(_first_segment("Season 01/ep.mkv"), "Season 01")
        self.assertEqual(_first_segment("/Season 01/ep.mkv"), "Season 01")
        self.assertEqual(_first_segment(""), "")
        self.assertEqual(_first_segment(None), "")

    def test_join_picks_its_separator_from_the_source_path(self):
        self.assertEqual(_join("C:\\Media", "Show\\ep.mkv"), "C:\\Media\\Show\\ep.mkv")
        self.assertEqual(_join("/mnt/media", "Show/ep.mkv"), "/mnt/media/Show/ep.mkv")
        self.assertEqual(_join("C:", "ep.mkv"), "C:\\ep.mkv")
        self.assertEqual(_join("", "Show/ep.mkv"), "Show/ep.mkv")

    def test_join_strips_a_leading_separator_and_leaves_the_rest_alone(self):
        # Only the seam is rebuilt. Whatever separators Media-Hoarder recorded
        # inside the relative path travel through untouched.
        self.assertEqual(_join(UNC_A, "/Show/ep.mkv"), "\\\\hostA\\media\\Show/ep.mkv")
        self.assertEqual(_join(UNC_A, "\\Show\\ep.mkv"), "\\\\hostA\\media\\Show\\ep.mkv")


class ReadOnlyConnectionTests(unittest.TestCase):
    """A '#' in the path must not cost the read-only guarantee.

    SQLite ends a URI filename at the first '#', so an unencoded URI would drop
    "?mode=ro" with it and open a truncated path read-write instead.
    """

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.db = self.tmp / "bob#1" / "media#2-hoarder.db"
        _make_db(self.db)
        self.mh = MediaHoarder(str(self.db))

    def test_reads_succeed_against_the_intended_file(self):
        con = self.mh._connect()
        try:
            self.assertEqual(con.execute("select count(*) from tbl_SourcePaths")
                             .fetchone()[0], 1)
            opened = con.execute("pragma database_list").fetchall()
        finally:
            con.close()
        self.assertEqual(Path(opened[0][2]).resolve(), self.db.resolve())

    def test_writes_are_refused(self):
        con = self.mh._connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("insert into tbl_SourcePaths (Path) values ('x')")
        finally:
            con.close()

    def test_opening_creates_no_second_file(self):
        before = _tree(self.tmp)
        con = self.mh._connect()
        con.close()
        # A truncated filename would have been created next to "bob#1".
        self.assertEqual(_tree(self.tmp), before)


def _live_connections_to(db):
    """Open sqlite connections still pointing at `db`.

    A closed connection raises ProgrammingError, so anything that answers
    "pragma database_list" is still holding the file.
    """
    want = Path(db).resolve()
    out = []
    for obj in gc.get_objects():
        if not isinstance(obj, sqlite3.Connection):
            continue
        try:
            rows = obj.execute("pragma database_list").fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            if row[2] and Path(row[2]).resolve() == want:
                out.append(obj)
                break
    return out


class ConnectionLifetimeTests(unittest.TestCase):
    """Readers must let go of the database when they are done with it.

    The module opens Media-Hoarder's live database while Media-Hoarder may be
    running, so a reader that returns while still holding the file is not a
    tidy guest. Note there is no gc.collect() below on purpose: releasing a
    handle only when the cyclic collector happens to run is the defect, not the
    fix for it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.db = self.tmp / "media-hoarder.db"
        _make_db(self.db)

    def test_explicit_close_releases_the_handle(self):
        con = MediaHoarder(str(self.db))._connect()
        con.close()
        self.assertEqual(_live_connections_to(self.db), [])

    def test_source_paths_releases_the_handle(self):
        mh = MediaHoarder(str(self.db))
        mh.source_paths()
        self.assertEqual(_live_connections_to(self.db), [],
                         "source_paths() returned while still holding the database")


class DeletionContainmentTests(unittest.TestCase):
    """Deleting here unlinks a real file, so containment is the safety layer.

    Every path below lives under a temporary directory. The guard resolves
    before comparing, which is what stops a "..' segment or a symlinked season
    directory from matching a source path and then unlinking something else.
    """

    def setUp(self):
        # Tolerant cleanup: the reader's lingering handle is ConnectionLifetime's
        # subject, and it must not show up as noise on the containment results.
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.root = self.tmp / "media"
        self.root.mkdir()
        self.inside = self.root / "ep.mkv"
        self.inside.write_bytes(b"inside")

        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.stranger = self.outside / "ep.mkv"
        self.stranger.write_bytes(b"do not touch")

        self.db = self.tmp / "data" / "media-hoarder.db"
        _make_db(self.db, sources=[str(self.root)])
        self.mh = MediaHoarder(str(self.db), allow_delete=True)

    def test_a_file_inside_a_source_path_is_allowed(self):
        self.assertEqual(self.mh._guard(str(self.inside), [str(self.root)]),
                         self.inside.resolve())

    def test_deletion_is_refused_until_it_is_turned_on(self):
        off = MediaHoarder(str(self.db))
        with self.assertRaisesRegex(MHError, "disabled"):
            off._guard(str(self.inside), [str(self.root)])
        self.assertTrue(self.inside.is_file())

    def test_a_sibling_that_merely_shares_a_prefix_is_refused(self):
        sibling = self.tmp / "media-extra"
        sibling.mkdir()
        victim = sibling / "ep.mkv"
        victim.write_bytes(b"do not touch")
        with self.assertRaisesRegex(MHError, "outside a Media-Hoarder source path"):
            self.mh._guard(str(victim), [str(self.root)])
        self.assertTrue(victim.is_file())

    def test_a_dotdot_segment_cannot_climb_out_of_the_source_path(self):
        escape = str(self.root / ".." / "outside" / "ep.mkv")
        with self.assertRaisesRegex(MHError, "outside a Media-Hoarder source path"):
            self.mh._guard(escape, [str(self.root)])
        self.assertTrue(self.stranger.is_file())

    def test_a_symlinked_season_directory_cannot_lead_out(self):
        link = self.root / "season"
        try:
            os.symlink(str(self.outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as e:
            self.skipTest("this platform will not create symlinks here: {0}".format(e))
        with self.assertRaisesRegex(MHError, "outside a Media-Hoarder source path"):
            self.mh._guard(str(link / "ep.mkv"), [str(self.root)])
        self.assertTrue(self.stranger.is_file())

    def test_delete_files_reclaims_inside_and_reports_outside(self):
        freed, errors = self.mh.delete_files([str(self.inside), str(self.stranger)])
        self.assertEqual(freed, len(b"inside"))
        self.assertEqual(len(errors), 1)
        self.assertIn("outside a Media-Hoarder source path", errors[0])
        self.assertFalse(self.inside.exists())
        self.assertTrue(self.stranger.is_file())


if __name__ == "__main__":
    unittest.main()
