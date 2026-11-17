"""The quarantine trigger set and cap, with no cluster, Prometheus or Loki."""

import datetime as dt
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("quarantine", Path(__file__).with_name("quarantine.py"))
q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(q)

NOW = 1_800_000_000.0
HOUR = 3600


def node(name, ip="192.168.1.9", taint=False, **notes):
    n = {"metadata": {"name": name, "annotations": dict(notes)},
         "spec": {"taints": [{"key": q.TAINT, "value": "true", "effect": "NoSchedule"}] if taint else []},
         "status": {"addresses": [{"type": "InternalIP", "address": ip},
                                  {"type": "InternalIP", "address": "2603::1"},
                                  {"type": "Hostname", "address": name}]}}
    return n


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decide(nodes, rules=None, flaps=None, battery=()):
    return q.decide(nodes, rules or {}, flaps or {}, set(battery), NOW)


class TriggerTests(unittest.TestCase):
    def test_one_soft_lockup_or_mce_quarantines(self):
        for rule in ("HostSoftLockup", "HostMachineCheck"):
            got = decide([node("a")], {rule: {"a": [NOW - HOUR]}})
            self.assertEqual(got, {"a": [f"{rule} x1"]})

    def test_gpu_fault_needs_two_windows(self):
        self.assertEqual(decide([node("a")], {"HostGpuFirmwareFault": {"a": [NOW - HOUR]}}), {})
        got = decide([node("a")], {"HostGpuFirmwareFault": {"a": [NOW - 2 * HOUR, NOW - HOUR]}})
        self.assertEqual(got, {"a": ["HostGpuFirmwareFault x2"]})

    def test_flaps_need_two_ready_drops(self):
        self.assertEqual(decide([node("a")], flaps={"a": 1}), {})
        self.assertEqual(decide([node("a")], flaps={"a": 2}), {"a": ["Ready dropped 2x"]})

    def test_on_battery_maps_exporter_instance_to_node(self):
        nodes = [node("a", ip="192.168.1.243"), node("b", ip="192.168.1.26")]
        self.assertEqual(decide(nodes, battery=["192.168.1.243:9100"]), {"a": ["on battery"]})

    def test_battery_on_a_non_node_is_ignored(self):
        self.assertEqual(decide([node("a")], battery=["192.168.1.2:9100"]), {})

    def test_reasons_combine(self):
        got = decide([node("a")], {"HostSoftLockup": {"a": [NOW - 60]}}, {"a": 3})
        self.assertEqual(got, {"a": ["HostSoftLockup x1", "Ready dropped 3x"]})

    def test_fault_on_another_host_does_not_count(self):
        self.assertEqual(decide([node("a")], {"HostSoftLockup": {"cachyos": [NOW - 60]}}), {})


class LookbackTests(unittest.TestCase):
    def test_faults_before_requalification_do_not_count(self):
        n = node("a", **{q.QUALIFIED_AT: iso(NOW - HOUR)})
        self.assertEqual(decide([n], {"HostSoftLockup": {"a": [NOW - 2 * HOUR]}}), {})
        self.assertEqual(decide([n], {"HostSoftLockup": {"a": [NOW - 60]}}), {"a": ["HostSoftLockup x1"]})

    def test_hand_released_node_is_not_retainted_for_the_same_fault(self):
        # degraded-at stays after `kubectl taint node a kblab.me/degraded-`.
        n = node("a", **{q.DEGRADED_AT: iso(NOW - HOUR)})
        self.assertEqual(decide([n], {"HostSoftLockup": {"a": [NOW - 2 * HOUR]}}), {})

    def test_since_is_capped_at_the_lookback(self):
        self.assertEqual(q.since(node("a"), NOW), NOW - q.LOOKBACK_H * HOUR)
        old = node("a", **{q.QUALIFIED_AT: iso(NOW - 30 * 24 * HOUR)})
        self.assertEqual(q.since(old, NOW), NOW - q.LOOKBACK_H * HOUR)

    def test_tainted_node_is_skipped(self):
        self.assertEqual(decide([node("a", taint=True)], {"HostSoftLockup": {"a": [NOW - 60]}}), {})


class CapTests(unittest.TestCase):
    def test_cap_counts_nodes_already_tainted(self):
        nodes = [node("a", taint=True), node("b"), node("c")]
        act, held = q.capped(nodes, {"b": ["x"], "c": ["x"]})
        self.assertEqual((act, held), (["b"], ["c"]))

    def test_cap_reached_holds_everything(self):
        nodes = [node("a", taint=True), node("b", taint=True), node("c")]
        self.assertEqual(q.capped(nodes, {"c": ["x"]}), ([], ["c"]))


if __name__ == "__main__":
    unittest.main()
