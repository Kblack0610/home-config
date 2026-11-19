#!/usr/bin/env python3
"""Fail when a CI topology fact is copied outside its home.

ansible/inventory.yml writes the CI hosts and their EC2 instances, and
scripts/render-ci-topology.py renders them into apps/ci-topology/topology.json, which every
reader loads. Two kinds of copy have drifted before and are refused anywhere else in the repo:
- an EC2 instance id (i- and 17 hex digits); config.json carried all four until hc-652
- a regex alternation of two topology hosts (deskslim-1|ms-a2, copy-guard: this example); ci-pool-idle.py hard-coded its
  hosts that way, and stopped seeing a host the inventory added

A line that must hold one anyway (a signing test vector) carries "copy-guard:" and the reason.

    check-topology-copies.py            scan every tracked file
    check-topology-copies.py PATH...    scan these files (CI's negative control)
"""

import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOMES = {"ansible/inventory.yml", "apps/ci-topology/topology.json"}
MARK = "copy-guard:"
INSTANCE_ID = re.compile(r"\bi-[0-9a-f]{17}\b")


def host_pair(hosts):
    """A regex that matches two topology host names joined by |, longest names first."""
    names = "|".join(re.escape(h) for h in sorted(hosts, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{names})\|(?:{names})(?![\w-])")


def copies(path, text, pair):
    """(line number, what) for each copy in one file's text."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if MARK in line:
            continue
        out += [(n, f"instance id {m.group(0)}") for m in INSTANCE_ID.finditer(line)]
        out += [(n, f"host alternation {m.group(0)}") for m in pair.finditer(line)]
    return out


def main(argv):
    with open(os.path.join(REPO, "apps/ci-topology/topology.json"), encoding="utf-8") as f:
        pair = host_pair(json.load(f)["hosts"])
    paths = argv or subprocess.run(["git", "ls-files"], cwd=REPO, check=True, capture_output=True,
                                   text=True).stdout.split()
    fails = 0
    for rel in paths:
        if rel in HOMES:
            continue
        try:
            with open(os.path.join(REPO, rel), encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for n, what in copies(rel, text, pair):
            print(f"FAIL: {rel}:{n}: {what}; read it from apps/ci-topology/topology.json (inventory: ansible/inventory.yml)")
            fails += 1
    if fails:
        return 1
    print(f"ok: no instance id or topology host alternation outside {sorted(HOMES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
