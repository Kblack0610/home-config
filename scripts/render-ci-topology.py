#!/usr/bin/env python3
"""Render apps/ci-topology/topology.json, every CI slot and what it can run, from ansible/inventory.yml.

The inventory is the one place a person writes CI hosts and slots. The exporter (each slot's
expected row, so a slot whose runner never registered still shows as missing) and the
governor (which hosts it powers, their slot counts and slot groups) read the rendered file
from the ci-topology ConfigMap instead of keeping copies of their own.

Per host: site (cloud when unity_ci_lan is false, else local), full (unity_ci_full, Unity
hosts only) and its slots. Per slot: the runner name the play registers, its caps (the job
kinds it takes: light, heavy, unity, unity-build, forgejo, macos, windows; HOST_CAPS says
which of the host's ci_caps each slot gets), the repo of a repo-scoped runner, the forge of a
slot that serves Forgejo (github otherwise), and for a build slot the test slots in its group. A
host's build slots are the keys of its unity_ci_slot_groups, each with the test slots that share
its cores (the governor never runs a build beside them), [] for a build slot with cores of its own.

Rendering also checks what the inventory must hold for the slots to fit their host:
- A group's test slots sit inside their build slot's cores, and their memory caps sum to no
  more than the build slot's, so a group never asks more of the host than its build slot alone.
- Two slots of a host share cores (ci_unity_slot_cpus) only inside one group.
- A unity-wide slot has at least WIDE_SLOT_MIN_GB.
- The pool's build slots carry RELEASE_LABEL on at least one and leave it off at least one,
  so a release always has a slot and never holds every slot players.yml builds on (hc-263).
- A core host or a host in no_ci_hosts carries no CI.
- Forgejo slots sit on LAN hosts only, and there are FORGEJO_SLOTS of them.

    render-ci-topology.py           write topology.json
    render-ci-topology.py --check   fail when topology.json is not what the inventory renders (the CI gate)
    --inventory PATH                another inventory (CI's negative controls)
"""

import argparse
import json
import os
import re
import sys

from host_inventory import cpu_set, load_inventory, tier_errors

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")
OUT = os.path.join(REPO, "apps/ci-topology/topology.json")

# Provisional, low confidence (one kill; revisit when the unity-wide jobs stop building players in the job, playground
# ucp-1793): Soak's server build was killed at 16.96G anon in an aws-burst-2 16G slot on
# 2026-11-11 (ucp-1830); the wide slots that build green (razer-laptop, aws-burst-1 slot 1) have 20G.
WIDE_SLOT_MIN_GB = 20
RELEASE_LABEL = "unity-release"
# Provisional, medium confidence: six Forgejo slots, two on ms-a2 (the capacity of the k3s runner
# pod they replaced, plan make-logical-possum phase 6) and two each on windows-media-ci and deskslim-2 since 2026-11-18,
# when the human asked for Forgejo on more than one host. Each slot serves one forge, so no lock or
# dispatcher shares a slot between forges. Revisit when Forgejo queue wait is over budget for
# 7 days while GitHub slots on the same host sit idle over 50%.
FORGEJO_SLOTS = 6
NO_CI_GROUP = "no_ci_hosts"
CI_GROUPS = ("linux_ci_runners", "unity_ci_runners", "unity_ci_mac_runners", "ci_hosts")
UNITY_GROUPS = ("unity_ci_runners", "unity_ci_mac_runners")
# The inventory keys this reads; a Jinja value in one would render as its literal text.
KEYS = ("ci_light_slots", "ci_heavy_slots", "ci_repo_runners", "ci_pinned_runners", "unity_ci_slots",
        "unity_ci_slot_groups", "unity_ci_lan", "unity_ci_full", "unity_ci_wide",
        "unity_ci_slot_labels", "ci_unity_slot_cpus", "ci_unity_slot_memory_max", "ci_caps", "forgejo_slots")
