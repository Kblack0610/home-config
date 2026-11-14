"""Offline tests for runner_labels_plan: python3 ansible/roles/github-actions-runner-linux/tests/test_runner_labels.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "filter_plugins"))
from runner_labels import runner_labels_plan  # noqa: E402

GOVERNED = ["unity", "unity-full", "unity-build"]
WITHHELD_ALL = ["unity-withheld", "unity-build-withheld"]
BASE = ["self-hosted", "linux", "x64", "host-aws-build-1"]


def runner(*custom):
    ro = [{"id": 0, "name": n, "type": "read-only"} for n in ("self-hosted", "Linux", "X64")]
    return {"name": "aws-build-1-unity-pool-1", "labels": ro + [{"id": 0, "name": n, "type": "custom"} for n in custom]}


def plan(r, desired, withheld="unity-build-withheld"):
    """The labels to PUT, or None when the runner needs none."""
    out = runner_labels_plan(r, desired, GOVERNED, withheld, WITHHELD_ALL)
    return out["labels"] if out["change"] else None


class Plan(unittest.TestCase):
    def test_build_slot_left_on_pool_labels(self):
        # 2026-11-12: slots 1-3 became build slots and kept their pool labels.
        r = runner("host-aws-build-1", "unity", "unity-full", "unity-gate")
        self.assertEqual(plan(r, BASE + ["unity-build", "unity-release"]),
                         ["host-aws-build-1", "unity-build", "unity-release"])

    def test_matching_runner_needs_no_put(self):
        r = runner("host-aws-build-1", "unity-build", "unity-release")
        self.assertIsNone(plan(r, BASE + ["unity-release", "unity-build"]))

    def test_case_and_readonly_ignored(self):
        r = runner("Host-AWS-Build-1", "unity-build")
        self.assertIsNone(plan(r, ["Self-Hosted", "LINUX", "x64", "host-aws-build-1", "unity-build"]))

    def test_withheld_slot_stays_withheld(self):
        # The live state of pool-1: the governor holds it; an apply must not admit it.
        r = runner("host-aws-build-1", "unity-release", "unity-build-withheld")
        self.assertIsNone(plan(r, BASE + ["unity-build", "unity-release"]))

    def test_withheld_slot_still_gets_other_changes(self):
        r = runner("host-aws-build-1", "unity-withheld", "unity-gate")
        self.assertEqual(plan(r, BASE + ["unity", "unity-full", "unity-gate", "unity-wide"], "unity-withheld"),
                         ["host-aws-build-1", "unity-gate", "unity-wide", "unity-withheld"])

    def test_withheld_pool_slot_turned_build_gets_build_withheld(self):
        # The governor admits a build slot by dropping unity-build-withheld, so a stale
        # unity-withheld would never be dropped.
        r = runner("host-aws-build-1", "unity-withheld", "unity-gate")
        self.assertEqual(plan(r, BASE + ["unity-build"]), ["host-aws-build-1", "unity-build-withheld"])

    def test_admitted_slot_gets_governed_labels(self):
        r = runner("host-razer-laptop", "unity")
        self.assertEqual(plan(r, ["self-hosted", "host-razer-laptop", "unity", "unity-full"], "unity-withheld"),
                         ["host-razer-laptop", "unity", "unity-full"])

    def test_hand_added_label_removed(self):
        r = runner("docker", "stray")
        self.assertEqual(runner_labels_plan(r, ["self-hosted", "linux", "x64", "docker"]),
                         {"change": True, "labels": ["docker"]})

    def test_no_custom_labels_left_is_a_change(self):
        self.assertEqual(runner_labels_plan(runner("docker"), ["self-hosted"]), {"change": True, "labels": []})

    def test_withheld_without_a_label_to_keep_fails(self):
        with self.assertRaises(ValueError):
            runner_labels_plan(runner("unity-withheld"), BASE, GOVERNED, None, WITHHELD_ALL)


if __name__ == "__main__":
    unittest.main()
