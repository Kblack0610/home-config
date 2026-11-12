#!/usr/bin/env python3
"""Assert the exporter's RUNNER_ROSTER and the governor's standby list match the inventory.

The exporter's denominator ("how many runners SHOULD be online") has to be written
down independently of the GitHub API, because the API only lists runners that
registered and a slot whose play never ran is exactly what the dashboard exists to
show. So it is a copy of the slot counts in ansible/inventory.yml
(linux_ci_runners ci_light_slots / ci_heavy_slots, unity_ci_runners
unity_ci_slots) plus the single-host runners pinned by their own plays (mac-mini, windows-media).

CLOUD_HOSTS is checked the same way: it must equal the runner hosts whose inventory
entry says unity_ci_lan: false, so a cloud host is declared once, in the inventory. The governor's standby list
(apps/ci-runner-governor/config.json) is the third copy: it must name exactly those
cloud hosts, each with max_admitted equal to its unity slots. A build host's first
unity_ci_build_slots slots (aws-build-1) are their own exporter pool, unity-build, and the rest
stay in unity, so one host can be in two pools; its standby entry carries that label and
withheld_label <label>-withheld. The governor's "rates" must name exactly the roster's hosts, so
every runner host has a $/h and a basis and none is priced that is gone.

Two slots may share cores (ci_unity_slot_cpus) only where the governor's "pairs" pair them: a
build slot and a test slot it never runs beside. Each paired test slot sits inside its build
slot's cores, and a pair's test slots together fit under its build slot's memory cap, so a pair
never asks more of the host than its build slot alone.

A slot that carries unity-wide (unity_ci_wide, or unity-wide in unity_ci_slot_labels) needs
ci_unity_slot_memory_max of at least WIDE_SLOT_MIN_GB: Multi, Soak, Two-process and Playloop
build their players in the job, and the server build passed 16G (playground ucp-1830).

A build host gives some of its build slots, never all, the RELEASE_LABEL in unity_ci_slot_labels:
playground release.yml asks for it, so a release (one per game from ship.sh) holds those
slots for hours while players.yml, which asks only unity-build, keeps the rest (hc-263).

Same rule as check-fleet-roster.py: independent copies are the design, copies nobody
compares are the bug. This compares them in BOTH directions, because both break: a
host added to a pool and forgotten here reads as fully healthy, a host retired there
and left here reads as permanently short.

Stdlib only (runs in a bare python:alpine CI container), so inventory.yml is read by
indentation, not by a YAML library. Exits non-zero on drift, or if it found nothing
to compare - an empty parse must never pass. An argument names another governor config, so
CI can show the check fails on a planted bad pair.
"""

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")
EXPORTER_DEPLOY = os.path.join(REPO, "apps/github-runner-exporter/deployment.yaml")
GOVERNOR_CONFIG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "apps/ci-runner-governor/config.json")

# inventory group -> {slot var: exporter pool}
GROUPS = {
    "linux_ci_runners": {"ci_light_slots": "light", "ci_heavy_slots": "heavy"},
    "unity_ci_runners": {"unity_ci_slots": "unity"},
    "unity_ci_mac_runners": {"unity_ci_slots": "unity"},
}
# The macOS runner and the Windows test runner are each one play pinned to one
# host (playbooks/site.yml, "macOS CI runner (mac-mini only)" and "Windows test
# runner (windows-media only)"), not inventory groups with a slot var. The
# mac-mini's second runner, m1-playground-mac, has no play (docs/mac-machines.md).
PINNED = {("mac-mini", "macos"): 2, ("windows-media", "windows"): 1}
# Provisional, low confidence (one kill; revisit when the unity-wide jobs stop building players in the job, playground
# ucp-1793): Soak's server build was killed at 16.96G anon in an aws-burst-2 16G slot on
# 2026-11-11 (ucp-1830); the wide slots that build green (razer-laptop, aws-burst-1 slot 1) have 20G.
WIDE_SLOT_MIN_GB = 20
RELEASE_LABEL = "unity-release"


