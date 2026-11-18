"""Offline check of the shared GitHub host-key tasks: python3 ansible/roles/ci-host/tests/test_github_known_hosts.py"""
import os
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(HERE, "..", "tasks", "github-known-hosts.yml")


class GithubKnownHosts(unittest.TestCase):
    def test_meta_read_runs_on_every_host(self):
        # Both callers import this file under a `when`. A run_once read runs on the play's
        # first host only; when that host fails the `when` (razer-laptop, no Unity caps), the
        # skipped result goes to every host and the loop over its .json fails on the rest.
        with open(TASKS) as f:
            tasks = yaml.safe_load(f)
        reads = [t for t in tasks if "ansible.builtin.uri" in t]
        self.assertEqual(len(reads), 1)
        self.assertFalse(reads[0].get("run_once", False))
        loops = [t for t in tasks if "_github_meta" in str(t.get("loop", ""))]
        self.assertTrue(loops, "the known_hosts task no longer loops over _github_meta")


if __name__ == "__main__":
    unittest.main()
