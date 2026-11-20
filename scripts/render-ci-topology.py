#!/usr/bin/env python3
"""Render apps/ci-topology/topology.json, every CI slot and what it can run, from ansible/inventory.yml.

The inventory is the one place a person writes CI hosts and slots. The exporter (each slot's
expected row, so a slot whose runner never registered still shows as missing) and the
governor (which hosts it powers, their slot counts and slot groups) read the rendered file
from the ci-topology ConfigMap instead of keeping copies of their own.

Top level: "labels", the runner label vocabulary (inventory all.vars ci_labels), which the
governor, the exporter and site.yml read instead of keeping their own. Per host: site (cloud
when unity_ci_lan is false, else local), full (unity_ci_full, Unity hosts only), "power" for a
host the governor powers (ci_power plus its ansible_host as instance_id) and its slots. Per slot: the runner name the play registers, its caps (the job
kinds it takes: light, heavy, unity, unity-build, unity-wide, forgejo, macos, windows; HOST_CAPS says
which of the host's ci_caps each slot gets), the repo of a repo-scoped runner, the forge of a
seat that serves Forgejo (github otherwise), the Unity slot a seat borrows ("borrows"), and for a
lead slot the test slots in its group. A host's lead slots are the keys of its unity_ci_slot_groups,
each with the test slots that share its cores (the governor never runs a lead beside them), [] for a
lead with cores of its own. A lead is a build slot (unity-build) unless unity_ci_wide_slots names it:
then it is a wide slot (unity-wide, for Multi, Soak and Playloop), which the governor admits only
while a wide job waits.

Rendering also checks what the inventory must hold for the slots to fit their host:
- A group's test slots sit inside their build slot's cores, and their memory caps sum to no
  more than the build slot's, so a group never asks more of the host than its build slot alone.
- Two slots of a host share cores (ci_unity_slot_cpus) only inside one group.
- A unity-wide slot, wide lead included, has at least WIDE_SLOT_MIN_GB; a wide slot is a group's lead.
- The pool's build slots carry RELEASE_LABEL on at least one and, when there are two or more, leave
  it off at least one, so a release always has a slot and never holds every slot players.yml builds
  on (hc-263). A lone build slot carries it and players.yml shares it.
- A core host or a host in no_ci_hosts carries no CI.
- A seat borrows one Unity test slot of its own LAN host, outside every slot group: the governor
  lends that slot to a Forgejo seat (one on each such slot of a host tagged forgejo) or to a
  repo runner (ci_repo_runners "borrows") while no GitHub job holds it, so no seat has cores of its own.
  Each such slot registers floor(its CPUs / MIN_JOB_CORES) Forgejo seats (<slot>-forgejo, then
  <slot>-forgejo-2 and on), each entry carrying the slot's "cpus" and "memory_gb"; the governor lends
  one slot to as many of them as their measured weights fit (apps/ci-runner-governor, pack()).
  A host's ci_seat_kinds (light, heavy; each also in its ci_caps) puts the same seats of that GitHub
  kind on each such slot (<slot>-light-1 and on): org runners a lend serves, so small GitHub jobs pack
  onto one slot too, and every Unity slot of that host drops the kind from its caps, so a seat is the
  one path a small job has there. A host without it runs those jobs on whole slots.
- A host's ci_seat_kinds needs a lendable slot, or the kinds would have no runner on it.
- A LAN Unity host's ci_local_seats (a count) is its "local_seats": the local test runs the governor
  leases at once on that host (apps/ci-runner-governor, "Local seats"). They have no runner and no
  forge, so they are a host count, never slots, which every slot reader takes for a runner.

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

# Provisional, low confidence (one kill; revisit when the unity-wide jobs stop building players in the job, playground
# ucp-1793): Soak's server build was killed at 16.96G anon in an aws-burst-2 16G slot on
# 2026-11-11 (ucp-1830); the wide slots that build green (razer-laptop, aws-burst-1 slot 1) have 20G.
WIDE_SLOT_MIN_GB = 20
RELEASE_LABEL = "unity-release"
NO_CI_GROUP = "no_ci_hosts"
CI_GROUPS = ("unity_ci_runners", "unity_ci_mac_runners", "ci_hosts")
UNITY_GROUPS = ("unity_ci_runners", "unity_ci_mac_runners")
# The inventory keys this reads; a Jinja value in one would render as its literal text.
KEYS = ("ci_repo_runners", "ci_pinned_runners", "unity_ci_slots",
        "unity_ci_slot_groups", "unity_ci_wide_slots", "unity_ci_lan", "unity_ci_full", "unity_ci_wide",
        "unity_ci_slot_labels", "ci_unity_slot_cpus", "ci_unity_slot_memory_max", "ci_caps", "ci_seat_kinds",
        "ci_power", "ci_labels", "ci_local_seats")
# The keys of ci_labels, each the label the governor sets or reads for that role.
LABEL_ROLES = ("pool", "full", "build", "wide", "withheld", "build_withheld", "seat_withheld", "lent")
# The roles whose label is also a topology cap name, which the governor matches against slot caps.
CAP_ROLES = {"pool": "unity", "build": "unity-build", "wide": "unity-wide"}
INSTANCE_ID = re.compile(r"i-[0-9a-f]{8,17}")
# A slot registers one Forgejo seat per MIN_JOB_CORES of its CPUs, the most small jobs it could hold at once.
# Provisional, medium confidence: a Forgejo job's p90 was 1.4-2.9 cores, median seat 1.7 (ci_runner_cores_busy,
# 2026-11-11..18), so a slot of 6 cores registers 3 seats; the measured weight, not this, holds any of them
# back. Revisit when a small kind's p90 measures under 2 cores (scripts/ci-kind-weights.py).
MIN_JOB_CORES = 2
# What a host may be tagged for (ci_caps): each of its slots takes every job kind its host is
# tagged for that the slot can hold. A Unity test slot takes unity and SHARED_CAPS; a build slot
# takes builds alone, so a build never waits behind a small job; a wide slot takes unity-wide alone.
# No slot is a small kind's alone:
# a Unity repo's light job parks the warm tree's untracked state first (roles/ci-host,
# ci-fresh-workspace). A seat runs on a Unity test slot the governor lends it (seats()).
HOST_CAPS = ("light", "heavy", "unity", "unity-build", "unity-wide", "forgejo")
# Heavy needs /dev/kvm: platform's Mobile Smoke Android runs the emulator with hardware acceleration
# and times out under -accel off. Tag a host heavy only when it has /dev/kvm (ms-a2, deskslim-2 do;
# the windows-media-ci Hyper-V VM cannot get it, so it is not heavy).
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
        slots += [{"name": f"{host}-{r['slot']}", "caps": ["light"], "repo": r["repo"],
                   "borrows": unity_slot(host, r["borrows"])} for r in v.get("ci_repo_runners", []) if "borrows" in r]
        fails += [f"{host}: ci_repo_runners {r['slot']} has no borrows (the Unity slot it runs on)"
                  for r in v.get("ci_repo_runners", []) if "borrows" not in r]
        unity = any(host in groups.get(g, ()) for g in UNITY_GROUPS)
        local = v.get("ci_local_seats")
        if local is not None and (type(local) is not int or local < 1 or not unity or v.get("unity_ci_lan", True) is False):
            fails.append(f"{host}: ci_local_seats is a count of 1 or more on a LAN Unity host, not {local!r}")
            local = None
        if unity:
            total, group = unity_layout(v)
            fails += [f"{host}: build slot {b} (a unity_ci_slot_groups key) is not one of its slots 1-{total}"
                      for b in sorted(group) if not 1 <= b <= total]
            fails += [f"{host}: wide slot {n} (unity_ci_wide_slots) is not a unity_ci_slot_groups key"
                      for n in wide_slots(v) if n not in group]
            seen = set()
            for b, tests in sorted(group.items()):
                for t in tests:
                    if not 1 <= t <= total or t in group:
                        fails.append(f"{host}: group {b} names {t}, not a test slot")
                    elif t in seen:
                        fails.append(f"{host}: test slot {t} is in two groups")
                    seen.add(t)
            # A kind the host's seats serve runs on a seat only, never on a whole slot.
            seated = set(v.get("ci_seat_kinds") or [])
            for n in range(1, total + 1):
                slot = {"name": unity_slot(host, n),
                        "caps": [("unity-wide" if n in wide_slots(v) else "unity-build")] if n in group
                        else ["unity"] + [c for c in SHARED_CAPS if c in caps and c not in seated]}
                if group.get(n):
                    slot["group"] = [unity_slot(host, t) for t in group[n]]
                slots.append(slot)
            slots += seats(host, v, caps, total, group, slots, fails)
        elif any(s.get("borrows") for s in slots):
            fails.append(f"{host}: a repo runner borrows a Unity slot, but the host has none")
        slots += [{"name": p["name"], "caps": [p["cap"]], **({"repo": p["repo"]} if p.get("repo") else {})}
                  for p in v.get("ci_pinned_runners", [])]
        kinds = {c for s in slots for c in s["caps"] if c in HOST_CAPS}
        fails += [f"{host}: has {c} slots, but ci_caps leaves out {c}" for c in sorted(kinds - set(caps))
                  if not v.get("ci_pinned_runners")]
        power = v.get("ci_power")
        if power:
            fails += power_failures(host, v, power, unity)
            power = {"provider": power.get("provider"), "instance_id": v.get("ansible_host"), "region": power.get("region")}
        if slots:
            topo[host] = {"site": "cloud" if v.get("unity_ci_lan", True) is False else "local",
                          **({"full": v.get("unity_ci_full", True) is not False} if unity else {}),
                          **({"power": power} if power else {}),
                          **({"local_seats": local} if local else {}),
                          "slots": slots}
    names = [s["name"] for h in topo.values() for s in h["slots"]]
    fails += [f"slot {n} is declared twice" for n in sorted({n for n in names if names.count(n) > 1})]
    return {"hosts": topo}, fails


def power_failures(host, v, power, unity):
    """What a ci_power host must hold: an aws instance id as ansible_host, a region, and Unity slots off the LAN."""
    fails = []
    if power.get("provider") != "aws" or not power.get("region") or set(power) - {"provider", "region"}:
        fails.append(f"{host}: ci_power is {{provider: aws, region: <region>}}, not {power}")
    if not INSTANCE_ID.fullmatch(str(v.get("ansible_host", ""))):
        fails.append(f"{host}: ci_power needs an EC2 instance id as ansible_host, not {v.get('ansible_host')}")
    if not unity or v.get("unity_ci_lan", True) is not False:
        fails.append(f"{host}: ci_power on a host that is not a cloud Unity host")
    return fails


def label_failures(labels):
    """The vocabulary has every role, a distinct label for each, and the cap roles their cap names."""
    if not isinstance(labels, dict):
        return ["all.vars has no ci_labels map"]
    fails = [f"ci_labels has no {r}" for r in LABEL_ROLES if not labels.get(r)]
    fails += [f"ci_labels names {r}, not one of {list(LABEL_ROLES)}" for r in labels if r not in LABEL_ROLES]
    fails += [f"ci_labels {r} is {labels.get(r)}, but it is also a topology cap, {c}"
              for r, c in CAP_ROLES.items() if labels.get(r) and labels[r] != c]
    vals = [str(x) for x in labels.values()]
    fails += [f"ci_labels gives {x} to two roles" for x in sorted({x for x in vals if vals.count(x) > 1})]
    return fails


def seats(host, v, caps, total, group, slots, fails):
    """A Forgejo seat on each lendable slot of a LAN host tagged forgejo, plus the failures of
    the repo runners' "borrows". A lendable slot is a test slot in no slot group: a grouped one
    already lends its cores to its build slot, and a slot has one lender at a time."""
    grouped = set(group) | {t for ts in group.values() for t in ts}
    lendable = [unity_slot(host, n) for n in range(1, total + 1) if n not in grouped]
    lan = v.get("unity_ci_lan", True) is not False
    fails += [f"{host}: {s['name']} borrows {s['borrows']}, not one of its lendable LAN slots {lendable}"
              for s in slots if s.get("borrows") and not (lan and s["borrows"] in lendable)]
    kinds = list(v.get("ci_seat_kinds") or [])
    fails += [f"{host}: ci_seat_kinds names {k}, not one of {list(SHARED_CAPS)} in its ci_caps"
              for k in kinds if k not in SHARED_CAPS or k not in caps]
    if kinds and not lan:
        fails.append(f"{host}: ci_seat_kinds on a cloud host, so a small job would power a paid host")
    if not lan:
        return []
    if kinds and not lendable:
        fails.append(f"{host}: ci_seat_kinds with no lendable slot (each is in a slot group), so {kinds} would have no runner")
    cpus, mem = slot_budget(v, total)

    def size(n):
        """The slot's CPUs and memory in G where the inventory gives them: what the governor divides a kind's measured share by."""
        i = int(n.rsplit("-", 1)[1]) - 1
        return {**({"cpus": len(cpus[i])} if i < len(cpus) else {}),
                **({"memory_gb": mem[i]} if i < len(mem) and mem[i] else {})}

    def count(n):
        """The seats a slot registers: one per MIN_JOB_CORES of it, one where the inventory gives no cores.
        The governor's weights decide how many of them run at once."""
        return max(1, int(size(n).get("cpus", 0) // MIN_JOB_CORES))

    forgejo = [{"name": f"{n}-forgejo" + (f"-{i}" if i > 1 else ""), "caps": ["forgejo"], "forge": "forgejo",
                "borrows": n, **size(n)} for n in lendable if "forgejo" in caps for i in range(1, count(n) + 1)]
    return forgejo + [{"name": f"{n}-{k}-{i}", "caps": [k], "borrows": n, **size(n)}
                      for k in kinds if k in SHARED_CAPS and k in caps for n in lendable for i in range(1, count(n) + 1)]


def unity_layout(v):
    """A host's Unity slot count and slot groups (build slot -> its test slots)."""
    group = {int(b): [int(t) for t in ts] for b, ts in (v.get("unity_ci_slot_groups") or {}).items()}
    return int(v.get("unity_ci_slots", 0)), group


def wide_slots(v):
    """The lead slots of a host that are wide slots (unity_ci_wide_slots), the rest being build slots."""
    return {int(n) for n in v.get("unity_ci_wide_slots") or []}


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
                or "unity-wide" in labels.get(n, []) or n in wide_slots(v) & set(group)]
        caps = [mem[n - 1] if n <= len(mem) else None for n in wide]
        if wide and (None in caps or min(caps) < WIDE_SLOT_MIN_GB):
            have = "unset or not in whole G" if None in caps else f"{min(caps)}G"
            fails.append(f"{host} carries unity-wide, but its ci_unity_slot_memory_max is {have}, "
                         f"under the {WIDE_SLOT_MIN_GB}G a player build in the job needs")
        builds_here = [n for n in group if n not in wide_slots(v)]
        release += sum(1 for n in builds_here if RELEASE_LABEL in labels.get(n, []))
        builds += len(builds_here)
    if builds and not (0 < release < builds or release == builds == 1):
        fails.append(f"{release} of the pool's {builds} build slots (unity_ci_slot_groups keys) carry {RELEASE_LABEL} "
                     f"in unity_ci_slot_labels; a release must have at least one and, with two or more, "
                     f"leave players.yml at least one")
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
           f'  "labels": {json.dumps(topo["labels"])},',
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
    labels = (yaml.safe_load(open(args.inventory, encoding="utf-8"))["all"].get("vars") or {}).get("ci_labels")
    fails += label_failures(labels)
    topo = {"labels": labels if isinstance(labels, dict) else {}, **topo}
    fails += fit_failures(hosts, groups) + no_ci_failures(hosts, groups)
    if not fails and not any(s.get("group") for h in topo["hosts"].values() for s in h["slots"]):
        fails.append("rendered no slot group; the parser is broken, not the inventory")
    for f in fails:
        print(f"FAIL: {f}")
    if fails:
        return 1
    text = dump(topo)
    assert json.loads(text) == {"generated_by": json.loads(text)["generated_by"], **topo}
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
