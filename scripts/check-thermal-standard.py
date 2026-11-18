#!/usr/bin/env python3
"""Check that every x86 Linux host in the inventory meets the thermal standard.

The standard (ansible/roles/thermal-cap/README.md): every x86 Linux host is in
thermal_capped, through its child groups, or says why not with
`thermal_cap_exempt: <reason>`. A capped host the convergence check
(apps/ansible-runner/cronjob.yaml HOSTS) does not reach would never show
ConvergenceDrift when its cap is missing, so each must be on that list too,
except one Ansible manages over a local connection, which the runner cannot dial.
Exit 1 on a violation, 2 on an unreadable input.
"""

import argparse
from pathlib import Path
import re
import sys

import yaml

from host_inventory import load_inventory


REPO = Path(__file__).resolve().parent.parent
# Groups whose hosts are not x86 Linux: the role writes Linux x86 sysfs knobs.
NOT_X86_LINUX = ("rpi_hosts", "macos_hosts", "windows_hosts", "openwrt")
CAPPED = "thermal_capped"
EXEMPT = "thermal_cap_exempt"


def convergence_hosts(cronjob):
    match = re.search(r'^\s*HOSTS="([^"]*)"', cronjob.read_text(encoding="utf-8"), re.M)
    if not match:
        raise ValueError(f"{cronjob}: no HOSTS=\"...\" line")
    return {entry.split("=", 1)[0] for entry in match.group(1).split()}


def thermal_errors(hosts, groups, reached):
    capped = groups.get(CAPPED, set())
    skip = set().union(*(groups.get(name, set()) for name in NOT_X86_LINUX))
    errors = []
    for host in sorted(set(hosts) - skip):
        exempt = hosts[host].get(EXEMPT)
        if host in capped and exempt is not None:
            errors.append(f"{host} is in {CAPPED} and sets {EXEMPT}; drop one")
        elif host not in capped and not (isinstance(exempt, str) and exempt.strip()):
            errors.append(f"{host} is an x86 Linux host in no {CAPPED} group and has no {EXEMPT}: <reason>")
        elif host in capped and host not in reached and hosts[host].get("ansible_connection") != "local":
            errors.append(f"{host} is in {CAPPED} but not in the convergence check's HOSTS, so a missing cap never shows as drift")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=REPO / "ansible/inventory.yml")
    parser.add_argument("--cronjob", type=Path, default=REPO / "apps/ansible-runner/cronjob.yaml")
    args = parser.parse_args(argv)
    try:
        hosts, groups = load_inventory(args.inventory)
        if CAPPED not in groups:
            raise ValueError(f"{args.inventory}: no {CAPPED} group")
        errors = thermal_errors(hosts, groups, convergence_hosts(args.cronjob))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"FAIL: {error}")
    capped = len(groups[CAPPED])
    print(f"thermal standard: {capped} hosts capped; {len(errors)} violations")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