def inventory_roster(path):
    out, cloud, build, group, in_hosts, host = {}, set(), {}, None, False, None
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
            elif group and host and indent == 10 and key == "unity_ci_lan" and val.strip() == "false":
                cloud.add(host)
            elif group and host and indent == 10 and key == "unity_ci_build_slots":
                build[host] = int(val.strip())
    # A build host's first slots are the unity-build pool's; the rest stay the gate pool's.
    for h, n in build.items():
        if (h, "unity") in out:
            out[(h, "unity-build")] = n
            out[(h, "unity")] -= n
            if not out[(h, "unity")]:
                del out[(h, "unity")]
    return out, cloud


def flow_list(val):
    """The items of a one-line YAML flow list (["1-10", "11-20"] or [36G, 16G])."""
    return [v.strip().strip("'\"") for v in val.strip()[1:-1].split(",") if v.strip()]


def gigabytes(val):
    m = re.fullmatch(r"(\d+)G", val)
    return int(m.group(1)) if m else None


def cpu_set(spec):
    out = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        out |= set(range(int(a), int(b or a) + 1))
    return out


def slot_budgets(path):
    """{host: (one core set per slot, or []; one memory cap in GB or None per slot, or a scalar
    cap)} from ci_hosts' ci_unity_slot_cpus and ci_unity_slot_memory_max."""
    cpus, mem, group, host = {}, {}, None, None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            key, _, val = line.strip().partition(":")
            val = val.strip()
            if indent == 4:
                group, host = key, None
            elif indent == 8:
                host = key
            elif group == "ci_hosts" and host and indent == 10 and key == "ci_unity_slot_cpus":
                cpus[host] = [cpu_set(c) for c in flow_list(val)]
            elif group == "ci_hosts" and host and indent == 10 and key == "ci_unity_slot_memory_max":
                mem[host] = [gigabytes(m) for m in flow_list(val)] if val.startswith("[") else gigabytes(val)
    return {h: (cpus.get(h, []), mem.get(h)) for h in set(cpus) | set(mem)}


def wide_slot_memory(path, budgets):
    """{host: the smallest memory cap in GB of its unity-wide slots, or None} for every host
    with one: every pool slot with unity_ci_wide, else the slots unity_ci_slot_labels gives it."""
    wide, slots, built, group, host = {}, {}, {}, None, None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            key, _, val = line.strip().partition(":")
            val = val.strip()
            if indent == 4:
                group, host = key, None
            elif indent == 8:
                host = key
            elif group != "unity_ci_runners" or not host:
                continue
            elif indent == 10 and key == "unity_ci_wide" and val == "true":
                wide[host] = None
            elif indent == 10 and key == "unity_ci_slots":
                slots[host] = int(val)
            elif indent == 10 and key == "unity_ci_build_slots":
                built[host] = int(val)
            elif indent == 12 and key.isdigit() and re.search(r"\bunity-wide\b", val) and wide.get(host, ()) is not None:
                wide.setdefault(host, set()).add(int(key))
    out = {}
    for h, numbers in wide.items():
        numbers = range(built.get(h, 0) + 1, slots.get(h, 0) + 1) if numbers is None else numbers
        mem = (budgets.get(h) or ([], None))[1]
        caps = [mem[n - 1] if isinstance(mem, list) and n <= len(mem) else None for n in numbers] \
            if isinstance(mem, list) else [mem]
        out[h] = None if None in caps or not caps else min(caps)
    return out


