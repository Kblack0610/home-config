#!/usr/bin/env python3
"""Check active stateful workloads' declared criticality and required node tier.

Source manifests are followed through Kustomization.resources. Helm checks inspect
explicit persistence in values, including monitoring/helm-values.yaml; chart
defaults, generated manifests and patches require a rendered-chart audit.
"""

import argparse
from pathlib import Path
import sys

import yaml

from host_inventory import load_inventory, tier_errors


REPO = Path(__file__).resolve().parent.parent
CRITICALITY = "kblab.me/criticality"
TIER = "kblab.me/tier"
HOSTNAME = "kubernetes.io/hostname"
SYSTEM_PATHS = ("/dev", "/proc", "/sys", "/run", "/var/log")
SYSTEM_FILES = {"/etc/localtime", "/etc/timezone", "/etc/machine-id", "/etc/hostname", "/etc/resolv.conf"}


def read_documents(path):
    docs = [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc is not None]
    if any(not isinstance(doc, dict) for doc in docs):
        raise ValueError(f"{path}: expected YAML object documents")
    return docs


def active_documents(root):
    seen, documents = set(), []

    def visit(path):
        path = path.resolve()
        if path in seen:
            return
        seen.add(path)
        if path.is_dir():
            candidates = [path / name for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")]
            path = next((p for p in candidates if p.is_file()), None)
            if path is None:
                raise ValueError(f"{candidates[0].parent}: no Kustomization")
        for doc in read_documents(path):
            if doc.get("kind") == "Kustomization":
                for resource in doc.get("resources", []):
                    if not isinstance(resource, str) or "://" in resource:
                        raise ValueError(f"{path}: only local resource paths can be checked")
                    visit(path.parent / resource)
                # Monitoring is installed by Helm using this values file, outside
                # the Kustomization's resource list.
                values = path.parent / "helm-values.yaml"
                if path.parent.name == "monitoring" and values.is_file():
                    documents.extend((values, {"kind": "ChartValues", "values": value})
                                     for value in read_documents(values))
            else:
                documents.append((path, doc))

    visit(root)
    if not documents:
        raise ValueError(f"{root}: parsed no resources")
    return documents


def pod_spec(doc):
    kind, spec = doc.get("kind"), doc.get("spec", {})
    if kind == "Pod":
        return spec
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}:
        return spec.get("template", {}).get("spec", {})
    if kind == "CronJob":
        return spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {})
    return None


def stateful(doc, pod):
    if doc.get("spec", {}).get("volumeClaimTemplates"):
        return True
    for volume in pod.get("volumes", []):
        if "persistentVolumeClaim" in volume:
            return True
        host = volume.get("hostPath", {}).get("path")
        if host and host not in SYSTEM_FILES and not any(
                host == prefix or host.startswith(prefix + "/") for prefix in SYSTEM_PATHS):
            return True
    return False


def annotations(obj):
    return (obj.get("annotations", {}) | obj.get("podAnnotations", {})
            | obj.get("metadata", {}).get("annotations", {}))


def placement_errors(pod, hosts):
    selector = pod.get("nodeSelector", {})
    terms = pod.get("affinity", {}).get("nodeAffinity", {}).get(
        "requiredDuringSchedulingIgnoredDuringExecution", {}).get("nodeSelectorTerms", [])
    reasons = []
    if selector.get(TIER) != "core":
        if not terms or not all(any(e.get("key") == TIER and e.get("operator") == "In"
                                   and e.get("values") == ["core"]
                                   for e in term.get("matchExpressions", [])) for term in terms):
            reasons.append("high criticality must require kblab.me/tier=core in every scheduling term")
    pins = set()
    if pod.get("nodeName"):
        pins.add(pod["nodeName"])
    if HOSTNAME in selector:
        pins.add(selector[HOSTNAME])
    for term in terms:
        for expr in term.get("matchExpressions", []):
            if expr.get("key") == HOSTNAME and expr.get("operator") == "In":
                pins.update(expr.get("values", []))
    aliases = {str(values.get("k3s_node_name", host)): values for host, values in hosts.items()}
    aliases.update(hosts)
    for pin in sorted(pins):
        if aliases.get(pin, {}).get("host_tier") != "core":
            reasons.append(f"high criticality pins {pin}, whose inventory tier is not core or is unknown")
    return reasons


