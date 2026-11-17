"""Placement policy tests with isolated manifest and inventory fixtures."""

import contextlib
import datetime as dt
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

import yaml

from host_inventory import QUALIFICATION_KEY, load_inventory, qualification_path, tier_errors

NOW = dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc)


SPEC = importlib.util.spec_from_file_location("placement", Path(__file__).with_name("check-placement.py"))
placement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(placement)


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inventory = self.root / "inventory.yml"
        self.inventory.write_text("all:\n  children:\n    nodes:\n      hosts:\n        safe:\n          host_tier: core\n        unsafe:\n          host_tier: standard\n")
        self.hosts, _ = load_inventory(self.inventory)
        self.pod = {"containers": [{"name": "app", "image": "app"}],
                    "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data"}}]}

    def workload(self, criticality="high", pod=None, kind="Deployment"):
        doc = {"apiVersion": "apps/v1", "kind": kind,
               "metadata": {"name": "fixture", "annotations": {placement.CRITICALITY: criticality}},
               "spec": {"template": {"spec": pod or self.pod}}}
        return doc

    def errors(self, doc):
        return placement.check_documents([(self.root / "app.yaml", doc)], self.hosts)[0]

    def term(self, tier):
        return {"matchExpressions": [{"key": placement.TIER, "operator": "In", "values": [tier]}]}

    def affinity(self, terms):
        return {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": terms}}}

    def run_cli(self, document, *args):
        path = self.root / "app.yaml"
        path.write_text(yaml.safe_dump(document))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = placement.main(["--inventory", str(self.inventory), "--apps", str(path), *args])
        return rc, output.getvalue()

    def test_high_requires_core(self):
        self.assertIn("must require", self.errors(self.workload())[0])

    def test_core_node_selector_passes(self):
        self.pod["nodeSelector"] = {placement.TIER: "core"}
        self.assertEqual(self.errors(self.workload()), [])

    def test_required_core_affinity_passes(self):
        self.pod["affinity"] = self.affinity([self.term("core")])
        self.assertEqual(self.errors(self.workload()), [])

    def test_or_term_cannot_bypass_core(self):
        self.pod["affinity"] = self.affinity([self.term("core"), self.term("standard")])
        self.assertTrue(self.errors(self.workload()))

    def test_preferred_core_is_insufficient(self):
        self.pod["affinity"] = {"nodeAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": [
            {"weight": 100, "preference": self.term("core")}]}}
        self.assertTrue(self.errors(self.workload()))

    def test_core_selector_with_noncore_hostname_fails(self):
        self.pod["nodeSelector"] = {placement.TIER: "core", placement.HOSTNAME: "unsafe"}
        self.assertIn("pins unsafe", self.errors(self.workload())[0])

    def test_node_name_cannot_bypass_tier_selection(self):
        self.pod["nodeSelector"] = {placement.TIER: "core"}
        self.pod["nodeName"] = "unsafe"
        self.assertIn("pins unsafe", self.errors(self.workload())[0])

    def test_core_selector_with_noncore_hostname_affinity_fails(self):
        self.pod["nodeSelector"] = {placement.TIER: "core"}
        term = {"matchExpressions": [{"key": placement.HOSTNAME, "operator": "In", "values": ["unsafe"]}]}
        self.pod["affinity"] = self.affinity([term])
        self.assertIn("pins unsafe", self.errors(self.workload())[0])

    def test_hostname_pin_alone_is_not_tier_selection(self):
        self.pod["nodeSelector"] = {placement.HOSTNAME: "safe"}
        self.assertIn("must require", self.errors(self.workload())[0])

    def test_standard_pvc_needs_no_core(self):
        self.assertEqual(self.errors(self.workload("standard")), [])

    def test_missing_declaration_fails(self):
        doc = self.workload()
        del doc["metadata"]["annotations"]
        self.assertIn("must declare", self.errors(doc)[0])

    def test_hostpath_persistence_requires_declaration(self):
        self.pod["volumes"] = [{"hostPath": {"path": "/mnt/media/database"}}]
        self.assertIn("must declare", self.errors(self.workload(None))[0])

    def test_system_hostpaths_do_not_require_declaration(self):
        self.pod["volumes"] = [{"hostPath": {"path": "/etc/localtime"}}, {"hostPath": {"path": "/dev/dri"}}]
        self.assertEqual(self.errors(self.workload(None)), [])

    def test_application_hostpath_under_etc_is_stateful(self):
        self.pod["volumes"] = [{"hostPath": {"path": "/etc/my-app/state"}}]
        self.assertIn("must declare", self.errors(self.workload(None))[0])

    def test_statefulset_volume_claim_template_requires_declaration(self):
        doc = self.workload(None, {"containers": [{"name": "db"}]}, "StatefulSet")
        doc["spec"]["volumeClaimTemplates"] = [{"metadata": {"name": "db"}}]
        self.assertIn("must declare", self.errors(doc)[0])

    def test_cronjob_nested_pvc_is_checked(self):
        doc = self.workload(None)
        doc["kind"] = "CronJob"
        doc["spec"] = {"jobTemplate": {"spec": doc["spec"]}}
        self.assertIn("must declare", self.errors(doc)[0])

    def test_high_annotation_checks_even_without_local_storage(self):
        self.assertTrue(self.errors(self.workload(pod={"containers": [{"name": "writer"}]})))

    def test_helm_explicit_persistence_is_checked(self):
        doc = {"kind": "HelmRelease", "metadata": {"name": "chart"}, "spec": {
            "values": {"db": {"persistence": {"enabled": True}, "podAnnotations": {placement.CRITICALITY: "high"}}}}}
        self.assertIn("values.db", self.errors(doc)[0])

    def test_helm_disabled_storage_is_ignored(self):
        values = {"db": {"enabled": False, "persistence": {"enabled": True}},
                  "cache": {"persistence": {"enabled": False}},
                  "lapi": {"persistentVolume": {"data": {"enabled": False}, "config": {"enabled": False}}}}
        self.assertEqual(list(placement.chart_components(values)), [])

    def test_helm_persistent_volume_map_checked_at_owner(self):
        values = {"lapi": {"persistentVolume": {"data": {"enabled": True}},
                           "podAnnotations": {placement.CRITICALITY: "standard"}}}
        self.assertEqual([(name, criticality) for name, _, criticality in placement.chart_components(values)],
                         [("values.lapi", "standard")])

    def test_helm_direct_persistent_volume_requires_declaration(self):
        doc = {"kind": "HelmRelease", "metadata": {"name": "chart"}, "spec": {
            "values": {"db": {"persistentVolume": {"enabled": True, "size": "1Gi"}}}}}
        self.assertIn("values.db", self.errors(doc)[0])
        self.assertIn("must declare", self.errors(doc)[0])

    def test_helm_direct_persistent_volume_checks_high_placement(self):
        doc = {"kind": "HelmRelease", "metadata": {"name": "chart"}, "spec": {
            "values": {"db": {"persistentVolume": {"enabled": True, "size": "1Gi"},
                              "podAnnotations": {placement.CRITICALITY: "high"},
                              "nodeSelector": {placement.TIER: "standard"}}}}}
        self.assertIn("must require", self.errors(doc)[0])

    def test_helm_disabled_direct_persistent_volume_is_ignored(self):
        values = {"db": {"persistentVolume": {"enabled": False, "size": "1Gi"}}}
        self.assertEqual(list(placement.chart_components(values)), [])

    def test_monitoring_storage_keeps_component_placement(self):
        component = {"storageSpec": {"volumeClaimTemplate": {"spec": {"storageClassName": "local-path"}}},
                     "podAnnotations": {placement.CRITICALITY: "high"}, "nodeSelector": {placement.TIER: "core"}}
        doc = {"kind": "ChartValues", "values": {"prometheus": {"prometheusSpec": component}}}
        self.assertEqual(self.errors(doc), [])

    def test_report_keeps_policy_diagnostic_but_returns_zero(self):
        rc, output = self.run_cli(self.workload(), "--report")
        self.assertEqual(rc, 0)
        self.assertIn("REPORT:", output)
        self.assertIn("must require kblab.me/tier=core", output)

    def test_strict_planted_standard_node_fails(self):
        self.pod["nodeSelector"] = {placement.TIER: "standard"}
        rc, output = self.run_cli(self.workload())
        self.assertEqual(rc, 1)
        self.assertIn("must require kblab.me/tier=core", output)

    def test_empty_and_malformed_inputs_error_even_in_report(self):
        path = self.root / "app.yaml"
        for text in ("", "[invalid", "[]", "kind: Deployment\nspec: {}\n"):
            with self.subTest(text=text):
                path.write_text(text)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(placement.main(["--apps", str(path), "--inventory", str(self.inventory), "--report"]), 2)

    def test_empty_resource_set_does_not_pass(self):
        rc, output = self.run_cli({"kind": "Kustomization", "resources": []})
        self.assertEqual(rc, 2)
        self.assertIn("no resources", output)

    def test_kustomization_follows_active_resources_only(self):
        (self.root / "kustomization.yaml").write_text("kind: Kustomization\nresources: [active.yaml]\n")
        (self.root / "active.yaml").write_text(yaml.safe_dump(self.workload("standard")))
        (self.root / "excluded.yaml").write_text("[malformed")
        documents = placement.active_documents(self.root)
        self.assertEqual([path.name for path, _ in documents], ["active.yaml"])

    def test_missing_inventory_tier_is_input_error(self):
        self.inventory.write_text("all:\n  hosts:\n    unclassified: {}\n")
        rc, output = self.run_cli(self.workload("standard"), "--report")
        self.assertEqual(rc, 2)
        self.assertIn("host_tier", output)

    def test_inventory_aggregates_hostvars_and_recursive_groups(self):
        self.inventory.write_text("all:\n  children:\n    linux:\n      hosts:\n        one:\n          host_tier: core\n    cluster:\n      children:\n        workers:\n          hosts:\n            one:\n              address: localhost\n")
        hosts, groups = load_inventory(self.inventory)
        self.assertEqual(tier_errors(hosts), [])
        self.assertEqual(hosts["one"], {"host_tier": "core", "address": "localhost"})
        self.assertEqual(groups["all"], {"one"})
        self.assertEqual(groups["cluster"], {"one"})

    def test_inventory_child_reference_before_declaration(self):
        self.inventory.write_text("all:\n  children:\n    cluster:\n      children:\n        workers: {}\n    workers:\n      hosts:\n        one:\n          host_tier: standard\n")
        _, groups = load_inventory(self.inventory)
        self.assertEqual(groups["cluster"], {"one"})

    def test_inventory_rejects_conflicting_tiers(self):
        self.inventory.write_text("all:\n  children:\n    first:\n      hosts:\n        one:\n          host_tier: standard\n    second:\n      hosts:\n        one:\n          host_tier: core\n")
        with self.assertRaisesRegex(ValueError, "conflicting host_tier"):
            load_inventory(self.inventory)

    def qualify(self, host, qualified_for, age_days):
        path = qualification_path(self.root / "host_vars", host)
        path.parent.mkdir(parents=True, exist_ok=True)
        date = NOW - dt.timedelta(days=age_days)
        path.write_text(yaml.safe_dump({QUALIFICATION_KEY: {
            "date": date.strftime("%Y-%m-%dT%H:%M:%SZ"), "qualified_for": qualified_for, "checks": []}}))

    def qualification(self):
        return placement.qualification_errors(self.hosts, self.root / "host_vars", NOW)

    def test_core_without_record_is_refused(self):
        self.assertIn("safe: core tier has no qualification record", self.qualification()[0])

    def test_core_with_fresh_core_record_passes(self):
        self.qualify("safe", "core", 29)
        self.assertEqual(self.qualification(), [])

    def test_core_with_stale_record_is_refused(self):
        self.qualify("safe", "core", 31)
        self.assertIn("older than 30 days", self.qualification()[0])

    def test_core_with_standard_record_is_refused(self):
        self.qualify("safe", "standard", 1)
        self.assertIn("qualifies it for standard", self.qualification()[0])

    def test_standard_host_needs_no_record(self):
        self.qualify("safe", "core", 1)
        self.assertEqual(self.qualification(), [])  # "unsafe" is standard and has none

    def test_strict_cli_fails_on_unqualified_core_host(self):
        rc, output = self.run_cli(self.workload("standard"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL: safe: core tier has no qualification record", output)


if __name__ == "__main__":
    unittest.main()
