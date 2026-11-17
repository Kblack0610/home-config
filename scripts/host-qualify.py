#!/usr/bin/env python3
"""Qualify a host for a tier and write its record, the only proof check-placement.py accepts.

  scripts/host-qualify.py razer-laptop                 # passive checks, then a 10 minute burn-in
  scripts/host-qualify.py asus-laptop --no-burn-in     # a host holding core state takes no load
  scripts/host-qualify.py hp-victus --dry-run          # print the record, write nothing

Passive checks first, from what already watches the host: host-headroom.py's 7-day readings,
the Loki host-kernel-faults rules (read from Loki's ruler through host-postmortem.py), uptime,
mains power, the armed watchdog, netconsole and SMART. The burn-in (stress-ng on every CPU plus a
quarter of memory with --verify, and a verified fio random write) runs only when the passive
checks meet the standard bar, so a host with a fault history is never stressed. During it the
peak CPU temperature is read from Prometheus and the kernel log is matched against the same
fault rules.

Each check gates a tier (the bars are in docs/host-tiers.md). The record names the highest tier
whose every check passed and goes to ansible/host_vars/<host>/qualification.yml. A record that
meets the host's tier (and at least standard) also lifts cluster-janitor's quarantine taint
from its node: this is the only automated way it comes off. The burn-in
tools come from roles/host-qualify. Runs from the workstation: ssh to the inventory alias,
Prometheus, and kubectl to home-k3s for Loki.
"""

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

from host_inventory import (QUALIFICATION_KEY, TIER_ORDER, load_inventory, qualification_path)

HERE = Path(__file__).resolve().parent


def _module(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


headroom = _module("headroom", "host-headroom.py")
postmortem = _module("postmortem", "host-postmortem.py")

STANDARD, CORE = ["standard", "core"], ["core"]
# Provisional, medium confidence (2026-11-16, host-tiers plan); revisit after P5's first runs.
FAULT_DAYS = 14          # the core bar's "14 days with no kernel fault", applied to standard too
UPTIME_DAYS = 14         # the core bar's "14 days with no unplanned reboot"
PEAK_TEMP_C = 90.0       # NodeCpuHot's threshold: a burn-in that would page fails
SMART_TRANSPORTS = {"nvme", "sata", "sas"}  # internal disks; a USB backup drive is not the host
KUBE_CONTEXT = postmortem.KUBE_CONTEXT
DEGRADED_TAINT = "kblab.me/degraded"         # written by cluster-janitor's quarantine step

PASSIVE = r"""
missing=""; for t in stress-ng fio smartctl; do command -v $t >/dev/null || missing="$missing $t"; done
echo "===missing$missing"
echo "===watchdog $(systemctl show -p RuntimeWatchdogUSec --value) $(test -e /dev/watchdog0 && echo dev || echo nodev)"
echo "===netconsole $(systemctl is-active netconsole)"
lsblk -dno NAME,TRAN | while read -r name tran; do
  [ -n "$tran" ] || continue
  echo "===smart $name $tran"
  sudo -n smartctl -H -A -j /dev/$name
done
"""

BURN_IN = r"""
dir=$(mktemp -d /var/tmp/host-qualify.XXXXXX); trap 'rm -rf "$dir"' EXIT
start=$(date +%s)
stress-ng --cpu 0 --vm 1 --vm-bytes 25% --verify --timeout {seconds}s --metrics-brief >"$dir/stress" 2>&1 &
pid=$!
fio --name=qualify --directory="$dir" --size=1G --rw=randwrite --bs=4k --direct=1 --ioengine=libaio \
    --iodepth=16 --verify=crc32c --output-format=json >"$dir/fio" 2>&1
echo "===fio $?"; cat "$dir/fio"
wait $pid; echo "===stress $?"; tail -n 5 "$dir/stress"
echo "===kernel"; sudo -n journalctl -k -o cat --no-pager --since "@$start"
"""


def ssh(host, script, timeout):
    # bash -s: asus-laptop's login shell is fish, which cannot parse these scripts.
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "bash", "-s"],
                         input=script, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0 and "===" not in out.stdout:
        sys.exit(f"FAIL: ssh {host}: {out.stderr.strip()[:300]}")
    return out.stdout


def sections(text):
    """Split '===name args' marked output into [(header, body)]."""
    parts = []
    for line in text.splitlines():
        if line.startswith("==="):
            parts.append([line[3:], []])
        elif parts:
            parts[-1][1].append(line)
    return [(head, "\n".join(body)) for head, body in parts]


def check(name, ok, detail, gates):
    return {"name": name, "pass": bool(ok), "gates": list(gates), "detail": detail}


def qualified_for(checks):
    """The highest tier whose every gating check passed; probation needs only to have joined."""
    best = "probation"
    for tier in TIER_ORDER[1:]:
        if all(c["pass"] for c in checks if tier in c["gates"]):
            best = tier
        else:
            break
    return best


