"""Tests for forgejo-issue-index-raise.py: raise-only planning from a source db
and --set, a missing issue_index row, and the --apply round trip."""

import contextlib
import importlib.util
import io
import pathlib
import sqlite3
import tempfile
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("raise_index", HERE / "forgejo-issue-index-raise.py")
raise_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(raise_index)


def scratch_db(path, repos):
    """repos: {name: (repo_id, max_index or None, [issue indexes])}"""
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE repository (id INTEGER PRIMARY KEY, owner_name TEXT, lower_name TEXT);
        CREATE TABLE issue_index (group_id INTEGER PRIMARY KEY NOT NULL, max_index INTEGER NULL);
        CREATE TABLE issue (id INTEGER PRIMARY KEY, repo_id INTEGER, "index" INTEGER);
        """
    )
    for name, (rid, mx, issues) in repos.items():
        owner, repo = name.split("/")
        db.execute("INSERT INTO repository VALUES (?, ?, ?)", (rid, owner, repo))
        if mx is not None:
            db.execute("INSERT INTO issue_index VALUES (?, ?)", (rid, mx))
        for n in issues:
            db.execute('INSERT INTO issue (repo_id, "index") VALUES (?, ?)', (rid, n))
    db.commit()
    db.close()


def read_index(path):
    db = sqlite3.connect(path)
    out = dict(db.execute("SELECT group_id, max_index FROM issue_index"))
    db.close()
    return out


class RaiseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.live, self.src = str(d / "live.db"), str(d / "src.db")
        # live: hc restarted at 931 from an old backup; audio behind; notes has no row.
        scratch_db(self.live, {"me/hc": (15, 931, [930, 931]), "me/audio": (27, 16, [16]), "me/notes": (18, None, []), "me/ahead": (5, 12, [12])})
        # source: ids differ from live on purpose; match is by name.
        scratch_db(self.src, {"me/hc": (99, 960, [960]), "me/audio": (98, 18, [18]), "me/ahead": (97, 10, [10])})

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = raise_index.main([self.live, *args])
        return rc, out.getvalue()

    def test_dry_run_changes_nothing(self):
        rc, out = self.run_main("--from", self.src)
        self.assertEqual(rc, 0)
        self.assertIn("me/hc: max_index 931 -> 960 (next #961)", out)
        self.assertEqual(read_index(self.live), {15: 931, 27: 16, 5: 12})

    def test_apply_raises_only_and_matches_by_name(self):
        self.run_main("--from", self.src, "--apply")
        # ahead stays 12 (never lowered); notes has no issues, so no row (Forgejo makes it on the first).
        self.assertEqual(read_index(self.live), {15: 960, 27: 18, 5: 12})

    def test_set_and_from_take_the_highest(self):
        self.run_main("--from", self.src, "--set", "me/hc=955", "--set", "me/audio=20", "--apply")
        self.assertEqual(read_index(self.live)[15], 960)
        self.assertEqual(read_index(self.live)[27], 20)

    def test_counter_below_its_own_issues_is_repaired(self):
        db = sqlite3.connect(self.live)
        db.execute("UPDATE issue_index SET max_index = 900 WHERE group_id = 15")
        db.commit()
        db.close()
        self.run_main("--set", "me/audio=16", "--apply")
        self.assertEqual(read_index(self.live)[15], 931)

    def test_unknown_repo_in_set_refuses(self):
        with self.assertRaises(SystemExit):
            self.run_main("--set", "me/nope=5")

    def test_needs_a_source(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_main()


if __name__ == "__main__":
    unittest.main()
