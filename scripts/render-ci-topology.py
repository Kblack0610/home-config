#!/usr/bin/env python3
"""Render apps/ci-topology/topology.json, every CI slot and what it can run, from ansible/inventory.yml.

The inventory is the one place a person writes CI hosts and slots. The exporter (each slot's
expected row, so a slot whose runner never registered still shows as missing) and the
governor (which hosts it powers, their slot counts and slot groups) read the rendered file
from the ci-topology ConfigMap instead of keeping copies of their own.

Per host: site (cloud when unity_ci_lan is false, else local), full (unity_ci_full, Unity
hosts only) and its slots. Per slot: the runner name the play registers, its caps (the pools
it serves: light, heavy, unity, unity-build, macos, windows), the repo of a repo-scoped
runner, and for a build slot the test slots in its group (unity_ci_slot_groups: they share
its cores, so the governor never runs a build beside them).

Rendering also checks what the inventory must hold for the slots to fit their host:
- A group's test slots sit inside their build slot's cores, and their memory caps sum to no
  more than the build slot's, so a group never asks more of the host than its build slot alone.
- Two slots of a host share cores (ci_unity_slot_cpus) only inside one group.
- A unity-wide slot has at least WIDE_SLOT_MIN_GB.
- A build host gives RELEASE_LABEL to some of its build slots, never all (hc-263).
- A core host or a host in no_ci_hosts carries no CI, and the Forgejo runner's affinity
  excludes every one of them.

    render-ci-topology.py           write topology.json
    render-ci-topology.py --check   fail when topology.json is not what the inventory renders (the CI gate)
    --inventory PATH                another inventory (CI's negative controls)
"""

import argparse
import json
import os
import re
import sys

import yaml

from host_inventory import cpu_set, load_inventory, tier_errors

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")
OUT = os.path.join(REPO, "apps/ci-topology/topology.json")
FORGEJO_RUNNER = os.path.join(REPO, "apps/forgejo/runner-deployment.yaml")

# Provisional, low confidence (one kill; revisit when the unity-wide jobs stop building players in the job, playground
# ucp-1793): Soak's server build was killed at 16.96G anon in an aws-burst-2 16G slot on
# 2026-11-11 (ucp-1830); the wide slots that build green (razer-laptop, aws-burst-1 slot 1) have 20G.
WIDE_SLOT_MIN_GB = 20
RELEASE_LABEL = "unity-release"
NO_CI_GROUP = "no_ci_hosts"
CI_GROUPS = ("linux_ci_runners", "unity_ci_runners", "unity_ci_mac_runners", "ci_hosts")
UNITY_GROUPS = ("unity_ci_runners", "unity_ci_mac_runners")
# The inventory keys this reads; a Jinja value in one would render as its literal text.
KEYS = ("ci_light_slots", "ci_heavy_slots", "ci_repo_runners", "ci_pinned_runners", "unity_ci_slots",
        "unity_ci_build_slots", "unity_ci_slot_groups", "unity_ci_lan", "unity_ci_full", "unity_ci_wide",
        "unity_ci_slot_labels", "ci_unity_slot_cpus", "ci_unity_slot_memory_max")


def gigabytes(val):
    m = re.fullmatch(r"(\d+)G", str(val))
    return int(m.group(1)) if m else None


def unity_slot(host, n):
    return f"{host}-unity-pool-{n}"


