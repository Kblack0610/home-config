#!/usr/bin/env python3
"""Assert github-runner-exporter's RUNNER_ROSTER matches the ansible inventory's slot counts.

The exporter's denominator ("how many runners SHOULD be online") has to be written
down independently of the GitHub API, because the API only lists runners that
registered and a slot whose play never ran is exactly what the dashboard exists to
show. So it is a copy of the slot counts in ansible/inventory.yml
(platform_ci_runners pmp_light_slots / pmp_heavy_slots, unity_ci_runners
unity_ci_slots) plus the single mac-mini runner from the "macOS CI runner" play.

Same rule as check-fleet-roster.py: independent copies are the design, copies nobody
compares are the bug. This compares them in BOTH directions, because both break: a
host added to a pool and forgotten here reads as fully healthy, a host retired there
and left here reads as permanently short.

Stdlib only (runs in a bare python:alpine CI container), so inventory.yml is read by
indentation, not by a YAML library. Exits non-zero on drift, or if it found nothing
to compare - an empty parse must never pass.
"""

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")
EXPORTER_DEPLOY = os.path.join(REPO, "apps/github-runner-exporter/deployment.yaml")

# inventory group -> {slot var: exporter pool}
GROUPS = {
    "platform_ci_runners": {"pmp_light_slots": "pmp-light", "pmp_heavy_slots": "pmp-heavy"},
    "unity_ci_runners": {"unity_ci_slots": "unity"},
}
# The macOS runner is one play pinned to one host (playbooks/site.yml, "macOS CI
# runner (mac-mini only)"), not an inventory group with a slot var.
MAC = {("mac-mini", "macos"): 1}


def inventory_roster(path):
    out, group, in_hosts, host = {}, None, False, None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            key, _, val = line.strip().partition(":")
            if indent == 4:
                group, in_hosts, host = (key if key in GROUPS else None), False, None
            elif group and indent == 6:
                in_hosts, host = key == "hosts", None
            elif group and in_hosts and indent == 8:
                host = key
            elif group and host and indent == 10 and key in GROUPS[group]:
                out[(host, GROUPS[group][key])] = int(val.strip())
    return out


def exporter_roster(path):
    text = open(path, encoding="utf-8").read()
    m = re.search(r"name:\s*RUNNER_ROSTER\s*\n\s*value:\s*\"([^\"]*)\"", text)
    if not m:
        sys.exit(f"FAIL: no RUNNER_ROSTER value in {path}")
    out = {}
    for entry in m.group(1).split():
        host, pool, slots = entry.split(":")
        out[(host, pool)] = int(slots)
    return out


def main():
    want = inventory_roster(INVENTORY)
    want.update(MAC)
    have = exporter_roster(EXPORTER_DEPLOY)
    if len(want) <= len(MAC):
        sys.exit(f"FAIL: parsed no runner slots out of {INVENTORY}; the parser is broken, not the roster")
    rc = 0
    for key in sorted(set(want) | set(have)):
        w, h = want.get(key), have.get(key)
        if w != h:
            rc = 1
            print(f"FAIL: {key[0]} {key[1]}: inventory says {w}, RUNNER_ROSTER says {h}")
    if rc == 0:
        print(f"ok: {len(want)} host/pool slot counts agree")
    return rc


if __name__ == "__main__":
    sys.exit(main())