def check_pairs(host, pairs, build, slots, budget):
    """Failures of one standby host's "pairs" against its inventory slots and budgets."""
    cpus, mem = budget
    mem = mem if isinstance(mem, list) else [mem] * slots
    fails, seen = [], set()
    if len(cpus) != slots or len(mem) != slots:
        return [f"{host}: pairs need {slots} ci_unity_slot_cpus and ci_unity_slot_memory_max entries"]
    # The governor tells a build slot from a test slot by the pairs' keys.
    if {int(k) for k in pairs} != set(range(1, build + 1)):
        fails.append(f"{host}: pairs keys are {sorted(int(k) for k in pairs)}, its build slots 1-{build}")
    for key, tests in sorted(pairs.items()):
        b = int(key)
        if not 1 <= b <= build:
            fails.append(f"{host}: pairs key {b} is not a build slot (1-{build})")
            continue
        for t in tests:
            if not build < t <= slots:
                fails.append(f"{host}: pair {b} names {t}, not a test slot ({build + 1}-{slots})")
            elif t in seen:
                fails.append(f"{host}: test slot {t} is in two pairs")
            elif not cpus[t - 1] <= cpus[b - 1]:
                fails.append(f"{host}: test slot {t}'s cores are not inside build slot {b}'s")
            seen.add(t)
        used = [mem[t - 1] for t in tests if build < t <= slots]
        if None in used or mem[b - 1] is None:
            fails.append(f"{host}: pair {b}'s memory caps are not in whole G")
        elif sum(used) > mem[b - 1]:
            fails.append(f"{host}: pair {b}'s test slots take {sum(used)}G, over build slot {b}'s {mem[b - 1]}G")
    return fails


def overlaps(host, pairs, budget):
    """Two slots of one host that share cores and that the governor does not pair."""
    cpus = budget[0]
    paired = {(int(b), t) for b, ts in (pairs or {}).items() for t in ts}
    return [f"{host}: slots {i} and {j} share cores and the governor does not pair them"
            for i in range(1, len(cpus) + 1) for j in range(i + 1, len(cpus) + 1)
            if cpus[i - 1] & cpus[j - 1] and (i, j) not in paired]


def build_release_slots(path):
    """{host: (its build slots, how many of them carry RELEASE_LABEL in unity_ci_slot_labels)}
    for every build host. A label on a slot past unity_ci_build_slots is on a test slot, where
    no build asks for it, so it does not count."""
    build, labelled, group, host, in_labels = {}, {}, None, None, False
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            key, _, val = line.strip().partition(":")
            val = val.strip()
            if indent == 4:
                group, host = key, None
            elif indent <= 8:
                host = key if indent == 8 else None
            if indent <= 10:
                in_labels = False
            if group != "unity_ci_runners" or not host or indent <= 8:
                continue
            if indent == 10 and key == "unity_ci_build_slots":
                build[host] = int(val)
            elif indent == 10 and key == "unity_ci_slot_labels":
                in_labels = True
            elif in_labels and indent == 12 and key.isdigit() and RELEASE_LABEL in re.findall(r"[\w-]+", val):
                labelled.setdefault(host, set()).add(int(key))
    return {h: (n, sum(1 for s in labelled.get(h, ()) if 1 <= s <= n)) for h, n in build.items()}


def env_value(text, name, path):
    m = re.search(rf"name:\s*{name}\s*\n\s*value:\s*\"([^\"]*)\"", text)
    if not m:
        sys.exit(f"FAIL: no {name} value in {path}")
    return m.group(1)


def exporter_roster(path):
    text = open(path, encoding="utf-8").read()
    out = {}
    for entry in env_value(text, "RUNNER_ROSTER", path).split():
        host, pool, slots = entry.split(":")
        out[(host, pool)] = int(slots)
    return out, set(env_value(text, "CLOUD_HOSTS", path).split())


