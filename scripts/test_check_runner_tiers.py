"""Core tiers and explicit CI prohibitions must survive every affinity branch."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml

SPEC = importlib.util.spec_from_file_location("roster", Path(__file__).with_name("check-runner-roster.py"))
roster = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(roster)
ROOT = Path(__file__).resolve().parents[1]


class RunnerTierTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.inventory = yaml.safe_load((ROOT / "ansible/inventory.yml").read_text())
        self.runner = yaml.safe_load((ROOT / "apps/forgejo/runner-deployment.yaml").read_text())
        self.terms = self.runner["spec"]["template"]["spec"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]

    def failures(self):
        inventory = self.path / "inventory.yml"
        runner = self.path / "runner.yml"
        inventory.write_text(yaml.safe_dump(self.inventory))
        runner.write_text(yaml.safe_dump(self.runner))
        return roster.no_ci_failures(inventory, runner)

    def test_current_exclusions(self):
        self.assertEqual(self.failures(), [])

    def test_core_in_ci_group(self):
        self.inventory["all"]["children"]["ci_hosts"]["hosts"]["asus-laptop"] = {}
        self.assertTrue(any("core tier and is in ci_hosts" in error for error in self.failures()))

    def test_probation_no_ci_host_stays_excluded(self):
        self.inventory["all"]["children"]["ci_hosts"]["hosts"]["hp-victus"] = {}
        self.assertTrue(any("hp-victus is in no_ci_hosts and in ci_hosts" in error for error in self.failures()))

    def test_no_ci_or_bypass(self):
        unsafe = copy.deepcopy(self.terms[0])
        unsafe["matchExpressions"] = [e for e in unsafe["matchExpressions"] if e["key"] != "kubernetes.io/hostname"]
        self.terms.append(unsafe)
        self.assertTrue(any("hp-victus" in error and "every required affinity term" in error for error in self.failures()))

    def test_core_or_bypass(self):
        unsafe = copy.deepcopy(self.terms[0])
        unsafe["matchExpressions"] = [e for e in unsafe["matchExpressions"] if e["key"] != "kblab.me/tier"]
        self.terms.append(unsafe)
        self.assertTrue(any("exclude core tier" in error for error in self.failures()))

    def test_pinned_ci_host_cannot_be_core(self):
        self.inventory["all"]["children"]["macos_hosts"]["hosts"]["mac-mini"]["host_tier"] = "core"
        self.assertTrue(any("mac-mini carries pinned CI" in error for error in self.failures()))


if __name__ == "__main__":
    unittest.main()
