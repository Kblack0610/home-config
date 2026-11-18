"""Core tiers and explicit CI prohibitions hold, and every slot takes the caps its host allows."""

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

    def failures(self):
        inventory = self.path / "inventory.yml"
        inventory.write_text(yaml.safe_dump(self.inventory))
        return topology.no_ci_failures(*topology.load_inventory(inventory))

    def test_current_exclusions(self):
        self.assertEqual(self.failures(), [])

    def test_core_in_ci_group(self):
        self.inventory["all"]["children"]["ci_hosts"]["hosts"]["asus-laptop"] = {}
        self.assertTrue(any("core tier and is in ci_hosts" in error for error in self.failures()))

    def test_probation_no_ci_host_stays_excluded(self):
        self.inventory["all"]["children"]["ci_hosts"]["hosts"]["hp-victus"] = {}
        self.assertTrue(any("hp-victus is in no_ci_hosts and in ci_hosts" in error for error in self.failures()))

    def test_pinned_ci_host_cannot_be_core(self):
        self.inventory["all"]["children"]["macos_hosts"]["hosts"]["mac-mini"]["host_tier"] = "core"
        self.assertTrue(any("mac-mini carries pinned CI" in error for error in self.failures()))




class CapTests(unittest.TestCase):
    def setUp(self):
        self.hosts, self.groups = topology.load_inventory(ROOT / "ansible/inventory.yml")

    def slots(self):
        topo, fails = topology.render(self.hosts, self.groups)
        return {s["name"]: s["caps"] for h in topo.get("hosts", {}).values() for s in h["slots"]}, fails

    def test_unity_test_slot_takes_its_hosts_light(self):
        slots, fails = self.slots()
        self.assertEqual(fails, [])
        self.assertIn("light", slots["deskslim-2-unity-pool-1"])
        self.assertEqual(slots["ms-a2-heavy-1"], ["heavy", "light"])

    def test_unity_slot_takes_heavy_only_on_a_heavy_host(self):
        self.hosts["deskslim-2"]["ci_caps"] = ["light", "unity"]
        slots, fails = self.slots()
        self.assertEqual(fails, [])
        self.assertEqual(slots["deskslim-2-unity-pool-1"], ["unity", "light"])
        self.hosts["deskslim-2"]["ci_caps"] = ["light", "heavy", "unity"]
        slots, fails = self.slots()
        self.assertEqual(fails, [])
        self.assertEqual(slots["deskslim-2-unity-pool-1"], ["unity", "light", "heavy"])
        self.assertEqual(slots["ms-a2-unity-pool-1"], ["unity", "light", "heavy"])

    def test_unity_only_host_takes_no_heavy(self):
        slots, fails = self.slots()
        self.assertEqual(fails, [])
        self.assertEqual(slots["cachyos-x8664-main-unity-pool-1"], ["unity"])

    def test_cloud_host_never_takes_heavy(self):
        self.hosts["aws-spot-1"]["ci_caps"] = ["unity", "heavy"]
        _, fails = self.slots()
        self.assertTrue(any("aws-spot-1: a cloud host" in f for f in fails))

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

    def test_forgejo_slots_serve_forgejo_alone(self):
        topo, fails = topology.render(self.hosts, self.groups)
        self.assertEqual(fails, [])
        fj = [s for h in topo["hosts"].values() for s in h["slots"] if s.get("forge") == "forgejo"]
        self.assertEqual(len(fj), topology.FORGEJO_SLOTS)
        self.assertTrue(all(s["caps"] == ["forgejo"] for s in fj))

    def test_forgejo_slot_count_is_the_rule(self):
        self.hosts["ms-a2"]["forgejo_slots"] = topology.FORGEJO_SLOTS + 1
        _, fails = self.slots()
        self.assertTrue(any("not FORGEJO_SLOTS" in f for f in fails))

    def test_cloud_host_never_takes_forgejo(self):
        self.hosts["aws-spot-1"]["ci_caps"] = ["unity", "forgejo"]
        _, fails = self.slots()
        self.assertTrue(any("aws-spot-1: a cloud host" in f for f in fails))

    def test_a_lan_build_slot_is_its_group_key_on_its_tests_cores(self):
        topo, fails = topology.render(self.hosts, self.groups)
        self.assertEqual(fails + topology.fit_failures(self.hosts, self.groups), [])
        slots = {s["name"]: s for s in topo["hosts"]["ms-a2"]["slots"]}
        self.assertEqual(slots["ms-a2-unity-pool-5"]["caps"], ["unity-build"])
        self.assertEqual(slots["ms-a2-unity-pool-5"]["group"], ["ms-a2-unity-pool-3", "ms-a2-unity-pool-4"])
        self.assertEqual(slots["ms-a2-unity-pool-1"]["caps"], ["unity", "light", "heavy"])

    def test_a_group_off_its_build_slots_cores_fails(self):
        self.hosts["ms-a2"]["unity_ci_slot_groups"] = {5: [1, 2]}
        fails = topology.fit_failures(self.hosts, self.groups)
        self.assertIn("ms-a2: test slot 1's cores are not inside build slot 5's", fails)
        self.assertIn("ms-a2: slots 3 and 5 share cores and are not one slot group", fails)

    def test_the_release_rule_is_pool_wide(self):
        # ms-a2's one build slot has no unity-release; aws-build-1's slots 1-2 do.
        self.assertEqual(topology.fit_failures(self.hosts, self.groups), [])
        self.hosts["aws-build-1"]["unity_ci_slot_labels"] = {}
        self.assertTrue(any("0 of the pool's 4 build slots" in f for f in topology.fit_failures(self.hosts, self.groups)))
        self.hosts["aws-build-1"]["unity_ci_slot_labels"] = {n: ["unity-release"] for n in (1, 2, 3)}
        self.hosts["ms-a2"]["unity_ci_slot_labels"] = {5: ["unity-release"]}
        self.assertTrue(any("4 of the pool's 4 build slots" in f for f in topology.fit_failures(self.hosts, self.groups)))


if __name__ == "__main__":
    unittest.main()