def main():
    want, want_cloud = inventory_roster(INVENTORY)
    want.update(PINNED)
    have, have_cloud = exporter_roster(EXPORTER_DEPLOY)
    if len(want) <= len(PINNED):
        sys.exit(f"FAIL: parsed no runner slots out of {INVENTORY}; the parser is broken, not the roster")
    rc = 0
    for key in sorted(set(want) | set(have)):
        w, h = want.get(key), have.get(key)
        if w != h:
            rc = 1
            print(f"FAIL: {key[0]} {key[1]}: inventory says {w}, RUNNER_ROSTER says {h}")
    if want_cloud != have_cloud:
        rc = 1
        print(f"FAIL: CLOUD_HOSTS is {sorted(have_cloud)}, inventory unity_ci_lan: false hosts are {sorted(want_cloud)}")
    with open(GOVERNOR_CONFIG, encoding="utf-8") as fh:
        governor = json.load(fh)
    entries = governor["standby"]
    roster_hosts = {h for h, _ in have}
    if set(governor.get("rates", {})) != roster_hosts:
        rc = 1
        print(f"FAIL: governor rates name {sorted(governor.get('rates', {}))}, RUNNER_ROSTER hosts are {sorted(roster_hosts)}")
    standby = {s["host"]: s["max_admitted"] for s in entries}
    for s in entries:
        # Without its own withheld label a dedicated host's withheld slots read as the gate pool's.
        if s.get("labels") and s.get("withheld_label") != s["labels"][0] + "-withheld":
            rc = 1
            print(f"FAIL: {s['host']}: a dedicated standby host needs withheld_label {s['labels'][0]}-withheld")
        if s.get("labels") and (s["host"], s["labels"][0]) not in want:
            rc = 1
            print(f"FAIL: {s['host']}: a dedicated standby host, but the inventory gives it no {s['labels'][0]} slots")
    if set(standby) != want_cloud:
        rc = 1
        print(f"FAIL: governor standby is {sorted(standby)}, inventory unity_ci_lan: false hosts are {sorted(want_cloud)}")
    for host in sorted(set(standby) & want_cloud):
        slots = sum(n for (h, _), n in want.items() if h == host)
        if standby[host] != slots:
            rc = 1
            print(f"FAIL: {host}: governor max_admitted is {standby[host]}, inventory unity slots {slots}")
    budgets = slot_budgets(INVENTORY)
    builds = build_release_slots(INVENTORY)
    pairs = {s["host"]: s["pairs"] for s in entries if s.get("pairs")}
    for host in sorted(set(pairs) - set(builds)):
        rc = 1
        print(f"FAIL: {host}: governor pairs, but the inventory gives it no unity_ci_build_slots")
    fails = [f for host in sorted(set(pairs) & set(builds)) for f in check_pairs(
        host, pairs[host], builds[host][0], sum(n for (h, _), n in want.items() if h == host),
        budgets.get(host, ([], None)))]
    fails += [f for host in sorted(budgets) for f in overlaps(host, pairs.get(host), budgets[host])]
    for f in fails:
        rc = 1
        print(f"FAIL: {f}")
    if not pairs:
        rc = 1
        print("FAIL: parsed no governor pairs; the parser is broken, not the roster")
    wide = wide_slot_memory(INVENTORY, budgets)
    if not wide:
        rc = 1
        print("FAIL: parsed no unity-wide slot out of the inventory; the parser is broken, not the roster")
    for host, gb in sorted(wide.items()):
        if gb is None or gb < WIDE_SLOT_MIN_GB:
            rc = 1
            have = "unset or not in whole G" if gb is None else f"{gb}G"
            print(f"FAIL: {host} carries unity-wide, but its ci_unity_slot_memory_max is {have}, "
                  f"under the {WIDE_SLOT_MIN_GB}G a player build in the job needs")
    if not builds:
        rc = 1
        print("FAIL: parsed no build host out of the inventory; the parser is broken, not the roster")
    for host, (slots, release) in sorted(builds.items()):
        if not 0 < release < slots:
            rc = 1
            print(f"FAIL: {host}: {release} of its {slots} build slots (unity_ci_build_slots) carry {RELEASE_LABEL} "
                  f"in unity_ci_slot_labels; a release must have at least one and leave players.yml at least one")
    if rc == 0:
        print(f"ok: {len(want)} host/pool slot counts agree, cloud hosts {sorted(want_cloud)} match the governor standby, "
              f"build hosts leave Players a slot: {sorted(builds)}, pairs fit their build slots: {sorted(pairs)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
