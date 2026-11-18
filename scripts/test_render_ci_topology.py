"""Core tiers and explicit CI prohibitions must survive every affinity branch."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml

SPEC = importlib.util.spec_from_file_location("topology", Path(__file__).with_name("render-ci-topology.py"))
topology = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(topology)
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
        return topology.no_ci_failures(*topology.load_inventory(inventory), runner)

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


class CapTests(unittest.TestCase):
    def setUp(self):
        self.hosts, self.groups = topology.load_inventory(ROOT / "ansible/inventory.yml")

    def slots(self):
        topo, fails = topology.render(self.hosts, self.groups)
        return {s["name"]: s["caps"] for h in topo.get("hosts", {}).values() for s in h["slots"]}, fails

    def with_heavy(self):
        """No host has a heavy slot beside Unity slots since razer-laptop left the pool; give ms-a2 one."""
        self.hosts["ms-a2"]["ci_heavy_slots"] = 1
        self.hosts["ms-a2"]["ci_caps"] = ["light", "heavy", "unity"]
        return self.slots()

    def test_unity_test_slot_takes_its_hosts_light(self):
        slots, fails = self.with_heavy()
        self.assertEqual(fails, [])
        self.assertEqual(slots["ms-a2-unity-pool-1"], ["unity", "light"])
        self.assertEqual(slots["ms-a2-heavy-1"], ["heavy", "light"])

    def test_heavy_waits_for_ephemeral_ports(self):
        slots, _ = self.with_heavy()
        self.assertNotIn("heavy", slots["ms-a2-unity-pool-1"])

    def test_build_slot_takes_builds_alone(self):
        slots, _ = self.slots()
        self.assertEqual(slots["aws-build-1-unity-pool-1"], ["unity-build"])

    def test_cloud_host_never_takes_light(self):
        self.hosts["aws-spot-1"]["ci_caps"] = ["unity", "light"]
        _, fails = self.slots()
        self.assertTrue(any("aws-spot-1: a cloud host" in f for f in fails))

    def test_slot_kind_missing_from_caps(self):
        self.hosts["ms-a2"]["ci_caps"] = ["unity"]
        _, fails = self.slots()
        self.assertIn("ms-a2: has light slots, but ci_caps leaves out light", fails)

    def test_unknown_cap(self):
        self.hosts["ms-a2"]["ci_caps"] = ["light", "unity", "gpu"]
        _, fails = self.slots()
        self.assertTrue(any("ci_caps names gpu" in f for f in fails))
