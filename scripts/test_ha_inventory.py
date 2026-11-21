"""ha-inventory's join and view, on a fixture shaped like Home Assistant's websocket results."""

import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("inv", Path(__file__).with_name("ha-inventory.py"))
inv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inv)

DATA = {
    "config_entries/get": [
        {"entry_id": "e1", "domain": "esphome", "title": "Athom Plug V3 613888", "state": "loaded", "disabled_by": None},
        {"entry_id": "e2", "domain": "shelly", "title": "Shelly Plug US 1", "state": "loaded", "disabled_by": None},
        {"entry_id": "e3", "domain": "cast", "title": "Old Speaker", "state": "not_loaded", "disabled_by": "user"},
        {"entry_id": "e4", "domain": "tapo_rv30", "title": "Tapo", "state": "setup_retry", "disabled_by": None},
    ],
    "config/device_registry/list": [{"id": "d1", "name": "Athom Plug V3 613888", "name_by_user": None}],
    "config/entity_registry/list": [
        {"entity_id": "switch.plug_613888", "platform": "esphome", "config_entry_id": "e1", "device_id": "d1"},
        {"entity_id": "sensor.plug_613888_led", "platform": "esphome", "config_entry_id": "e1", "device_id": "d1",
         "disabled_by": "integration"},
        {"entity_id": "switch.shelly_1", "platform": "shelly", "config_entry_id": "e2"},
        {"entity_id": "media_player.old", "platform": "cast", "config_entry_id": "e3"},
        {"entity_id": "input_text.gone", "platform": "input_text", "config_entry_id": None},
    ],
    "get_states": [
        {"entity_id": "switch.plug_613888", "state": "unavailable", "attributes": {}},
        {"entity_id": "switch.shelly_1", "state": "on", "attributes": {}},
        {"entity_id": "media_player.old", "state": "unavailable", "attributes": {}},
        {"entity_id": "input_text.gone", "state": "unavailable", "attributes": {"restored": True}},
    ],
}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.rows = inv.join(DATA)
        self.by = {r["title"]: r for r in self.rows}

    def test_every_entry_and_yaml_platform_is_a_row(self):
        self.assertEqual(len(self.rows), 5)
        self.assertIn("input_text (YAML, no config entry)", self.by)

    def test_retired_plug_reads_unavailable(self):
        self.assertEqual([e["entity_id"] for e in inv.unavailable(self.by["Athom Plug V3 613888"])],
                         ["switch.plug_613888"])

    def test_disabled_integration_does_not_count_as_unavailable(self):
        self.assertEqual(inv.unavailable(self.by["Old Speaker"]), [])

    def test_restored_entry_is_an_orphan(self):
        ent = self.by["input_text (YAML, no config entry)"]["entities"][0]
        self.assertTrue(ent["orphan"])

    def test_view(self):
        out = inv.render(self.rows, show_entities=True)
        self.assertIn("Integrations: 5 (4 enabled, 1 disabled)", out)
        self.assertIn("disabled (user)", out)
        self.assertIn("Enabled but not loaded: 1\n  tapo_rv30: Tapo  (setup_retry)", out)
        self.assertIn("esphome / Athom Plug V3 613888: switch.plug_613888", out)
        self.assertIn("input_text / (orphaned: nothing provides it): input_text.gone", out)
        self.assertIn("sensor.plug_613888_led", out)
        self.assertNotIn("media_player.old\n", out.split("Unavailable")[1].split("Entities")[0])

    def test_grep_keeps_matching_entities_only(self):
        rows = inv.filtered(self.rows, "613888")
        self.assertEqual([r["title"] for r in rows], ["Athom Plug V3 613888"])
        self.assertEqual(len(inv.filtered(self.rows, "shelly_1")[0]["entities"]), 1)

    def test_long_device_list_is_shortened(self):
        self.assertIn("and 7 more", inv.short([f"s.{i}" for i in range(10)]))


if __name__ == "__main__":
    unittest.main()
