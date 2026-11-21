"""Tests for ci-pool-idle.py: python3 -m unittest scripts/test_ci_pool_idle.py"""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location(
    "ci_pool_idle", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci-pool-idle.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
S = m.STEP


class Roster(unittest.TestCase):
    def test_hosts_and_kinds_come_from_the_topology(self):
        # The LAN hosts that share slots between kinds; the one-kind workstation and the cloud hosts stay out.
        self.assertIn("ms-a2", m.HOSTS.split("|"))
        self.assertNotIn("cachyos-x8664-main", m.HOSTS.split("|"))
        self.assertFalse([h for h in m.HOSTS.split("|") if h.startswith("aws-")])
        self.assertTrue({"unity", "light", "forgejo"} <= set(m.KINDS))


class Tally(unittest.TestCase):
    def test_idle_counts_only_while_a_kind_the_slot_cannot_take_waits(self):
        idle = {("ms-a2", "ms-a2-light-1"): {0: {"light"}, S: {"light"}, 2 * S: {"light"}},
                ("ms-a2", "ms-a2-light-2"): {0: {"light"}, S: {"light"}, 2 * S: {"light"}}}
        waiting = {"unity": {S: 3}, "light": {2 * S: 1}}
        lost, _ = m.tally(idle, waiting, {}, range(0, 3 * S, S))
        # t=0 nothing waits; t=S unity waits (both count); t=2S only light, which they take, waits (does not).
        self.assertAlmostEqual(lost["ms-a2"], 2 * S / 3600)

    def test_a_slot_that_takes_the_waiting_kind_is_not_lost(self):
        # The agnostic slot is unity first, but takes light too: a light wait does not strand it.
        pool_slot = {("ms-a2", "ms-a2-unity-pool-1", c): {0: 0, S: 0} for c in ("unity", "light", "heavy")}
        pool_slot[("ms-a2", "ms-a2-forgejo-1", "forgejo")] = {S: 0}
        idle = m.idle_slots(pool_slot)
        self.assertEqual(idle[("ms-a2", "ms-a2-unity-pool-1")][0], {"unity", "light", "heavy"})
        lost, _ = m.tally(idle, {"light": {0: 1, S: 1}}, {}, range(0, 2 * S, S))
        # Only the Forgejo seat, idle at t=S while a light job waited, counts.
        self.assertAlmostEqual(lost["ms-a2"], S / 3600)

    def test_wait_quantiles_only_over_minutes_a_job_waited(self):
        waiting = {"forgejo": {0: 1, S: 1, 2 * S: 0}}
        oldest = {"forgejo": {0: 60, S: 600, 2 * S: 9999}}
        _, waits = m.tally({}, waiting, oldest, range(0, 3 * S, S))
        hours, p50, p90 = waits["forgejo"]
        self.assertAlmostEqual(hours, 2 * S / 3600)
        self.assertAlmostEqual(p50, 330)
        self.assertLess(p90, 9999)
        self.assertEqual(waits["unity"], (0, None, None))


if __name__ == "__main__":
    unittest.main()