# What a host may be tagged for (ci_caps): each of its slots takes every job kind its host is
# tagged for that the slot can hold. A light or heavy slot also takes light jobs; a Unity test
# slot also takes SHARED_CAPS; a build slot takes builds alone, so a build never waits behind
# a small job. A forgejo slot serves Forgejo Actions alone (one forge per slot). Every light and heavy slot carries fresh-workspace (site.yml): its job-started
# hook gives each job an empty workspace, which the Unity repos' light jobs ask for, since on a
# Unity slot their checkout would clean the warm Library.
HOST_CAPS = ("light", "heavy", "unity", "unity-build", "forgejo")
SLOT_CAPS = {"light": ["light"], "heavy": ["heavy", "light"]}
# Heavy is shared since platform's heavy jobs, the only ones that ask for heavy, take ephemeral
# service and app ports (BlackNBrownStudios/platform#1592 on main, #1599 on develop), so two heavy
# jobs on one host no longer clash. Revisit if a heavy job fails on an address already in use.
SHARED_CAPS = ("light", "heavy")


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
        caps = list(v.get("ci_caps") or [])
        fails += [f"{host}: ci_caps names {c}, not one of {list(HOST_CAPS)}" for c in caps if c not in HOST_CAPS]
        if v.get("unity_ci_lan", True) is False and set(caps) & {*SHARED_CAPS, "forgejo"}:
            fails.append(f"{host}: a cloud host's ci_caps carry no light, heavy or forgejo, so a small job never powers a paid host")
        if host in groups.get("linux_ci_runners", ()):
            for kind in ("light", "heavy"):
                slots += [{"name": f"{host}-{kind}-{n}", "caps": SLOT_CAPS[kind]} for n in range(1, int(v.get(f"ci_{kind}_slots", 0)) + 1)]
            slots += [{"name": f"{host}-{r['slot']}", "caps": SLOT_CAPS["light"], "repo": r["repo"]} for r in v.get("ci_repo_runners", [])]
            slots += [{"name": f"{host}-forgejo-{n}", "caps": ["forgejo"], "forge": "forgejo"}
                      for n in range(1, int(v.get("forgejo_slots", 0)) + 1)]
        unity = any(host in groups.get(g, ()) for g in UNITY_GROUPS)
        if unity:
            total, group = unity_layout(v)
            fails += [f"{host}: build slot {b} (a unity_ci_slot_groups key) is not one of its slots 1-{total}"
                      for b in sorted(group) if not 1 <= b <= total]
            seen = set()
            for b, tests in sorted(group.items()):
                for t in tests:
                    if not 1 <= t <= total or t in group:
                        fails.append(f"{host}: group {b} names {t}, not a test slot")
                    elif t in seen:
                        fails.append(f"{host}: test slot {t} is in two groups")
                    seen.add(t)
            for n in range(1, total + 1):
                slot = {"name": unity_slot(host, n),
                        "caps": ["unity-build"] if n in group else ["unity"] + [c for c in SHARED_CAPS if c in caps]}
                if group.get(n):
                    slot["group"] = [unity_slot(host, t) for t in group[n]]
                slots.append(slot)
        slots += [{"name": p["name"], "caps": [p["cap"]], **({"repo": p["repo"]} if p.get("repo") else {})}
                  for p in v.get("ci_pinned_runners", [])]
        kinds = {c for s in slots for c in s["caps"] if c in HOST_CAPS}
        fails += [f"{host}: has {c} slots, but ci_caps leaves out {c}" for c in sorted(kinds - set(caps))
                  if not v.get("ci_pinned_runners")]
        if slots:
            topo[host] = {"site": "cloud" if v.get("unity_ci_lan", True) is False else "local",
                          **({"full": v.get("unity_ci_full", True) is not False} if unity else {}),
                          "slots": slots}
    names = [s["name"] for h in topo.values() for s in h["slots"]]
    forgejo = sum(1 for h in topo.values() for s in h["slots"] if s.get("forge") == "forgejo")
    if forgejo != FORGEJO_SLOTS:
        fails.append(f"{forgejo} forgejo slots (forgejo_slots), not FORGEJO_SLOTS={FORGEJO_SLOTS}; change both together")
    fails += [f"slot {n} is declared twice" for n in sorted({n for n in names if names.count(n) > 1})]
    return {"hosts": topo}, fails


