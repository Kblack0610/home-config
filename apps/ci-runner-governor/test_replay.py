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


def fake_range(url, expr, start, end):
    for k, q in governor.QUERIES.items():
        if expr == q:
            return {(("nodename", "ms-a2"),): {t: (HOT if HOT_FROM <= t < HOT_TO else COOL)[k]
                                               for t in range(START, END, 60)}}
    if "github_actions_job_waiting" in expr:
        return {(("labels", "self-hosted,unity"),): {t: 1 for t in range(START, END, 60)}}
    return {}


class Replay(unittest.TestCase):
    def test_a_hot_host_withholds_per_cooldown_and_admits_when_cool(self):
        cfg = governor.load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
        with mock.patch.object(replay, "query_range", fake_range), mock.patch.object(replay.time, "time", lambda: END):
            st, minutes, slots = replay.replay("http://x", 1, cfg)
        c = st["ms-a2"]
        self.assertEqual(minutes, 1440)
        self.assertEqual(c["withholds"], 3)  # 30 hot minutes at one withhold per 600 s
        self.assertEqual(c["admits"], 3)
        self.assertEqual(c["held_max"], 3)
        # A host with no readings never acts.
        self.assertEqual(st["deskslim-2"]["withholds"], 0)
        self.assertEqual(st["deskslim-2"]["coverage"], 0)


if __name__ == "__main__":
    unittest.main()