def render(hosts, groups):
    """{"hosts": {host: {"site", "full"?, "slots": [...]}}} and the failures that stop a render."""
    fails, topo = [], {}
    for host, v in sorted(hosts.items()):
        for k in KEYS:
            if "{{" in json.dumps(v.get(k, "")):
                fails.append(f"{host}: {k} is templated; the topology needs a literal value")
    if fails:
        return {}, fails
    for host in sorted(hosts):
        v, slots = hosts[host], []
        if host in groups.get("linux_ci_runners", ()):
            for kind in ("light", "heavy"):
                slots += [{"name": f"{host}-{kind}-{n}", "caps": [kind]} for n in range(1, int(v.get(f"ci_{kind}_slots", 0)) + 1)]
            slots += [{"name": f"{host}-{r['slot']}", "caps": ["light"], "repo": r["repo"]} for r in v.get("ci_repo_runners", [])]
        unity = any(host in groups.get(g, ()) for g in UNITY_GROUPS)
        if unity:
            total, build, group = unity_layout(v)
            if build and set(group) != set(range(1, build + 1)):
                fails.append(f"{host}: unity_ci_slot_groups keys are {sorted(group)}, its build slots 1-{build}")
            if group and not build:
                fails.append(f"{host}: unity_ci_slot_groups, but no unity_ci_build_slots")
            seen = set()
            for b, tests in sorted(group.items()):
                for t in tests:
                    if not build < t <= total:
                        fails.append(f"{host}: group {b} names {t}, not a test slot ({build + 1}-{total})")
                    elif t in seen:
                        fails.append(f"{host}: test slot {t} is in two groups")
                    seen.add(t)
            for n in range(1, total + 1):
                slot = {"name": unity_slot(host, n), "caps": ["unity-build" if n <= build else "unity"]}
                if group.get(n):
                    slot["group"] = [unity_slot(host, t) for t in group[n]]
                slots.append(slot)
        slots += [{"name": p["name"], "caps": [p["cap"]], **({"repo": p["repo"]} if p.get("repo") else {})}
                  for p in v.get("ci_pinned_runners", [])]
        if slots:
            topo[host] = {"site": "cloud" if v.get("unity_ci_lan", True) is False else "local",
                          **({"full": v.get("unity_ci_full", True) is not False} if unity else {}),
                          "slots": slots}
    names = [s["name"] for h in topo.values() for s in h["slots"]]
    fails += [f"slot {n} is declared twice" for n in sorted({n for n in names if names.count(n) > 1})]
    return {"hosts": topo}, fails


def unity_layout(v):
    """A host's Unity slot count, build slot count and slot groups (build slot -> its test slots)."""
    group = {int(b): [int(t) for t in ts] for b, ts in (v.get("unity_ci_slot_groups") or {}).items()}
    return int(v.get("unity_ci_slots", 0)), int(v.get("unity_ci_build_slots", 0)), group


def slot_budget(v, total):
    """One core set per Unity slot ([] without ci_unity_slot_cpus) and one memory cap in G per slot."""
    cpus = [cpu_set(c) for c in v.get("ci_unity_slot_cpus") or []]
    mem = v.get("ci_unity_slot_memory_max")
    mem = [gigabytes(m) for m in mem] if isinstance(mem, list) else [gigabytes(mem) if mem else None] * total
    return cpus, mem


def fit_failures(hosts, groups):
    """Where a host's slots do not fit it: group cores and memory, shared cores, wide memory, release slots."""
    fails = []
    for host in sorted(set().union(*(groups.get(g, set()) for g in UNITY_GROUPS))):
        v = hosts[host]
        total, build, group = unity_layout(v)
        cpus, mem = slot_budget(v, total)
        if group and (len(cpus) != total or len(mem) != total):
            fails.append(f"{host}: slot groups need {total} ci_unity_slot_cpus and ci_unity_slot_memory_max entries")
            continue
        for b, tests in sorted(group.items()):
            tests = [t for t in tests if build < t <= total]
            fails += [f"{host}: test slot {t}'s cores are not inside build slot {b}'s"
                      for t in tests if not cpus[t - 1] <= cpus[b - 1]]
            used = [mem[t - 1] for t in tests]
            if None in used or mem[b - 1] is None:
                fails.append(f"{host}: group {b}'s memory caps are not in whole G")
            elif sum(used) > mem[b - 1]:
                fails.append(f"{host}: group {b}'s test slots take {sum(used)}G, over build slot {b}'s {mem[b - 1]}G")
        grouped = {(b, t) for b, ts in group.items() for t in ts}
        fails += [f"{host}: slots {i} and {j} share cores and are not one slot group"
                  for i in range(1, len(cpus) + 1) for j in range(i + 1, len(cpus) + 1)
                  if cpus[i - 1] & cpus[j - 1] and (i, j) not in grouped]
        labels = {int(k): ls for k, ls in (v.get("unity_ci_slot_labels") or {}).items()}
        wide = [n for n in range(1, total + 1) if n > build and v.get("unity_ci_wide")
                or "unity-wide" in labels.get(n, [])]
        caps = [mem[n - 1] if n <= len(mem) else None for n in wide]
        if wide and (None in caps or min(caps) < WIDE_SLOT_MIN_GB):
            have = "unset or not in whole G" if None in caps else f"{min(caps)}G"
            fails.append(f"{host} carries unity-wide, but its ci_unity_slot_memory_max is {have}, "
                         f"under the {WIDE_SLOT_MIN_GB}G a player build in the job needs")
        release = sum(1 for n in range(1, build + 1) if RELEASE_LABEL in labels.get(n, []))
        if build and not 0 < release < build:
            fails.append(f"{host}: {release} of its {build} build slots (unity_ci_build_slots) carry {RELEASE_LABEL} "
                         f"in unity_ci_slot_labels; a release must have at least one and leave players.yml at least one")
    return fails


