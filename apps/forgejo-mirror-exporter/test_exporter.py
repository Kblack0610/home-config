#!/usr/bin/env python3
"""Offline tests for the default-branch rewind and ref-clobbering remote signals.

Builds bare-repo-shaped directories (HEAD, refs, logs, config) in a temp dir;
no git binary, no Forgejo. The reflog cases are the shapes seen on home-config's
server reflog: a push mirror rewinding master after a merge, the re-land that
moves it forward again, and two mirrors flip-flopping. Stdlib only; CI runs it
in the checks workflow.
"""
import contextlib
import io
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exporter  # noqa: E402

NOW = time.time()
Z = "0" * 40
A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)


def entry(old, new, age, msg="push"):
    return f"{old} {new} Gitea <gitea@fake.local> {NOW - age:.0f} +0000\t{msg}\n"


class Repo:
    def __init__(self, root, branch="master", reflog=None, packed=False, config=""):
        self.path = os.path.join(root, "o", "r.git")
        os.makedirs(os.path.join(self.path, "refs", "heads"))
        with open(os.path.join(self.path, "HEAD"), "w") as f:
            f.write(f"ref: refs/heads/{branch}\n")
        if packed:
            with open(os.path.join(self.path, "packed-refs"), "w") as f:
                f.write(f"# pack-refs with: peeled fully-peeled sorted\n{A} refs/heads/{branch}\n")
        else:
            with open(os.path.join(self.path, "refs", "heads", branch), "w") as f:
                f.write(A + "\n")
        if reflog is not None:
            os.makedirs(os.path.join(self.path, "logs", "refs", "heads"))
            with open(os.path.join(self.path, "logs", "refs", "heads", branch), "w") as f:
                f.writelines(reflog)
        with open(os.path.join(self.path, "config"), "w") as f:
            f.write("[core]\n\tbare = true\n" + config)


class RewindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def rewinds(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            branch, rw = exporter._branch_rewinds(Repo(self.tmp.name, **kw).path, NOW)
        return branch, rw, out.getvalue()

    def test_fast_forwards_are_not_rewinds(self):
        _, rw, _ = self.rewinds(reflog=[entry(Z, A, 900), entry(A, B, 600), entry(B, C, 300)])
        self.assertEqual(rw, {"count": 0, "newest": 0.0})

    def test_mirror_reset_after_merge_is_a_rewind_and_names_the_dropped_sha(self):
        # #697: merge B on A, then "update by push" sets master back to A.
        _, rw, out = self.rewinds(reflog=[entry(Z, A, 900), entry(A, B, 300), entry(B, A, 298, "update by push")])
        self.assertEqual(rw["count"], 1)
        self.assertIn(f"{B} -> {A}", out)

    def test_relanding_the_dropped_merge_moves_forward(self):
        _, rw, _ = self.rewinds(reflog=[entry(Z, A, 900), entry(A, B, 300), entry(B, A, 298), entry(A, B, 200)])
        self.assertEqual(rw["count"], 1)

    def test_two_mirrors_flip_flopping_count_once(self):
        # master at C, one mirror writes back B, the next writes C again.
        _, rw, _ = self.rewinds(reflog=[entry(Z, B, 900), entry(B, C, 600), entry(C, B, 300), entry(B, C, 299)])
        self.assertEqual(rw["count"], 1)

    def test_rewind_outside_the_window_keeps_its_timestamp_but_not_its_count(self):
        old = exporter.REWIND_WINDOW + 3600
        _, rw, _ = self.rewinds(reflog=[entry(Z, A, old + 60), entry(A, B, old + 30), entry(B, A, old)])
        self.assertEqual(rw["count"], 0)
        self.assertAlmostEqual(rw["newest"], NOW - old, delta=1)

    def test_branch_delete_and_recreate_are_not_rewinds(self):
        _, rw, _ = self.rewinds(reflog=[entry(Z, A, 900), entry(A, B, 600), entry(B, Z, 500), entry(Z, A, 400)])
        self.assertEqual(rw["count"], 0)

    def test_missing_reflog_is_unknown_not_zero(self):
        branch, rw, _ = self.rewinds(reflog=None)
        self.assertEqual(branch, "master")
        self.assertIsNone(rw)

    def test_packed_branch_is_found(self):
        branch, rw, _ = self.rewinds(reflog=[entry(Z, A, 60)], packed=True)
        self.assertEqual((branch, rw["count"]), ("master", 0))

    def test_empty_repo_reports_no_branch(self):
        path = Repo(self.tmp.name).path
        os.unlink(os.path.join(path, "refs", "heads", "master"))
        self.assertEqual(exporter._branch_rewinds(path, NOW), (None, None))


class ClobberingRemoteTest(unittest.TestCase):
    def remotes(self, config):
        with tempfile.TemporaryDirectory() as tmp:
            return exporter._clobbering_remotes(Repo(tmp, config=config).path)

    def test_push_mirror_with_mirror_fetch_refspec_is_named(self):
        cfg = ('[remote "remote_mirror_x"]\n\turl = ssh://github.com/o/r.git\n'
               "\tfetch = +refs/*:refs/*\n\tmirror = true\n"
               "\tpush = +refs/heads/*:refs/heads/*\n\tpush = +refs/tags/*:refs/tags/*\n")
        self.assertEqual(self.remotes(cfg), ["remote_mirror_x"])

    def test_push_mirror_without_fetch_refspec_is_fine(self):
        cfg = ('[remote "remote_mirror_x"]\n\turl = ssh://github.com/o/r.git\n'
               "\tmirror = true\n\tpush = +refs/heads/*:refs/heads/*\n")
        self.assertEqual(self.remotes(cfg), [])

    def test_pull_mirror_maps_refs_but_never_pushes(self):
        cfg = ('[remote "origin"]\n\turl = git@github.com:o/r.git\n'
               "\tfetch = +refs/*:refs/*\n\tmirror = true\n")
        self.assertEqual(self.remotes(cfg), [])

    def test_ordinary_remote_tracking_refspec_is_fine(self):
        cfg = ('[remote "gh"]\n\turl = git@github.com:o/r.git\n'
               "\tfetch = +refs/heads/*:refs/remotes/gh/*\n\tpush = refs/heads/master\n")
        self.assertEqual(self.remotes(cfg), [])


class RenderTest(unittest.TestCase):
    def test_series_are_emitted_from_fs_state(self):
        rec = {"locks": {}, "tmps": {}, "gc_log": None, "loose": 0, "branch": "master",
               "rewinds": {"count": 2, "newest": 1700000000.0}, "clobbering_remotes": ["m1"]}
        bare = dict(rec, branch="main", rewinds=None, clobbering_remotes=[])
        with exporter.LOCK:
            exporter.STATE["fs"].update(data={"o/hc": rec, "o/bare": bare}, ok=1, ts=NOW)
        text = exporter.render()
        self.assertIn('forgejo_repo_default_branch_rewinds{repo="o/hc",branch="master"} 2', text)
        self.assertIn('forgejo_repo_default_branch_reflog_present{repo="o/hc",branch="master"} 1', text)
        self.assertIn('forgejo_repo_push_remote_rewrites_branches{repo="o/hc",remote="m1"} 1', text)
        self.assertIn('forgejo_repo_default_branch_reflog_present{repo="o/bare",branch="main"} 0', text)
        self.assertNotIn('forgejo_repo_default_branch_rewinds{repo="o/bare"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
