#!/usr/bin/env python3
"""Offline tests for governor.py: canned readings, no Prometheus, no GitHub.

Covers what a live night cannot show on demand: a hot host withholds its idle
runner first and its longest-running busy one next, a host with room re-admits
only while jobs wait, the pool floor and cap hold, a cooling host is left alone,
a missing reading is never room, a standby host takes only what the others cannot,
and a live action adds the new label before it removes the old. Stdlib only; CI runs it in the checks workflow.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import governor  # noqa: E402

CFG = governor.load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
COOL = {"idle": 0.6, "psi": 0.05, "tctl": 60, "mem": 0.5}
BUSY = {"idle": 0.3, "psi": 0.3, "tctl": 70, "mem": 0.4}
HOT = {"idle": 0.1, "psi": 0.5, "tctl": 70, "mem": 0.4}


def readings(**per_host):
    return {k: {h: r[k] for h, r in per_host.items() if k in r} for k in governor.QUERIES}


def pool(*specs):
    """specs: (name, host, busy, elapsed, withheld)."""
    return {n: {"host": h, "busy": b, "elapsed": e, "withheld": w} for n, h, b, e, w in specs}


RUNNERS = pool(("razer-1", "razer-laptop", True, 900, False), ("razer-2", "razer-laptop", True, 100, False),
               ("razer-3", "razer-laptop", False, 0, False), ("asus-1", "asus-laptop", True, 300, False),
               ("hpv-1", "hp-victus", True, 50, False), ("hpv-2", "hp-victus", False, 0, True))


def actions(hosts, runners=RUNNERS, queue=5, last=None, cfg=CFG):
    return {h: (a, r) for h, a, r, _, _ in governor.decide(cfg, hosts, runners, queue, last or {}, 10_000)}


class GovernorTest(unittest.TestCase):
    def test_hot_host_withholds_its_idle_runner_first(self):
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": BUSY}))
        self.assertEqual(got["razer-laptop"], ("withhold", "razer-3"))

    def test_then_the_busy_runner_nearest_its_end(self):
        runners = {n: r for n, r in RUNNERS.items() if n != "razer-3"}
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": BUSY}), runners)
        self.assertEqual(got["razer-laptop"], ("withhold", "razer-1"))

    def test_room_readmits_only_while_jobs_wait(self):
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": COOL})
        self.assertEqual(actions(hosts)["hp-victus"], ("admit", "hpv-2"))
        self.assertEqual(actions(hosts, queue=0)["hp-victus"], ("hold", ""))

    def test_host_floor(self):
        runners = pool(("razer-1", "razer-laptop", True, 9, False), ("asus-1", "asus-laptop", True, 9, False),
                       ("hpv-1", "hp-victus", True, 9, False))
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": HOT, "hp-victus": BUSY}), runners)
        self.assertEqual(got["razer-laptop"], ("hold", ""))   # razer's floor is 1
        self.assertEqual(got["asus-laptop"], ("withhold", "asus-1"))  # asus's is 0

    def test_pool_floor_keeps_the_watch_green(self):
        runners = pool(("asus-1", "asus-laptop", True, 9, False), ("razer-1", "razer-laptop", True, 9, False),
                       ("razer-2", "razer-laptop", False, 0, True), ("hpv-1", "hp-victus", True, 9, True))
        got = actions(readings(**{"razer-laptop": BUSY, "asus-laptop": HOT, "hp-victus": BUSY}), runners)
        self.assertEqual(got["asus-laptop"], ("hold", ""))

    def test_pool_cap(self):
        cfg = {**CFG, "pool_max_admitted": 5}
        got = actions(readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": COOL}), cfg=cfg)
        self.assertEqual(got["hp-victus"], ("hold", ""))

    def test_a_withhold_makes_room_under_the_cap(self):
        cfg = {**CFG, "pool_max_admitted": 5}
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": COOL}), cfg=cfg)
        self.assertEqual(got["razer-laptop"], ("withhold", "razer-3"))
        self.assertEqual(got["hp-victus"], ("admit", "hpv-2"))

    def test_cooling_host_is_left_alone(self):
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": BUSY}),
                      last={"razer-laptop": 10_000 - 60})
        self.assertEqual(got["razer-laptop"], ("hold", ""))

    def test_a_missing_reading_is_never_room(self):
        got = actions(readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": {"tctl": 50}}))
        self.assertEqual(got["hp-victus"], ("hold", ""))

    def test_asus_runs_hot_sooner(self):
        warm = {**COOL, "tctl": 91}
        self.assertEqual(governor.verdict(warm, CFG["hosts"]["asus-laptop"])[0], "HOT")
        self.assertEqual(governor.verdict(warm, CFG["hosts"]["razer-laptop"])[0], "STEADY")

    def mac(self, on_busy=0, on_idle=0, off=2):
        specs = [(f"mac-{i}", "mac-studio", True, 100 - i, False) for i in range(on_busy)]
        specs += [(f"mac-idle-{i}", "mac-studio", False, 0, False) for i in range(on_idle)]
        specs += [(f"mac-off-{i}", "mac-studio", False, 0, True) for i in range(off)]
        return pool(*specs)

    FULL = pool(("razer-1", "razer-laptop", True, 900, False), ("hpv-1", "hp-victus", True, 50, False))
    STEADY = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
    CAPPED = {**CFG, "standby": {"mac-studio": {"max_admitted": 6}}}

    def test_standby_stays_out_while_another_host_has_an_idle_slot(self):
        runners = {**self.mac(), **RUNNERS}  # razer-3 is admitted and idle
        self.assertEqual(actions(self.STEADY, runners)["mac-studio"], ("hold", ""))

    def test_standby_takes_one_when_the_other_hosts_are_full(self):
        runners = {**self.mac(), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners)["mac-studio"], ("admit", "mac-off-0"))

    def test_standby_waits_for_a_host_with_room_to_admit_first(self):
        runners = {**self.mac(), **self.FULL, "hpv-2": RUNNERS["hpv-2"]}
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": COOL})
        got = actions(hosts, runners)
        self.assertEqual(got["hp-victus"], ("admit", "hpv-2"))
        self.assertEqual(got["mac-studio"], ("hold", ""))

    def test_standby_never_passes_its_own_cap(self):
        runners = {**self.mac(on_busy=6), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, cfg=self.CAPPED)["mac-studio"], ("hold", ""))

    def test_standby_over_its_cap_gives_one_back(self):
        runners = {**self.mac(on_busy=8, off=0), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, cfg=self.CAPPED)["mac-studio"], ("withhold", "mac-0"))

    def test_standby_respects_the_pool_cap(self):
        runners = {**self.mac(on_busy=1), **self.FULL}
        got = actions(self.STEADY, runners, cfg={**CFG, "pool_max_admitted": 3})
        self.assertEqual(got["mac-studio"], ("hold", ""))

    def test_standby_goes_back_when_nothing_waits(self):
        runners = {**self.mac(on_busy=1, on_idle=1), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, queue=0)["mac-studio"], ("withhold", "mac-idle-0"))

    def test_standby_leaves_a_busy_slot_when_nothing_waits(self):
        runners = {**self.mac(on_busy=1), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, queue=0)["mac-studio"], ("hold", ""))

    def test_standby_slots_do_not_hold_the_pool_floor(self):
        runners = {**self.mac(on_busy=4, off=0), "asus-1": RUNNERS["asus-1"], "razer-1": RUNNERS["razer-1"]}
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": HOT, "hp-victus": BUSY})
        self.assertEqual(actions(hosts, runners)["asus-laptop"], ("hold", ""))

    def test_live_admit_adds_before_it_removes(self):
        calls = []

        def gh(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                return {"runners": [{"id": 7, "name": "hpv-2",
                                     "labels": [{"name": "self-hosted"}, {"name": "unity-withheld"}]}]}
            return None
        governor.gh = gh
        governor.apply(CFG, "admit", "hpv-2")
        self.assertEqual([c[0] for c in calls], ["GET", "POST", "DELETE"])
        self.assertEqual(calls[1][2], {"labels": ["unity", "unity-full"]})
        self.assertTrue(calls[2][1].endswith("/7/labels/unity-withheld"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