def smart_check(name, tran, body):
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return check(f"smart {name}", False, f"{tran}: smartctl gave no JSON", STANDARD)
    passed = data.get("smart_status", {}).get("passed")
    nvme = data.get("nvme_smart_health_information_log", {})
    warning, media = nvme.get("critical_warning", 0), nvme.get("media_errors", 0)
    ok = passed is True and not warning and not media
    return check(f"smart {name}", ok, f"{tran}: overall {'PASSED' if passed else passed}, critical_warning "
                 f"{warning}, media_errors {media}", STANDARD)


def passive_checks(host, inst):
    checks = []
    r = headroom.readings(inst)
    state, why = headroom.verdict(r)
    checks.append(check("headroom", state == "ROOM", headroom.line(host, r).split(None, 1)[1], CORE))

    end = dt.datetime.now(dt.timezone.utc)
    counts = {}
    for rule, regex in postmortem.fault_rules():
        expr = (f'sum(count_over_time({{host="{host}",transport="kernel"}} |~ {json.dumps(regex)}'
                f' [{FAULT_DAYS}d]))')
        res = postmortem.loki("/loki/api/v1/query", query=expr, time=postmortem.ns(end))["result"]
        counts[rule] = int(float(res[0]["value"][1])) if res else 0
    hits = {k: v for k, v in counts.items() if v}
    checks.append(check(f"kernel faults {FAULT_DAYS}d", not hits,
                        ", ".join(f"{k} {v}" for k, v in hits.items()) or f"none of {', '.join(counts)}",
                        STANDARD))

    i = f'instance="{inst}"'
    boot = headroom.scalar(f"node_boot_time_seconds{{{i}}}")
    days = None if boot is None else (end.timestamp() - boot) / 86400
    checks.append(check(f"uptime {UPTIME_DAYS}d", days is not None and days >= UPTIME_DAYS,
                        "no reading" if days is None else f"{days:.1f} days", CORE))

    mains = headroom.scalar({n: e for n, e, _ in postmortem.COLUMNS}["mains"] % {"i": i})
    checks.append(check("on mains", mains != 0, "no battery" if mains is None else f"online {mains:.0f}", CORE))

    out = sections(ssh(host, PASSIVE, 120))
    heads = [h for h, _ in out]
    missing = next((h.split()[1:] for h in heads if h.startswith("missing")), ["output"])
    if missing:
        sys.exit(f"FAIL: {host} lacks {' '.join(missing)}; apply roles/host-qualify first")
    for head, body in out:
        kind, *args = head.split()
        if kind == "watchdog":
            ok = args[0] not in ("0", "infinity", "") and args[1] == "dev"
            checks.append(check("watchdog", ok, f"RuntimeWatchdog {args[0]}, /dev/watchdog0 "
                                f"{'present' if args[1] == 'dev' else 'absent'}", STANDARD))
        elif kind == "netconsole":
            checks.append(check("netconsole", args == ["active"], f"unit {' '.join(args)}", CORE))
        elif kind == "smart" and args[1] in SMART_TRANSPORTS:
            checks.append(smart_check(args[0], args[1], body))
    if not any(c["name"].startswith("smart ") for c in checks):
        checks.append(check("smart", False, "no internal disk answered", STANDARD))
    return checks


def burn_in_checks(host, inst, seconds, rules):
    start = dt.datetime.now(dt.timezone.utc)
    out = dict(sections(ssh(host, BURN_IN.format(seconds=seconds), seconds + 1800)))
    heads = {h.split()[0]: h.split()[1:] for h in out}
    checks = []

    fio_rc = heads.get("fio", ["?"])[0]
    body = next((b for h, b in out.items() if h.startswith("fio")), "")
    try:
        job = json.loads(body[body.index("{"):])["jobs"][0]
        detail = f"exit {fio_rc}, {job['write']['iops']:.0f} write IOPS, {job['error']} errors, verified"
    except (ValueError, KeyError, IndexError):
        detail = f"exit {fio_rc}: {body[-200:]}"
    checks.append(check("burn-in fio", fio_rc == "0", detail, STANDARD))

    stress_rc = heads.get("stress", ["?"])[0]
    stress = next((b for h, b in out.items() if h.startswith("stress")), "")
    checks.append(check("burn-in stress-ng", stress_rc == "0",
                        f"exit {stress_rc}, {seconds}s cpu all + vm 25% --verify: {stress.splitlines()[-1:]}",
                        STANDARD))

    kernel = out.get("kernel", "").splitlines()
    hits = {rule: sum(1 for line in kernel if re.search(regex, line)) for rule, regex in rules}
    hits = {k: v for k, v in hits.items() if v}
    checks.append(check("burn-in kernel faults", not hits,
                        ", ".join(f"{k} {v}" for k, v in hits.items()) or f"none in {len(kernel)} kernel lines",
                        STANDARD))

    span = int((dt.datetime.now(dt.timezone.utc) - start).total_seconds())
    i = f'instance="{inst}"'
    peak = headroom.scalar(f'max(max_over_time(node_hwmon_temp_celsius{{{i}}}[{span}s]) * on (instance, chip)'
                           f' group_left (chip_name) node_hwmon_chip_names{{{i},chip_name=~"coretemp|k10temp"}})')
    checks.append(check("burn-in peak cpu temperature", peak is not None and peak <= PEAK_TEMP_C,
                        "no reading" if peak is None else f"{peak:.0f}C, limit {PEAK_TEMP_C:.0f}C", STANDARD))
    return checks


