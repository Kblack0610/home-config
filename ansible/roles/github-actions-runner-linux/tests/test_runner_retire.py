"""Offline tests for runners_retired: python3 ansible/roles/github-actions-runner-linux/tests/test_runner_retire.py"""
import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "filter_plugins"))
from runner_retire import runners_retired  # noqa: E402

HOME = "/var/lib/linux-ci"


def slurp(dirname, reg, bom=True, raw=None):
    if raw is None:
        raw = json.dumps(reg).encode()
    # The runner writes .runner with a UTF-8 BOM (deskslim-1, 2026-11-21).
    return {"source": f"{HOME}/{dirname}/.runner".replace("//", "/"),
            "content": base64.b64encode((b"\xef\xbb\xbf" if bom else b"") + raw).decode()}


def gh(name, agent_id, url):
    return {"agentId": agent_id, "agentName": name, "poolName": "Default", "gitHubUrl": url}


ORG = "https://github.com/BlackNBrownStudios"
REPO = "https://github.com/Kblack0610/personal-sites"


def pick(slurped, keep_names=(), keep_dirs=()):
    return runners_retired(slurped, HOME, list(keep_names), list(keep_dirs))


class Retired(unittest.TestCase):
    def test_lend_seats_retired_standing_kept(self):
        got = pick([
            slurp("unity-pool-1-light-1", gh("deskslim-1-unity-pool-1-light-1", 2098, ORG)),
            slurp("small-light-1", gh("deskslim-1-small-light-1", 3001, ORG)),
            slurp("personal-sites-1", gh("deskslim-1-personal-sites-1", 41, REPO), bom=False),
            slurp("small-personal-sites-1", gh("deskslim-1-small-personal-sites-1", 77, REPO)),
        ], ["deskslim-1-small-light-1", "deskslim-1-small-personal-sites-1"])
        self.assertEqual(got, {"unreadable": [], "retire": [
            {"name": "deskslim-1-personal-sites-1", "id": 41, "dir": f"{HOME}/personal-sites-1",
             "unit": "actions.runner.deskslim-1-personal-sites-1.service",
             "api_path": "repos/Kblack0610/personal-sites"},
            {"name": "deskslim-1-unity-pool-1-light-1", "id": 2098, "dir": f"{HOME}/unity-pool-1-light-1",
             "unit": "actions.runner.deskslim-1-unity-pool-1-light-1.service",
             "api_path": "orgs/BlackNBrownStudios"},
        ]})

    def test_forgejo_seat_skipped(self):
        forgejo = {"id": 9, "uuid": "x", "name": "deskslim-1-small-forgejo-1", "address": "https://git.kblab.me"}
        self.assertEqual(pick([slurp("small-forgejo-1", forgejo, bom=False)]), {"retire": [], "unreadable": []})

    def test_keep_is_case_insensitive(self):
        self.assertEqual(pick([slurp("a", gh("Host-Small-Light-1", 1, ORG))], ["host-small-light-1"])["retire"], [])

    def test_kept_dir_with_an_older_name_is_kept(self):
        r = slurp("small-personal-sites-1", gh("deskslim-1-personal-sites-1", 41, REPO))
        self.assertEqual(pick([r], keep_dirs=[f"{HOME}/small-personal-sites-1/"])["retire"], [])

    def test_runner_in_the_home_itself_is_never_retired(self):
        # Its dir would be the whole home, every seat with it.
        got = pick([slurp("", gh("h-stray", 1, ORG))])
        self.assertEqual(got, {"retire": [], "unreadable": [f"{HOME}/.runner"]})

    def test_unreadable_files_are_listed_not_retired(self):
        got = pick([
            slurp("empty", None, raw=b""),
            slurp("half", None, raw=b'{"agentName": "h-x", "agentId": 1'),
            slurp("nourl", {"agentName": "h-y", "agentId": 2}),
            slurp("deep", gh("h-z", 3, "https://github.com/a/b/c")),
            {"failed": True, "msg": "file not found", "item": {"path": f"{HOME}/gone/.runner"}},
            slurp("ok", gh("h-ok", 4, ORG)),
        ])
        self.assertEqual([r["name"] for r in got["retire"]], ["h-ok"])
        self.assertEqual(got["unreadable"], sorted(f"{HOME}/{d}/.runner" for d in ("empty", "half", "nourl", "deep", "gone")))


if __name__ == "__main__":
    unittest.main()