def check_criticality(label, declared, pod, hosts):
    if declared not in {"high", "standard"}:
        return [f"{label}: stateful workload must declare {CRITICALITY}=high or standard"]
    if declared == "high":
        return [f"{label}: {reason}" for reason in placement_errors(pod, hosts)]
    return []


def persistent_values(value):
    if not isinstance(value, dict) or value.get("enabled") is False or value.get("deploy") is False:
        return False
    if "volumeClaimTemplate" in value:
        return True
    return any(persistent_values(child) for child in value.values() if isinstance(child, dict))


def chart_components(value, prefix="values", inherited=None):
    """Yield explicit storage owners, preserving their own scheduling fields."""
    if not isinstance(value, dict) or value.get("enabled") is False or value.get("deploy") is False:
        return
    declared = annotations(value).get(CRITICALITY, inherited)
    storage = value.get("persistentVolume", {})
    owns = isinstance(value.get("persistence"), dict) and value["persistence"].get("enabled") is not False
    owns = owns or (isinstance(storage, dict) and storage.get("enabled") is not False
                    and (storage.get("enabled") is True
                         or any(isinstance(child, dict) and child.get("enabled") is not False
                                for child in storage.values())))
    owns = owns or any(persistent_values(value.get(key)) for key in ("storage", "storageSpec"))
    if owns:
        yield prefix, value.get("pod", value), declared
    for key, child in value.items():
        if key not in {"persistence", "persistentVolume", "storage", "storageSpec"}:
            yield from chart_components(child, f"{prefix}.{key}", declared)


def check_documents(documents, hosts):
    errors, checked, workloads = [], 0, 0
    for path, doc in documents:
        label = f"{path}:{doc.get('metadata', {}).get('name', doc.get('kind', '?'))}"
        pod = pod_spec(doc)
        if pod is not None:
            workloads += 1
            if not pod:
                raise ValueError(f"{label}: empty pod spec")
            declared = annotations(doc).get(CRITICALITY)
            if stateful(doc, pod) or declared is not None:
                checked += 1
                errors.extend(check_criticality(label, declared, pod, hosts))
        if doc.get("kind") in {"HelmRelease", "ChartValues"}:
            workloads += 1
            values = doc.get("values", {}) if doc["kind"] == "ChartValues" else doc.get("spec", {}).get("values", {})
            for component, placement, declared in chart_components(values, inherited=annotations(doc).get(CRITICALITY)):
                checked += 1
                errors.extend(check_criticality(f"{label}:{component}", declared, placement, hosts))
    if not workloads:
        raise ValueError("parsed no workloads; placement check cannot pass vacuously")
    return errors, checked, workloads


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", type=Path, default=REPO / "apps")
    parser.add_argument("--inventory", type=Path, default=REPO / "ansible/inventory.yml")
    parser.add_argument("--report", action="store_true", help="report policy violations without gating during P1-P4")
    args = parser.parse_args(argv)
    try:
        hosts, _ = load_inventory(args.inventory)
        invalid = tier_errors(hosts)
        if invalid:
            raise ValueError("; ".join(invalid))
        errors, checked, workloads = check_documents(active_documents(args.apps), hosts)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"{'REPORT' if args.report else 'FAIL'}: {error}")
    print(f"placement: checked {checked} stateful or declared workloads of {workloads}; {len(errors)} violations")
    return 1 if errors and not args.report else 0


if __name__ == "__main__":
    sys.exit(main())