def unity_layout(v):
    """A host's Unity slot count and slot groups (build slot -> its test slots)."""
    group = {int(b): [int(t) for t in ts] for b, ts in (v.get("unity_ci_slot_groups") or {}).items()}
    return int(v.get("unity_ci_slots", 0)), group


def slot_budget(v, total):
    """One core set per Unity slot ([] without ci_unity_slot_cpus) and one memory cap in G per slot."""
    cpus = [cpu_set(c) for c in v.get("ci_unity_slot_cpus") or []]
    mem = v.get("ci_unity_slot_memory_max")
    mem = [gigabytes(m) for m in mem] if isinstance(mem, list) else [gigabytes(mem) if mem else None] * total
    return cpus, mem


def fit_failures(hosts, groups):
    """Where a host's slots do not fit it: group cores and memory, shared cores, wide memory; and
    where the pool's build slots leave a release, or players.yml, none."""
    fails, release, builds = [], 0, 0
    for host in sorted(set().union(*(groups.get(g, set()) for g in UNITY_GROUPS))):
        v = hosts[host]
        total, group = unity_layout(v)
        cpus, mem = slot_budget(v, total)
        if group and (len(cpus) != total or len(mem) != total):
            fails.append(f"{host}: slot groups need {total} ci_unity_slot_cpus and ci_unity_slot_memory_max entries")
            continue
        for b, tests in sorted(group.items()):
            tests = [t for t in tests if 1 <= t <= total and t not in group]
            if not 1 <= b <= total:
                continue
            fails += [f"{host}: test slot {t}'s cores are not inside build slot {b}'s"
                      for t in tests if not cpus[t - 1] <= cpus[b - 1]]
            used = [mem[t - 1] for t in tests]
            if None in used or mem[b - 1] is None:
                fails.append(f"{host}: group {b}'s memory caps are not in whole G")
            elif sum(used) > mem[b - 1]:
                fails.append(f"{host}: group {b}'s test slots take {sum(used)}G, over build slot {b}'s {mem[b - 1]}G")
        grouped = {pair for b, ts in group.items() for t in ts for pair in ((b, t), (t, b))}
        fails += [f"{host}: slots {i} and {j} share cores and are not one slot group"
                  for i in range(1, len(cpus) + 1) for j in range(i + 1, len(cpus) + 1)
                  if cpus[i - 1] & cpus[j - 1] and (i, j) not in grouped]
        labels = {int(k): ls for k, ls in (v.get("unity_ci_slot_labels") or {}).items()}
        wide = [n for n in range(1, total + 1) if n not in group and v.get("unity_ci_wide")
                or "unity-wide" in labels.get(n, [])]
        caps = [mem[n - 1] if n <= len(mem) else None for n in wide]
        if wide and (None in caps or min(caps) < WIDE_SLOT_MIN_GB):
            have = "unset or not in whole G" if None in caps else f"{min(caps)}G"
            fails.append(f"{host} carries unity-wide, but its ci_unity_slot_memory_max is {have}, "
                         f"under the {WIDE_SLOT_MIN_GB}G a player build in the job needs")
        release += sum(1 for n in group if RELEASE_LABEL in labels.get(n, []))
        builds += len(group)
    if builds and not 0 < release < builds:
        fails.append(f"{release} of the pool's {builds} build slots (unity_ci_slot_groups keys) carry {RELEASE_LABEL} "
                     f"in unity_ci_slot_labels; a release must have at least one and leave players.yml at least one")
    return fails


def no_ci_failures(hosts, groups):
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
    fails += fit_failures(hosts, groups) + no_ci_failures(hosts, groups)
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