def release_tier(inventory_tier):
    """A quarantined node comes back only on a record that meets its tier, and never below standard."""
    return TIER_ORDER[max(TIER_ORDER.index(inventory_tier or "probation"), TIER_ORDER.index("standard"))]


def releases(qualified, inventory_tier):
    return TIER_ORDER.index(qualified) >= TIER_ORDER.index(release_tier(inventory_tier))


def release(host, ip, record):
    """Lift the janitor's quarantine taint and stamp the requalification (apps/cluster-janitor/quarantine.py)."""
    nodes = json.loads(subprocess.run(["kubectl", "--context", KUBE_CONTEXT, "get", "nodes", "-o", "json"],
                                      check=True, capture_output=True, text=True).stdout)["items"]
    match = [n for n in nodes if any(a["address"] == ip for a in n["status"].get("addresses", []))]
    if len(match) != 1:
        print(f"{host}: {len(match)} nodes have InternalIP {ip}; nothing released")
        return
    node = match[0]["metadata"]["name"]
    if not any(t.get("key") == DEGRADED_TAINT for t in match[0]["spec"].get("taints") or []):
        print(f"{host}: node {node} is not quarantined")
        return
    if not releases(record["qualified_for"], record["inventory_tier"]):
        print(f"{host}: node {node} stays quarantined: qualified for {record['qualified_for']}, "
              f"needs {release_tier(record['inventory_tier'])}")
        return
    kubectl = ["kubectl", "--context", KUBE_CONTEXT]
    subprocess.run(kubectl + ["annotate", "node", node, "--overwrite", f"kblab.me/qualified-at={record['date']}"],
                   check=True)
    subprocess.run(kubectl + ["taint", "node", node, f"{DEGRADED_TAINT}-"], check=True)
    print(f"{host}: released node {node} from quarantine")


def parse_minutes(s):
    return int(s[:-1]) * {"m": 60, "h": 3600}[s[-1]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("host", help="inventory host name, e.g. razer-laptop")
    ap.add_argument("--burn-in", default="10m", help="stress-ng duration (e.g. 10m, 1h)")
    ap.add_argument("--no-burn-in", action="store_true", help="record the passive checks only (cannot qualify)")
    ap.add_argument("--dry-run", action="store_true", help="print the record, write nothing")
    ap.add_argument("--inventory", type=Path, default=HERE.parent / "ansible/inventory.yml")
    a = ap.parse_args(argv)

    hosts, _ = load_inventory(a.inventory)
    if a.host not in hosts:
        sys.exit(f"FAIL: {a.host} is not in {a.inventory}")
    known = headroom.hosts()
    if a.host not in known:
        sys.exit(f"FAIL: {a.host} is not a cc:host_instance host")
    inst = known[a.host]

    checks = passive_checks(a.host, inst)
    if a.no_burn_in:
        checks.append(check("burn-in", False, "not run (--no-burn-in)", STANDARD))
    elif qualified_for(checks) == "probation":
        checks.append(check("burn-in", False, "not run: the passive checks fail the standard bar", STANDARD))
    else:
        checks += burn_in_checks(a.host, inst, parse_minutes(a.burn_in), postmortem.fault_rules())

    record = {QUALIFICATION_KEY: {
        "date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "qualified_for": qualified_for(checks),
        "inventory_tier": hosts[a.host].get("host_tier"),
        "checks": checks,
    }}
    text = ("# Written by scripts/host-qualify.py (docs/host-tiers.md). Do not edit; rerun it.\n"
            + yaml.safe_dump(record, sort_keys=False, width=200))
    for c in checks:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['name']:<30} {'/'.join(c['gates']):<13} {c['detail']}")
    print(f"{a.host}: qualified for {record[QUALIFICATION_KEY]['qualified_for']} "
          f"(inventory tier {hosts[a.host].get('host_tier')})")
    if a.dry_run:
        print(text)
        return 0
    path = qualification_path(a.inventory.parent / "host_vars", a.host)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")
    release(a.host, hosts[a.host].get("ansible_host"), record[QUALIFICATION_KEY])
    return 0


if __name__ == "__main__":
    sys.exit(main())
