"""Tests for ci-kind-weights.py: python3 scripts/test_ci_kind_weights.py"""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location(
    "ci_kind_weights", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci-kind-weights.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
G = m.GIB

TOPOLOGY = {"box": {"slots": [
    {"name": "box-unity-pool-1", "caps": ["unity", "light", "heavy"]},
    {"name": "box-unity-pool-2", "caps": ["unity", "light", "heavy"]},
    {"name": "box-unity-pool-1-forgejo", "caps": ["forgejo"], "forge": "forgejo", "borrows": "box-unity-pool-1"},
    {"name": "box-web-1", "caps": ["light"], "repo": "acme/web", "borrows": "box-unity-pool-2"}]},
    "mac": {"slots": [{"name": "mac-mac", "caps": ["macos"], "repo": "acme/app"}]}}
HOSTS = {"box": {"unity_ci_slots": 2, "ci_unity_slot_cpus": ["0-5", "6-11"], "ci_unity_slot_memory_max": "16G"}}


class SlotSizes(unittest.TestCase):
    def test_a_seat_takes_the_size_of_the_slot_it_borrows(self):
        s = m.slot_sizes(TOPOLOGY, HOSTS)
        self.assertEqual(s["box-unity-pool-1"], (6, 16 * G))
        self.assertEqual(s["box-unity-pool-1-forgejo"], (6, 16 * G))
        self.assertEqual(s["box-web-1"], (6, 16 * G))
        self.assertNotIn("mac-mac", s)


class Weights(unittest.TestCase):
    def setUp(self):
        self.sizes = m.slot_sizes(TOPOLOGY, HOSTS)

    def test_weight_is_the_larger_p90_share_capped_at_one(self):
        cores = {("box-unity-pool-1", "light"): {0: 0.3, 60: 0.6},
                 ("box-unity-pool-1-forgejo", "forgejo"): {0: 1.2, 60: 1.2},
                 ("box-unity-pool-2", "unity"): {0: 9.0}}
        mem = {("box-unity-pool-1", "light"): {0: 1 * G, 60: 1 * G},
               ("box-unity-pool-1-forgejo", "forgejo"): {0: 12 * G, 60: 12 * G},
               ("box-unity-pool-2", "unity"): {0: 8 * G}}
        w = m.weights(cores, mem, self.sizes)
        self.assertEqual(w["light"]["samples"], 2)
        self.assertAlmostEqual(w["light"]["cores_p90"], 0.57)
        self.assertAlmostEqual(w["light"]["weight"], 0.57 / 6)  # cores share beats 1/16 memory
        self.assertAlmostEqual(w["forgejo"]["weight"], 12 / 16)  # memory share beats 1.2/6 cores
        self.assertEqual(w["unity"]["weight"], 1.0)  # 9 busy CPUs on a 6-CPU slot is a whole slot

    def test_a_runner_with_no_slot_has_figures_and_no_share(self):
        w = m.weights({("mac-mac", "macos"): {0: 2.0}}, {("mac-mac", "macos"): {0: 4 * G}}, self.sizes)
        self.assertEqual(w["macos"]["cores_p50"], 2.0)
        self.assertIsNone(w["macos"]["cores_share_p90"])
        self.assertIsNone(w["macos"]["weight"])

    def test_a_kind_with_no_samples_reads_dash_not_zero(self):
        rows = m.table(m.weights({("box-unity-pool-1", "light"): {0: 0.6}}, {}, self.sizes))
        (heavy,) = [r for r in rows if r.startswith("heavy ")]
        self.assertEqual(heavy.split()[1:], ["-"] * 8)
        (light,) = [r for r in rows if r.startswith("light ")]
        self.assertIn("0.10", light)  # 0.6 of 6 CPUs
        self.assertNotIn(" 0.00", heavy)


if __name__ == "__main__":
    unittest.main()
