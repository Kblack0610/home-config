#!/usr/bin/env python3
"""Offline test for replay.py: canned series, no VictoriaMetrics. A LAN host that runs hot
for half an hour while jobs wait gets its slots withheld one per cooldown, and back once
it cools. Stdlib only; CI runs it in the checks workflow."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replay  # noqa: E402  (sets TOPOLOGY before governor reads it)
import governor  # noqa: E402

END = 1_800_000_000 // 60 * 60
START = END - 86400
HOT_FROM, HOT_TO = END - 7200, END - 5400
COOL = {"idle": 0.6, "psi": 0.05, "tctl": 60, "mem": 0.5, "disk": 0.5}
HOT = {**COOL, "idle": 0.1, "psi": 0.5}


ONLINE_FROM = END - 43200  # ms-a2's slot is up for the last 12 hours only


def fake_range(url, expr, start, end, cool=COOL):
    for k, q in governor.QUERIES.items():
        if expr == q:
            return {(("nodename", "ms-a2"),): {t: (HOT if HOT_FROM <= t < HOT_TO else cool)[k]
                                               for t in range(START, END, 60)}}
    if 'state=~"idle|running"' in expr:
        return {(("name", "ms-a2-unity-pool-1"),): {t: 1 for t in range(ONLINE_FROM, END, 60)}}
    if "github_actions_job_waiting" in expr:
        return {(("labels", "self-hosted,unity"),): {t: 1 for t in range(START, END, 60)}}
    return {}


class Replay(unittest.TestCase):
    def test_a_hot_host_withholds_per_cooldown_and_admits_when_cool(self):
        cfg = governor.load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
        with mock.patch.object(replay, "query_range", fake_range), mock.patch.object(replay.time, "time", lambda: END):
            st, minutes, slots, _ = replay.replay("http://x", 1, cfg)
        c = st["ms-a2"]
        self.assertEqual(minutes, 1440)
        self.assertEqual(c["withholds"], 3)  # 30 hot minutes at one withhold per 600 s
        self.assertEqual(c["admits"], 3)
        self.assertEqual(c["held_max"], 3)
        # Held while jobs waited and not hot, split by the line that held it: here only cooldowns.
        causes = {k: v for k, v in c.items() if k.startswith("for nothing ")}
        self.assertEqual(sum(causes.values()), c["slot_minutes_held_for_nothing"])
        self.assertGreater(causes.get("for nothing cooldown", 0), 0)
        # Coverage counts only the minutes a slot was online.
        self.assertEqual(c["online"], 720)
        self.assertEqual(c["coverage"], 720)
        # A host with no readings never acts.
        self.assertEqual(st["deskslim-2"]["withholds"], 0)
        self.assertEqual(st["deskslim-2"]["coverage"], 0)

    def test_slots_held_in_the_disk_band_are_held_for_a_reason(self):
        cfg = governor.load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
        band = {**COOL, "disk": 0.22}  # between the withhold (0.2) and admit (0.25) lines

        def fake(url, expr, start, end):
            return fake_range(url, expr, start, end, cool=band)
        with mock.patch.object(replay, "query_range", fake), mock.patch.object(replay.time, "time", lambda: END):
            st, _, _, _ = replay.replay("http://x", 1, cfg)
        c = st["ms-a2"]
        self.assertEqual(c["admits"], 0)  # never ROOM again
        self.assertGreater(c["slot_minutes_held"], 3 * 80)  # 3 slots held for the last 90 minutes
        self.assertEqual(c["slot_minutes_held_for_nothing"], 0)

    def test_baseline_finds_no_difference_in_itself_and_one_in_a_broken_copy(self):
        cfg = governor.load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
        self.assertEqual(replay.synthetic(cfg, replay.load_baseline(governor.__file__), 300), [])
        broken = replay.load_baseline(governor.__file__)
        broken.pin_rule = lambda *a: None  # the pins pass removed
        self.assertTrue(replay.synthetic(cfg, broken, 300))


if __name__ == "__main__":
    unittest.main()
