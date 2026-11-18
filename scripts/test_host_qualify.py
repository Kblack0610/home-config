"""host-qualify's verdict and parsers, with no host, Prometheus or Loki."""

import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest import mock

import yaml

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

    def test_instance_by_name_then_inventory_address(self):
        # The returned name is the node name, which Loki's journal streams carry as `host`.
        known = {"razer-laptop": "192.168.1.26:9100", "raspberrypi": "192.168.1.20:9100",
                 "raspberrypi-771be84c": "192.168.1.23:9100", "workstation": "192.168.1.2:9100"}
        hi = qualify.host_instance
        self.assertEqual(hi("razer-laptop", {"ansible_host": "192.168.1.26"}, known),
                         ("razer-laptop", "192.168.1.26:9100"))
        self.assertEqual(hi("raspberrypi", None, known), ("raspberrypi", "192.168.1.20:9100"))
        self.assertEqual(hi("pi5-master", {"ansible_host": "192.168.1.20"}, known),
                         ("raspberrypi", "192.168.1.20:9100"))
        self.assertEqual(hi("pi5-worker3", {"ansible_host": "192.168.1.23"}, known),
                         ("raspberrypi-771be84c", "192.168.1.23:9100"))
        self.assertIsNone(hi("pi5-master", {"ansible_host": "192.168.1.3"}, known))
        self.assertIsNone(hi("nowhere", None, known))
        # The workstation is a local connection with no ansible_host, only fleet_ssh_host.
        with mock.patch("socket.gethostbyname", return_value="192.168.1.2"):
            self.assertEqual(hi("cachyos-x8664-main", {"fleet_ssh_host": "pc-home-cachy-main.lan"}, known),
                             ("workstation", "192.168.1.2:9100"))
        with mock.patch("socket.gethostbyname", side_effect=OSError):
            self.assertIsNone(hi("cachyos-x8664-main", {"fleet_ssh_host": "gone.lan"}, known))

    def test_postmortem_resolves_inventory_names(self):
        # 2026-11-17: pi5-master and cachyos-x8664-main both failed with no cc:host_instance series.
        series = [{"metric": {"host": "raspberrypi", "instance": "192.168.1.20:9100"}},
                  {"metric": {"host": "workstation", "instance": "192.168.1.2:9100"}}]
        pm = qualify.postmortem
        with mock.patch.object(pm, "prom", return_value=series), \
             mock.patch("socket.gethostbyname", side_effect=lambda a: "192.168.1.2" if a.endswith(".lan") else a):
            at = pm.parse_time("now")
            self.assertEqual(pm.instance_of("pi5-master", at), ("raspberrypi", "192.168.1.20:9100"))
            self.assertEqual(pm.instance_of("cachyos-x8664-main", at), ("workstation", "192.168.1.2:9100"))
            self.assertEqual(pm.instance_of("raspberrypi", at), ("raspberrypi", "192.168.1.20:9100"))
            with self.assertRaises(SystemExit):
                pm.instance_of("pi5-nope", at)

    def test_kernel_check_queries_loki_by_node_name(self):
        exprs = []

        def loki(path, **params):
            exprs.append(params["query"])
            return {"result": []}

        headroom = mock.Mock(**{"verdict.return_value": ("ROOM", ""), "line.return_value": "x ok",
                                "scalar.return_value": None})
        with mock.patch.object(qualify, "headroom", headroom), \
                mock.patch.object(qualify.postmortem, "loki", loki), \
                mock.patch.object(qualify.postmortem, "fault_rules", lambda: [("HostSoftLockup", "soft lockup")]), \
                mock.patch.object(qualify, "ssh", lambda host, script, timeout: "===missing\n"):
            checks = qualify.passive_checks("pi5-worker3", "raspberrypi-771be84c", "192.168.1.23:9100")
        self.assertTrue(exprs)
        self.assertTrue(all('host="raspberrypi-771be84c"' in e for e in exprs), exprs)
        kernel = next(c for c in checks if c["name"].startswith("kernel faults"))
        self.assertIn("raspberrypi-771be84c", kernel["detail"])

    def test_fault_rules_read_both_line_filters(self):
        # The ruler returns each query as written in apps/loki/rules-host-kernel-faults.yaml.
        # A |= rule (#1181) once crashed the parser, and with it every host-qualify run.
        lit = ('label_replace(sum by (host) (count_over_time({transport="kernel"} '
               '|= "FILE CORRUPTED" [10m])), "instance", "$1", "host", "(.*)") > 0')
        rx = ('sum by (host) (count_over_time({transport="kernel"} '
              '|~ "mce: \\\\[Hardware Error\\\\]|Machine check events logged" [10m])) > 0')
        groups = {"groups": [{"name": qualify.postmortem.RULE_GROUP, "rules": [
            {"name": "HostFsVerityCorruption", "query": lit}, {"name": "HostMachineCheck", "query": rx}]},
            {"name": "ups-usb", "rules": [{"name": "UpsUsbFlapping", "query": lit}]}]}
        with mock.patch.object(qualify.postmortem, "loki", lambda path, **params: groups):
            rules = dict(qualify.postmortem.fault_rules())
        self.assertEqual(set(rules), {"HostFsVerityCorruption", "HostMachineCheck"})
        self.assertRegex("verity: FILE CORRUPTED! pos=0", rules["HostFsVerityCorruption"])
        self.assertRegex("mce: [Hardware Error]: CPU 3", rules["HostMachineCheck"])
        self.assertNotRegex("mce: Hardware Error", rules["HostMachineCheck"])

    def test_every_committed_fault_rule_parses(self):
        # The rules as committed, so a rule shape the parser cannot read fails CI, not host-qualify.
        cm = yaml.safe_load((Path(__file__).parents[1] / "apps/loki/rules-host-kernel-faults.yaml").read_text())
        groups = yaml.safe_load(cm["data"]["host-kernel-faults.yaml"])["groups"]
        ruler = {"groups": [{"name": g["name"], "rules": [{"name": r["alert"], "query": r["expr"]}
                                                          for r in g["rules"]]} for g in groups]}
        with mock.patch.object(qualify.postmortem, "loki", lambda path, **params: ruler):
            rules = qualify.postmortem.fault_rules()
        self.assertIn("HostSoftLockup", dict(rules))
        self.assertNotIn("UpsUsbFlapping", dict(rules))  # a boot logs it once: not a host fault
        for name, regex in rules:
            re.compile(regex)

    def test_fault_rule_literal_is_unescaped_once(self):
        self.assertEqual(qualify.postmortem.line_filter_regex("X", '{a="b"} |= "say \\"hi\\" 90°C" [1m]'),
                         re.escape('say "hi" 90°C'))
        self.assertEqual(qualify.postmortem.line_filter_regex("X", '{a="b"} |~ `raw\\d+` [1m]'), "raw\\d+")

    def test_fault_rule_without_line_filter_fails(self):
        groups = {"groups": [{"name": qualify.postmortem.RULE_GROUP,
                              "rules": [{"name": "X", "query": 'count_over_time({transport="kernel"} [10m])'}]}]}
        with mock.patch.object(qualify.postmortem, "loki", lambda path, **params: groups), \
                self.assertRaises(SystemExit):
            qualify.postmortem.fault_rules()

    def test_sections_split_marked_output(self):
        text = "noise\n===fio 0\n{\"jobs\": []}\n===stress 2\nline\n===kernel\n"
        self.assertEqual(qualify.sections(text), [("fio 0", '{"jobs": []}'), ("stress 2", "line"), ("kernel", "")])


if __name__ == "__main__":
    unittest.main()
