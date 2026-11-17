"""host-qualify's verdict and parsers, with no host, Prometheus or Loki."""

import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("qualify", Path(__file__).with_name("host-qualify.py"))
qualify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualify)


def check(ok, gates):
    return qualify.check("x", ok, "", gates)


class QualifyTests(unittest.TestCase):
    def test_all_pass_is_core(self):
        self.assertEqual(qualify.qualified_for([check(True, qualify.STANDARD), check(True, qualify.CORE)]), "core")

    def test_core_only_failure_is_standard(self):
        self.assertEqual(qualify.qualified_for([check(True, qualify.STANDARD), check(False, qualify.CORE)]), "standard")

    def test_standard_failure_cannot_reach_core(self):
        self.assertEqual(qualify.qualified_for([check(False, qualify.STANDARD), check(True, qualify.CORE)]), "probation")

    def test_skipped_burn_in_cannot_qualify(self):
        # main() appends a failing standard-gated check when the burn-in does not run.
        self.assertEqual(qualify.qualified_for([check(True, qualify.CORE), check(False, qualify.STANDARD)]), "probation")

    def test_smart_passes_clean_nvme(self):
        body = json.dumps({"smart_status": {"passed": True},
                           "nvme_smart_health_information_log": {"critical_warning": 0, "media_errors": 0}})
        self.assertTrue(qualify.smart_check("nvme0n1", "nvme", body)["pass"])

    def test_smart_fails_on_media_errors_or_warning(self):
        for log in ({"critical_warning": 0, "media_errors": 3}, {"critical_warning": 4, "media_errors": 0}):
            body = json.dumps({"smart_status": {"passed": True}, "nvme_smart_health_information_log": log})
            self.assertFalse(qualify.smart_check("nvme0n1", "nvme", body)["pass"])

    def test_smart_fails_without_json(self):
        self.assertFalse(qualify.smart_check("sda", "sata", "smartctl: permission denied")["pass"])

    def test_release_needs_the_inventory_tier(self):
        self.assertTrue(qualify.releases("core", "core"))
        self.assertFalse(qualify.releases("standard", "core"))
        self.assertTrue(qualify.releases("standard", "standard"))

    def test_release_never_below_standard(self):
        # hp-victus is probation; a probation record (fault history) must not release it.
        self.assertFalse(qualify.releases("probation", "probation"))
        self.assertTrue(qualify.releases("standard", "probation"))

    def test_instance_by_name_then_inventory_ip(self):
        known = {"razer-laptop": "192.168.1.26:9100", "raspberrypi": "192.168.1.20:9100"}
        self.assertEqual(qualify.host_instance("razer-laptop", "192.168.1.26", known), "192.168.1.26:9100")
        self.assertEqual(qualify.host_instance("pi5-master", "192.168.1.20", known), "192.168.1.20:9100")
        self.assertIsNone(qualify.host_instance("pi5-master", "192.168.1.2", known))

    def test_sections_split_marked_output(self):
        text = "noise\n===fio 0\n{\"jobs\": []}\n===stress 2\nline\n===kernel\n"
        self.assertEqual(qualify.sections(text), [("fio 0", '{"jobs": []}'), ("stress 2", "line"), ("kernel", "")])


if __name__ == "__main__":
    unittest.main()
