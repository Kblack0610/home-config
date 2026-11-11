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
cloud hosts, each with max_admitted equal to its unity slots. A build host (unity_ci_build: true with
unity_ci_pool: false, aws-build-1) is its own exporter pool, unity-build, and its standby entry carries
that label and withheld_label <label>-withheld. The governor's "rates" must name exactly the
roster's hosts, so every runner host has a $/h and a basis and none is priced that is gone.

A slot that carries unity-wide (unity_ci_wide, or unity-wide in unity_ci_slot_labels) needs
ci_unity_slot_memory_max of at least WIDE_SLOT_MIN_GB: Multi, Soak, Two-process and Playloop
build their players in the job, and the server build passed 16G (playground ucp-1830).

A build host gives some of its slots, never all, the RELEASE_LABEL in unity_ci_slot_labels:
playground release.yml asks for it, so a release (one per game from ship.sh) holds those
slots for hours while players.yml, which asks only unity-build, keeps the rest (hc-263).

Same rule as check-fleet-roster.py: independent copies are the design, copies nobody
compares are the bug. This compares them in BOTH directions, because both break: a
host added to a pool and forgotten here reads as fully healthy, a host retired there
and left here reads as permanently short.

Stdlib only (runs in a bare python:alpine CI container), so inventory.yml is read by
indentation, not by a YAML library. Exits non-zero on drift, or if it found nothing
to compare - an empty parse must never pass.
"""

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")
EXPORTER_DEPLOY = os.path.join(REPO, "apps/github-runner-exporter/deployment.yaml")
GOVERNOR_CONFIG = os.path.join(REPO, "apps/ci-runner-governor/config.json")

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
    out, cloud, build, group, in_hosts, host = {}, set(), set(), None, False, None
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
            elif group and host and indent == 10 and key == "unity_ci_build" and val.strip() == "true":
                build.add(host)
    # A build-only host's slots are the unity-build pool's, not the gate pool's.
    for h in build:
        if (h, "unity") in out:
            out[(h, "unity-build")] = out.pop((h, "unity"))
    return out, cloud


def wide_slot_memory(path):
    """{host: its slot memory cap in GB, or None} for every host with a unity-wide slot."""
    wide, mem, group, host = set(), {}, None, None
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
            elif group == "unity_ci_runners" and host and indent == 10 and key == "unity_ci_wide" and val == "true":
                wide.add(host)
            elif group == "unity_ci_runners" and host and indent == 12 and re.search(r"\bunity-wide\b", val):
                wide.add(host)
            elif group == "ci_hosts" and host and indent == 10 and key == "ci_unity_slot_memory_max":
                m = re.fullmatch(r"(\d+)G", val)
                mem[host] = int(m.group(1)) if m else None
    return {h: mem.get(h) for h in wide}


def build_release_slots(path):
    """{host: (its unity slots, how many carry RELEASE_LABEL)} for every build host."""
    build, slots, release, group, host = set(), {}, {}, None, None
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
            elif indent == 10 and key == "unity_ci_build" and val == "true":
                build.add(host)
            elif indent == 10 and key == "unity_ci_slots":
                slots[host] = int(val)
            elif indent == 12 and re.search(rf"(?<![\w-]){RELEASE_LABEL}(?![\w-])", val):
                release[host] = release.get(host, 0) + 1
    return {h: (slots.get(h, 0), release.get(h, 0)) for h in build}


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
    standby = {s["host"]: (s["max_admitted"], (s.get("labels") or ["unity"])[0]) for s in entries}
    for s in entries:
        # Without its own withheld label a dedicated host's withheld slots read as the gate pool's.
        if s.get("labels") and s.get("withheld_label") != s["labels"][0] + "-withheld":
            rc = 1
            print(f"FAIL: {s['host']}: a dedicated standby host needs withheld_label {s['labels'][0]}-withheld")
    if set(standby) != want_cloud:
        rc = 1
        print(f"FAIL: governor standby is {sorted(standby)}, inventory unity_ci_lan: false hosts are {sorted(want_cloud)}")
    for host in sorted(set(standby) & want_cloud):
        cap, pool = standby[host]
        if cap != want.get((host, pool)):
            rc = 1
            print(f"FAIL: {host}: governor max_admitted is {cap}, inventory {pool} slots {want.get((host, pool))}")
    wide = wide_slot_memory(INVENTORY)
    if not wide:
        rc = 1
        print("FAIL: parsed no unity-wide slot out of the inventory; the parser is broken, not the roster")
    for host, gb in sorted(wide.items()):
        if gb is None or gb < WIDE_SLOT_MIN_GB:
            rc = 1
            have = "unset or not in whole G" if gb is None else f"{gb}G"
            print(f"FAIL: {host} carries unity-wide, but its ci_unity_slot_memory_max is {have}, "
                  f"under the {WIDE_SLOT_MIN_GB}G a player build in the job needs")
    builds = build_release_slots(INVENTORY)
    if not builds:
        rc = 1
        print("FAIL: parsed no build host out of the inventory; the parser is broken, not the roster")
    for host, (slots, release) in sorted(builds.items()):
        if not 0 < release < slots:
            rc = 1
            print(f"FAIL: {host}: {release} of its {slots} build slots carry {RELEASE_LABEL}; a release must "
                  f"have at least one and leave players.yml at least one")
    if rc == 0:
        print(f"ok: {len(want)} host/pool slot counts agree, cloud hosts {sorted(want_cloud)} match the governor standby")
    return rc


if __name__ == "__main__":
    sys.exit(main())
