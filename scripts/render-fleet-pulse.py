#!/usr/bin/env python3
"""Render the fleet.kblab.me machines and their roster from the fleet_pulse group in ansible/inventory.yml.

The inventory is the one place a machine joins or leaves the fleet board. This writes:
- apps/gatus-fleet/config.yaml: config.base.yaml, then an external-endpoint per machine that
  pushes and an endpoint per machine gatus polls (node_exporter on ansible_host:9100, plus the
  systemd units in fleet_pulse_units).
- apps/fleet-exporter/roster.json: {roster, display}. roster is every machine counted for health
  (fleet_pulse_roster is not false); display is the waybar tokens, <name>=<label> for a machine
  with its own dot (~<name>=<label> for an unrostered one, listed in the tooltip only), then @<group>=<label> for each
  of fleet_pulse_groups. fleet-exporter counts against the roster and serves the file at
  fleet.kblab.me/roster, which the status bars read.

Members are the group's hosts in file order, then fleet_pulse_devices (push-only devices Ansible
does not manage). The inventory group documents each variable.

    render-fleet-pulse.py           write both files
    render-fleet-pulse.py --check   fail when either is not what the inventory renders (the CI gate)
    --inventory PATH                another inventory (CI's negative control)
"""

import argparse
import json
import os
import re
import sys

import yaml

from host_inventory import load_inventory

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")
GROUP = "fleet_pulse"
BASE = os.path.join(REPO, "apps/gatus-fleet/config.base.yaml")
GATUS_OUT = os.path.join(REPO, "apps/gatus-fleet/config.yaml")
ROSTER_OUT = os.path.join(REPO, "apps/fleet-exporter/roster.json")
# The bars split the roster on whitespace and match names exactly, so a name is kebab-case.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# A label sits after the = of a display token the bars split on whitespace.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
NODE_EXPORTER_PORT = 9100
POLL_INTERVAL = "60s"


def file_order(top, node, seen):
    """A group's hosts in file order: its own, then each child group's, each host once."""
    for host in node.get("hosts") or {}:
        seen.setdefault(host, None)
    for child, inline in (node.get("children") or {}).items():
        file_order(top, inline or top.get(child) or {}, seen)
    return list(seen)


def members(path):
    """[{name, group, push, units, roster, display, addr}] for the group, in file order."""
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    top = (doc.get("all") or {}).get("children") or {}
    node = top.get(GROUP)
    if not node:
        raise SystemExit(f"FAIL: {os.path.relpath(path, REPO)} has no {GROUP} group")
    gvars = node.get("vars") or {}
    hosts, _ = load_inventory(path)
    out = []
    for host in file_order(top, node, {}):
        v = hosts[host]
        out.append({
            "name": v.get("fleet_pulse_name", host),
            "group": v.get("fleet_pulse_group", "k3s"),
            "push": bool(v.get("fleet_pulse_push", False)),
            "units": list(v.get("fleet_pulse_units") or []),
            "roster": v.get("fleet_pulse_roster", True) is not False,
            "display": v.get("fleet_pulse_display"),
            "addr": v.get("ansible_host"),
            "host": host,
        })
    for d in gvars.get("fleet_pulse_devices") or []:
        if not (isinstance(d, dict) and d.get("name") and d.get("group")):
            raise SystemExit(f"FAIL: a fleet_pulse_devices entry needs a name and a group: {d!r}")
        out.append({
            "name": d["name"], "group": d["group"], "push": True, "units": [],
            "roster": d.get("roster", True) is not False, "display": d.get("display"),
            "addr": None, "host": d["name"],
        })
    return out, dict(gvars.get("fleet_pulse_groups") or {})


