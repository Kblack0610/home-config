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

    def test_no_slot_has_cores_for_small_jobs_alone(self):
        topo, fails = topology.render(self.hosts, self.groups)
        self.assertEqual(fails, [])
        small = [s["name"] for h in topo["hosts"].values() for s in h["slots"]
                 if set(s["caps"]) <= {"light", "heavy", "forgejo"} and not s.get("borrows")]
        self.assertEqual(small, [])

    def test_unity_slot_takes_heavy_only_on_a_heavy_host(self):
        self.hosts["deskslim-2"]["ci_caps"] = ["light", "unity", "forgejo"]
        slots, fails = self.slots()
        self.assertEqual(fails, [])
        self.assertEqual(slots["deskslim-2-unity-pool-1"], ["unity", "light"])
        self.hosts["deskslim-2"]["ci_caps"] = ["light", "heavy", "unity", "forgejo"]
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
        self.hosts["deskslim-2"]["ci_caps"] = ["unity", "forgejo"]
        _, fails = self.slots()
        self.assertIn("deskslim-2: has light slots, but ci_caps leaves out light", fails)

    def test_unknown_cap(self):
        self.hosts["ms-a2"]["ci_caps"] = ["light", "unity", "gpu"]
        _, fails = self.slots()
        self.assertTrue(any("ci_caps names gpu" in f for f in fails))

    def test_a_forgejo_seat_borrows_each_ungrouped_test_slot_and_has_no_cores_of_its_own(self):
        topo, fails = topology.render(self.hosts, self.groups)
        self.assertEqual(fails, [])
        fj = {s["name"]: s for h in topo["hosts"].values() for s in h["slots"] if s.get("forge") == "forgejo"}
        # ms-a2's slots 3-4 are build slot 5's group, so only 1-2 lend.
        self.assertEqual(sorted({s["borrows"] for s in fj.values()}), sorted(
            [f"ms-a2-unity-pool-{n}" for n in (1, 2)] + [f"deskslim-2-unity-pool-{n}" for n in (1, 2, 3)]
            + [f"deskslim-1-unity-pool-{n}" for n in (1, 2, 3)]
            + [f"windows-media-ci-unity-pool-{n}" for n in (1, 2, 3)]))
        self.assertTrue(all(s["caps"] == ["forgejo"] and n.startswith(s["borrows"] + "-forgejo") for n, s in fj.items()))

    def test_a_slot_registers_one_seat_per_min_job_cores_and_names_its_size(self):
        topo, fails = topology.render(self.hosts, self.groups)
        self.assertEqual(fails, [])
        slots = {s["name"]: s for s in topo["hosts"]["windows-media-ci"]["slots"]}
        seats = [s for s in slots.values() if s.get("borrows") == "windows-media-ci-unity-pool-1" and s.get("forge") == "forgejo"]
        cores = len(topology.cpu_set(self.hosts["windows-media-ci"]["ci_unity_slot_cpus"][0]))
        self.assertEqual([s["name"] for s in seats], ["windows-media-ci-unity-pool-1-forgejo"]
                         + [f"windows-media-ci-unity-pool-1-forgejo-{i}" for i in range(2, cores // topology.MIN_JOB_CORES + 1)])
        self.assertTrue(all(s["cpus"] == cores and s["memory_gb"] == 14 for s in seats))
        self.assertNotIn("ci_units", topology.KEYS)

    def test_ci_seat_kinds_put_org_seats_of_that_kind_on_each_lendable_slot_and_only_there(self):
        topo, fails = topology.render(self.hosts, self.groups)
        self.assertEqual(fails, [])
        light = {h: [s for s in e["slots"] if s["caps"] == ["light"] and s.get("borrows") and not s.get("repo")]
                 for h, e in topo["hosts"].items()}
        self.assertEqual({h for h, s in light.items() if s}, {"windows-media-ci"})
        seats = light["windows-media-ci"]
        self.assertEqual(len(seats), 9)
        self.assertEqual(seats[0], {"name": "windows-media-ci-unity-pool-1-light-1", "caps": ["light"],
                                    "borrows": "windows-media-ci-unity-pool-1", "cpus": 6, "memory_gb": 14})
        self.assertTrue(all("forge" not in s for s in seats))

    def test_ci_seat_kinds_must_be_small_kinds_the_host_takes_and_not_on_a_cloud_host(self):
        for host, kinds, want in (("windows-media-ci", ["heavy"], "ci_seat_kinds names heavy"),
                                  ("windows-media-ci", ["unity"], "ci_seat_kinds names unity"),
                                  ("aws-spot-1", ["light"], "ci_seat_kinds on a cloud host")):
            with self.subTest(host=host, kinds=kinds):
                self.setUp()
                self.hosts[host]["ci_seat_kinds"] = kinds
                _, fails = topology.render(self.hosts, self.groups)
                self.assertTrue(any(want in f for f in fails), fails)

    def test_every_repo_runner_borrows_a_unity_slot(self):
        topo, fails = topology.render(self.hosts, self.groups)
        self.assertEqual(fails, [])
        repo = [s for h in topo["hosts"].values() for s in h["slots"]
                if s.get("repo", "").startswith("Kblack0610/")]
        self.assertTrue(repo)
        self.assertTrue(all(s.get("borrows") for s in repo))

    def test_a_repo_runner_on_a_grouped_or_missing_slot_fails(self):
        for host, n in (("deskslim-2", 4), ("ms-a2", 3)):
            with self.subTest(host=host, n=n):
                self.setUp()
                self.hosts[host]["ci_repo_runners"] = [{"repo": "Kblack0610/x", "slot": "x-1", "borrows": n}]
                _, fails = self.slots()
                self.assertTrue(any(f"{host}-x-1 borrows {host}-unity-pool-{n}, not one of" in f for f in fails), fails)

    def test_a_repo_runner_without_borrows_fails(self):
        self.hosts["deskslim-2"]["ci_repo_runners"] = [{"repo": "Kblack0610/x", "slot": "x-1"}]
        _, fails = self.slots()
        self.assertIn("deskslim-2: ci_repo_runners x-1 has no borrows (the Unity slot it runs on)", fails)

    def test_a_host_without_forgejo_in_ci_caps_gets_no_seat(self):
        self.hosts["ms-a2"]["ci_caps"] = ["light", "heavy", "unity", "unity-build"]
        slots, fails = self.slots()
        self.assertEqual(fails, [])
        self.assertFalse([n for n, caps in slots.items() if n.startswith("ms-a2-") and "forgejo" in caps])

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
        # ms-a2's build slot 5 and aws-build-1's slots 1-2 carry unity-release; aws-build-1 slot 3 does not.
        self.assertEqual(topology.fit_failures(self.hosts, self.groups), [])
        self.hosts["aws-build-1"]["unity_ci_slot_labels"] = {}
        self.hosts["ms-a2"]["unity_ci_slot_labels"] = {1: ["slim-tour"]}
        self.assertTrue(any("0 of the pool's 4 build slots" in f for f in topology.fit_failures(self.hosts, self.groups)))
        self.hosts["aws-build-1"]["unity_ci_slot_labels"] = {n: ["unity-release"] for n in (1, 2, 3)}
        self.hosts["ms-a2"]["unity_ci_slot_labels"] = {5: ["unity-release"]}
        self.assertTrue(any("4 of the pool's 4 build slots" in f for f in topology.fit_failures(self.hosts, self.groups)))

    def wide_ms_a2(self):
        """ms-a2 with a wide slot 6 over test slots 1 and 2's cores (cores 2-7), beside build slot 5."""
        v = self.hosts["ms-a2"]
        v.update(unity_ci_slots=6, unity_ci_slot_groups={5: [3, 4], 6: [1, 2]}, unity_ci_wide_slots=[6],
                 ci_unity_slot_cpus=[*v["ci_unity_slot_cpus"], "2-7,18-23"],
                 ci_unity_slot_memory_max=[*v["ci_unity_slot_memory_max"], "32G"],
                 ci_caps=[*v["ci_caps"], "unity-wide"])

    def test_a_wide_lead_is_a_unity_wide_slot_with_its_group(self):
        self.wide_ms_a2()
        topo, fails = topology.render(self.hosts, self.groups)
        self.assertEqual(fails + topology.fit_failures(self.hosts, self.groups), [])
        slots = {s["name"]: s for s in topo["hosts"]["ms-a2"]["slots"]}
        self.assertEqual(slots["ms-a2-unity-pool-6"]["caps"], ["unity-wide"])
        self.assertEqual(slots["ms-a2-unity-pool-6"]["group"], ["ms-a2-unity-pool-1", "ms-a2-unity-pool-2"])
        self.assertEqual(slots["ms-a2-unity-pool-5"]["caps"], ["unity-build"])

    def test_a_wide_lead_does_not_count_as_a_build_slot_for_the_release_rule(self):
        self.wide_ms_a2()
        self.assertEqual(topology.fit_failures(self.hosts, self.groups), [])

    def test_a_wide_slot_outside_a_group_or_under_the_memory_bar_fails(self):
        self.wide_ms_a2()
        self.hosts["ms-a2"]["unity_ci_wide_slots"] = [6, 2]
        _, fails = topology.render(self.hosts, self.groups)
        self.assertIn("ms-a2: wide slot 2 (unity_ci_wide_slots) is not a unity_ci_slot_groups key", fails)
        self.hosts["ms-a2"]["unity_ci_wide_slots"] = [6]
        self.hosts["ms-a2"]["ci_unity_slot_memory_max"][5] = "16G"
        self.assertTrue(any("ms-a2 carries unity-wide" in f for f in topology.fit_failures(self.hosts, self.groups)))


if __name__ == "__main__":
    unittest.main()