def no_ci_failures(hosts, groups, forgejo_runner):
    core = {host for host, variables in hosts.items() if variables.get("host_tier") == "core"}
    service = groups.get(NO_CI_GROUP, set())
    if not service:
        return [f"parsed no {NO_CI_GROUP} hosts; the parser is broken, not the inventory"]
    pinned = {host for host, variables in hosts.items() if variables.get("ci_pinned_runners")}
    fails = tier_errors(hosts)
    fails += [f"{host} is in {NO_CI_GROUP} and in {g}: a service host carries no CI"
              for g in CI_GROUPS for host in sorted(groups.get(g, set()) & service)]
    fails += [f"{host} has core tier and is in {g}: core hosts carry no CI"
              for g in CI_GROUPS for host in sorted(groups.get(g, set()) & core)]
    fails += [f"{host} carries pinned CI but is core or in {NO_CI_GROUP}" for host in sorted(pinned & (core | service))]
    with open(forgejo_runner, encoding="utf-8") as fh:
        runner = yaml.safe_load(fh)
    terms = runner["spec"]["template"]["spec"].get("affinity", {}).get("nodeAffinity", {}).get(
        "requiredDuringSchedulingIgnoredDuringExecution", {}).get("nodeSelectorTerms", [])
    if not terms:
        fails.append("Forgejo runner needs required affinity excluding core and no_ci_hosts")
    for term in terms:
        expressions = term.get("matchExpressions", [])
        if not any(e.get("key") == "kblab.me/tier" and e.get("operator") == "NotIn"
                   and "core" in e.get("values", []) for e in expressions):
            fails.append("Forgejo runner must exclude core tier in every required affinity term")
        excluded = {host for e in expressions
                    if e.get("key") == "kubernetes.io/hostname" and e.get("operator") == "NotIn"
                    for host in e.get("values", [])}
        fails += [f"{host} is in {NO_CI_GROUP}, but {os.path.relpath(forgejo_runner, REPO)} "
                  "does not exclude it in every required affinity term"
                  for host in sorted(service - excluded)]
    return fails


def dump(topo):
    """One slot per line, so a diff shows the slot that changed."""
    out = ['{',
           '  "generated_by": "scripts/render-ci-topology.py from ansible/inventory.yml: edit the inventory and re-run it",',
           '  "hosts": {']
    hosts = list(topo["hosts"].items())
    for i, (host, h) in enumerate(hosts):
        head = {k: v for k, v in h.items() if k != "slots"}
        out.append(f'    {json.dumps(host)}: {json.dumps(head)[:-1]}, "slots": [')
        out += [f"      {json.dumps(s)}{',' if j < len(h['slots']) - 1 else ''}" for j, s in enumerate(h["slots"])]
        out.append(f"    ]}}{',' if i < len(hosts) - 1 else ''}")
    return "\n".join(out + ["  }", "}", ""])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--check", action="store_true", help="fail when topology.json is stale")
    ap.add_argument("--inventory", default=INVENTORY)
    args = ap.parse_args()
    hosts, groups = load_inventory(args.inventory)
    topo, fails = render(hosts, groups)
    fails += fit_failures(hosts, groups) + no_ci_failures(hosts, groups, FORGEJO_RUNNER)
    if not fails and not any(s.get("group") for h in topo["hosts"].values() for s in h["slots"]):
        fails.append("rendered no slot group; the parser is broken, not the inventory")
    for f in fails:
        print(f"FAIL: {f}")
    if fails:
        return 1
    text = dump(topo)
    assert json.loads(text)["hosts"] == topo["hosts"]
    rel = os.path.relpath(OUT, REPO)
    slots = sum(len(h["slots"]) for h in topo["hosts"].values())
    if args.check:
        have = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if have != text:
            print(f"FAIL: {rel} is not what {os.path.relpath(args.inventory, REPO)} renders; "
                  f"run scripts/render-ci-topology.py and commit it")
            return 1
        print(f"ok: {rel} matches the inventory: {len(topo['hosts'])} hosts, {slots} slots; "
              f"no CI on {sorted(groups[NO_CI_GROUP])}")
        return 0
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {rel}: {len(topo['hosts'])} hosts, {slots} slots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