def failures(ms, groups):
    fails, seen = [], set()
    for m in ms:
        if not NAME_RE.match(m["name"]):
            fails.append(f"{m['host']}: gatus name {m['name']!r} is not kebab-case")
        if m["name"] in seen:
            fails.append(f"{m['host']}: gatus name {m['name']!r} is declared twice")
        seen.add(m["name"])
        if not m["push"] and not m["addr"]:
            fails.append(f"{m['host']}: polled, but has no ansible_host to poll")
        if m["units"] and m["push"]:
            fails.append(f"{m['host']}: fleet_pulse_units needs a polled host")
        if m["display"] is not None and not LABEL_RE.match(str(m["display"])):
            fails.append(f"{m['host']}: display label {m['display']!r} is not one word")
        if "{{" in json.dumps([m["name"], m["addr"], m["display"]]):
            fails.append(f"{m['host']}: a fleet_pulse value is templated; give it literally")
    for g, label in groups.items():
        if not LABEL_RE.match(str(label)):
            fails.append(f"fleet_pulse_groups label {label!r} for {g!r} is not one word")
        if not any(m["group"] == g for m in ms if m["roster"]):
            fails.append(f"fleet_pulse_groups names {g!r}, which no rostered machine is in")
    return fails


def q(s):
    return json.dumps(s)  # a YAML double-quoted scalar for the strings rendered here


def render_gatus(ms, base):
    L = [base.rstrip("\n"), ""]
    L.append("# --- Rendered from the fleet_pulse group in ansible/inventory.yml by")
    L.append("# scripts/render-fleet-pulse.py. Do not edit below this line.")
    L.append("")
    L.append("# PUSH: machines that report in, because nothing can reach them.")
    L.append("external-endpoints:")
    for m in (m for m in ms if m["push"]):
        L += [f"  - name: {m['name']}", f"    group: {m['group']}", '    token: "${FLEET_TOKEN}"']
    L.append("")
    L.append(f"# POLL: node_exporter on :{NODE_EXPORTER_PORT} of machines we can reach.")
    L.append("endpoints:")
    for m in (m for m in ms if not m["push"]):
        url = "http://%s:%d/metrics" % (m["addr"], NODE_EXPORTER_PORT)
        L += [f"  - name: {m['name']}", f"    group: {m['group']}", f"    url: {q(url)}",
              f"    interval: {POLL_INTERVAL}", "    conditions:", '      - "[STATUS] == 200"']
        for u in m["units"]:
            pat = f'*node_systemd_unit_state{{name="{u}",state="active",type="simple"}} 1*'
            L.append(f"      - '[BODY] == pat({pat})'")
    return "\n".join(L) + "\n"


def render_roster(ms, groups):
    display = [("" if m["roster"] else "~") + f"{m['name']}={m['display']}" for m in ms if m["display"]]
    display += [f"@{g}={label}" for g, label in groups.items()]
    doc = {"roster": [m["name"] for m in ms if m["roster"]], "display": display}
    return json.dumps(doc, indent=2) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--check", action="store_true", help="fail when a rendered file is stale")
    ap.add_argument("--inventory", default=INVENTORY)
    args = ap.parse_args()
    ms, groups = members(args.inventory)
    fails = failures(ms, groups)
    for f in fails:
        print(f"FAIL: {f}")
    if fails:
        return 1
    base = open(BASE, encoding="utf-8").read()
    want = {GATUS_OUT: render_gatus(ms, base), ROSTER_OUT: render_roster(ms, groups)}
    yaml.safe_load(want[GATUS_OUT])  # what gatus will parse
    stale = []
    for path, text in want.items():
        rel = os.path.relpath(path, REPO)
        if args.check:
            have = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
            if have != text:
                stale.append(rel)
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {rel}")
    if stale:
        inv = os.path.relpath(args.inventory, REPO)
        for rel in stale:
            print(f"FAIL: {rel} is not what {inv} renders; run scripts/render-fleet-pulse.py and commit it")
        return 1
    print(("ok: " if args.check else "") + f"{len(ms)} machines, {sum(m['roster'] for m in ms)} rostered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
