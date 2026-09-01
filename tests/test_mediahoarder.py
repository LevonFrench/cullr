"""Regression tests for the Media-Hoarder source.

Two things here are worth protecting. The path helpers have to survive the
mixed separators Media-Hoarder actually records, and chip labels have to stay
unique when two hosts export the same share name. Separately, the database is
opened through a percent-encoded URI so a '#' in the path cannot silently point
SQLite at some other file and open it read-write.

Everything runs against temporary directories. Nothing touches a real library.
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cullr.mediahoarder import (
    MediaHoarder,
    _drive_of,
    _first_segment,
    _join,
    _labels_for,
    _root_of,
)

UNC_A = "\\\\hostA\\media"
UNC_B = "\\\\hostB\\media"


def _make_db(path):
    """A minimal database with the two tables this module reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute("create table tbl_SourcePaths "
                    "(id_SourcePaths integer primary key, Path text)")
        con.execute("create table tbl_Movies (id_Movies integer primary key)")
        con.execute("insert into tbl_SourcePaths (id_SourcePaths, Path) values (1, ?)",
                    (str(path.parent),))
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


if __name__ == "__main__":
    unittest.main()
