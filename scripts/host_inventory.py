"""Read the inventory's host variables and group memberships for offline checks."""

import socket
from pathlib import Path

import yaml


TIERS = {"core", "standard", "probation"}


def cpu_set(spec):
    """{5, 6, 7, 13} from a cpuset like "5-7,13"."""
    out = set()
    for part in str(spec).split(","):
        a, _, b = part.strip().partition("-")
        out |= set(range(int(a), int(b or a) + 1))
    return out


def load_inventory(path):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("all"), dict):
        raise ValueError(f"{path}: expected an all inventory group")
    hosts, groups, children = {}, {}, {}

    def visit(name, group, parents, inherited):
        if not isinstance(group, dict):
            raise ValueError(f"{path}: invalid inventory group {name}")
        members = groups.setdefault(name, set())
        defaults = inherited | (group.get("vars") or {})
        for host, values in (group.get("hosts") or {}).items():
            if values is not None and not isinstance(values, dict):
                raise ValueError(f"{path}: invalid host variables for {host}")
            previous = hosts.get(host, {}).get("host_tier")
            declared = (values or {}).get("host_tier")
            if previous is not None and declared is not None and previous != declared:
                raise ValueError(f"{path}: conflicting host_tier declarations for {host}")
            hosts[host] = defaults | hosts.get(host, {}) | (values or {})
            members.add(host)
            for parent in parents:
                groups[parent].add(host)
        for child, value in (group.get("children") or {}).items():
            children.setdefault(name, set()).add(child)
            visit(child, value or {}, parents + [name], defaults)

    visit("all", data["all"], [], {})
    if not hosts:
        raise ValueError(f"{path}: parsed no hosts")
    # Ansible permits an empty child-group reference before its declaration.
    changed = True
    while changed:
        changed = False
        for name, descendants in children.items():
            members = set().union(*(groups[child] for child in descendants))
            if not members.issubset(groups[name]):
                groups[name].update(members)
                changed = True
    return hosts, groups


def host_instance(host, values, known):
    """(cc:host_instance host, scrape instance) for a host, or None.

    `known` maps each cc:host_instance host to its instance. That name is the k8s node name for
    a k3s node and the Endpoints hostname for a static target, and also the Loki journal `host`
    (apps/alloy/config.alloy). It is not always the inventory name (pi5-master is raspberrypi,
    cachyos-x8664-main is workstation), so an inventory host is found by its address:
    ansible_host, else fleet_ssh_host (the workstation, a local connection, has only that).
    """
    if host in known:
        return host, known[host]
    addr = (values or {}).get("ansible_host") or (values or {}).get("fleet_ssh_host")
    try:
        ip = socket.gethostbyname(addr) if addr else None
    except OSError:
        return None
    return next(((name, inst) for name, inst in known.items() if ip and inst.rsplit(":", 1)[0] == ip), None)


def tier_errors(hosts):
    return [f"{host}: host_tier must be one of {', '.join(sorted(TIERS))}"
            for host, values in sorted(hosts.items()) if values.get("host_tier") not in TIERS]


# The record scripts/host-qualify.py writes and check-placement.py reads.
TIER_ORDER = ("probation", "standard", "core")
QUALIFICATION_DAYS = 30
QUALIFICATION_KEY = "host_qualification"


def qualification_path(host_vars, host):
    return Path(host_vars) / host / "qualification.yml"


def load_qualification(host_vars, host):
    path = qualification_path(host_vars, host)
    if not path.is_file():
        return None
    record = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(QUALIFICATION_KEY)
    if not isinstance(record, dict):
        raise ValueError(f"{path}: expected a {QUALIFICATION_KEY} mapping")
    return record
