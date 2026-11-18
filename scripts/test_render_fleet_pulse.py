"""The fleet_pulse group renders the gatus endpoints and the roster, and refuses what the bars cannot match."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml

SPEC = importlib.util.spec_from_file_location("fleet_pulse", Path(__file__).with_name("render-fleet-pulse.py"))
fleet_pulse = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fleet_pulse)

INVENTORY = {"all": {"children": {
    "k3s": {"hosts": {"node-a": {"ansible_host": "192.0.2.1"}}},
    "fleet_pulse": {
        "vars": {"fleet_pulse_groups": {"k3s": "k3s"}, "fleet_pulse_devices": [
            {"name": "work-mac", "group": "workplace", "display": "wm"},
            {"name": "phone", "group": "android", "roster": False, "display": "ph"},
        ]},
        "hosts": {
            "node-a": None,
            "desk": {"fleet_pulse_name": "desk-key", "fleet_pulse_group": "homelab",
                     "fleet_pulse_push": True, "fleet_pulse_display": "main"},
            "room": {"ansible_host": "192.0.2.2", "fleet_pulse_group": "audio",
                     "fleet_pulse_units": ["snapclient.service"]},
        },
    },
}}}


class RenderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "inventory.yml"
        self.inventory = json.loads(json.dumps(INVENTORY))

    def members(self):
        self.path.write_text(yaml.safe_dump(self.inventory, sort_keys=False))
        return fleet_pulse.members(str(self.path))

    def test_roster_counts_only_rostered_and_display_marks_unrostered(self):
        ms, groups = self.members()
        doc = json.loads(fleet_pulse.render_roster(ms, groups))
        self.assertEqual(doc["roster"], ["node-a", "desk-key", "room", "work-mac"])
        self.assertEqual(doc["display"], ["desk-key=main", "work-mac=wm", "~phone=ph", "@k3s=k3s"])

    def test_gatus_pushes_and_polls_by_ansible_host(self):
        ms, groups = self.members()
        self.assertEqual(fleet_pulse.failures(ms, groups), [])
        cfg = yaml.safe_load(fleet_pulse.render_gatus(ms, "metrics: true\n"))
        self.assertEqual([e["name"] for e in cfg["external-endpoints"]], ["desk-key", "work-mac", "phone"])
        polled = {e["name"]: e for e in cfg["endpoints"]}
        self.assertEqual(polled["node-a"]["url"], "http://192.0.2.1:9100/metrics")
        self.assertEqual(polled["node-a"]["group"], "k3s")
        self.assertIn('state="active"', polled["room"]["conditions"][1])

    def test_child_groups_join_in_file_order_once(self):
        self.inventory["all"]["children"]["k3s"]["hosts"]["node-b"] = {"ansible_host": "192.0.2.3"}
        self.inventory["all"]["children"]["fleet_pulse"]["children"] = {"k3s": None}
        ms, _ = self.members()
        self.assertEqual([m["name"] for m in ms][:4], ["node-a", "desk-key", "room", "node-b"])

    def test_refuses_names_the_bars_cannot_match(self):
        hosts = self.inventory["all"]["children"]["fleet_pulse"]["hosts"]
        hosts["desk"]["fleet_pulse_name"] = "Desk Key"
        hosts["room"]["fleet_pulse_name"] = "node-a"
        del self.inventory["all"]["children"]["k3s"]
        self.inventory["all"]["children"]["fleet_pulse"]["vars"]["fleet_pulse_groups"] = {"iot": "iot"}
        fails = fleet_pulse.failures(*self.members())
        self.assertTrue(any("not kebab-case" in f for f in fails), fails)
        self.assertTrue(any("declared twice" in f for f in fails), fails)
        self.assertTrue(any("no ansible_host" in f for f in fails), fails)
        self.assertTrue(any("fleet_pulse_groups names 'iot'" in f for f in fails), fails)

    def test_refuses_a_templated_value(self):
        self.inventory["all"]["children"]["k3s"]["hosts"]["node-a"]["ansible_host"] = "{{ lan_ip }}"
        fails = fleet_pulse.failures(*self.members())
        self.assertTrue(any("templated" in f for f in fails), fails)


if __name__ == "__main__":
    unittest.main()
