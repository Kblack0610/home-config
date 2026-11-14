#!/usr/bin/env python3
"""Read what a host can still take, from 7 days of its own metrics, before placing work on it.

Run it before adding a runner slot, a pinned pod, a game server, a PV or a fleet worker to a
host, and quote its output in the PR. It prints the host's CPUs, load per CPU (p95 and max of
the 15-minute load), peak CPU temperature, lowest available memory and OOM kills, and a verdict:
ROOM (exit 0) only when every reading is under its limit below, else FULL (exit 1) with the
readings that failed. A host in no_ci_hosts (ansible/inventory.yml) takes no CI whatever this says.

  scripts/host-headroom.py hp-victus
  scripts/host-headroom.py --all          # every host, one line each

Hosts are the `host` names of the cc:host_instance series (apps/monitoring), the same names as
the inventory. Stdlib only. PROMETHEUS_URL overrides https://prometheus.kblab.me.

Why: on 2026-11-13 hp-victus carried Forgejo, Frigate and six CI runners at load15 41.5 on 24
threads and 90C and went NotReady. Its slots had been raised from two to five the day before on
an average, without reading its peaks or its temperature.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

PROM = os.environ.get("PROMETHEUS_URL", "https://prometheus.kblab.me").rstrip("/")
WINDOW = "7d"
# Provisional, medium confidence (set 2026-11-13 from the 7-day replay in
# prometheus-rules-capacity.yaml; revisit when NodeLoadHigh fires on a host this calls ROOM).
LIMITS = {
    "load_p95": 0.70,    # p95 of load15 per CPU: room for a burst before NodeLoadHigh (1.0x)
    "load_max": 1.00,    # NodeLoadHigh's threshold
    "temp_max": 85.0,    # roles/thermal-cap's target; NodeCpuHot fires at 90
    "mem_min": 0.20,     # lowest MemAvailable share; NodeMemoryLow fires at 0.15
    "oom_kills": 0,
}


def query(expr):
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=30) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        sys.exit(f"FAIL: Prometheus said {body.get('error')} for {expr}")
    return body["data"]["result"]


def scalar(expr):
    res = query(expr)
    return float(res[0]["value"][1]) if res else None


def hosts():
    return {r["metric"]["host"]: r["metric"]["instance"] for r in query("cc:host_instance")}


def readings(inst):
    i = f'instance="{inst}"'
    ratio = f'(node_load15{{{i}}} / scalar(count(node_cpu_seconds_total{{{i},mode="idle"}})))'
    chips = f'node_hwmon_chip_names{{{i},chip_name=~"coretemp|k10temp"}}'
    return {
        "up": scalar(f'max(up{{{i}}})'),
        "cpus": scalar(f'count(node_cpu_seconds_total{{{i},mode="idle"}} offset 1h)')
                or scalar(f'count(last_over_time(node_cpu_seconds_total{{{i},mode="idle"}}[{WINDOW}]))'),
        "load_p95": scalar(f"quantile_over_time(0.95, {ratio}[{WINDOW}:5m])"),
        "load_max": scalar(f"max_over_time({ratio}[{WINDOW}:5m])"),
        "temp_max": scalar(f"max(max_over_time(node_hwmon_temp_celsius{{{i}}}[{WINDOW}])"
                           f" * on (instance, chip) group_left (chip_name) last_over_time({chips}[{WINDOW}]))"),
        "mem_min": scalar(f"min_over_time((node_memory_MemAvailable_bytes{{{i}}}"
                          f" / node_memory_MemTotal_bytes{{{i}}})[{WINDOW}:5m])"),
        "oom_kills": scalar(f"sum(increase(node_vmstat_oom_kill{{{i}}}[{WINDOW}]))"),
    }


def verdict(r):
    """(ROOM|FULL, reasons). A missing load reading is never room; temperature, memory and OOM
    may be absent (no coretemp/k10temp chip on the Pis; the mac-mini exports neither of the others)."""
    why = []
    if not r["up"]:
        why.append("down now")
    for key in ("load_p95", "load_max"):
        if r[key] is None:
            why.append(f"no {key} reading")
    for key in ("load_p95", "load_max", "temp_max", "oom_kills"):
        if r[key] is not None and r[key] > LIMITS[key]:
            why.append(f"{key} {r[key]:.2f} > {LIMITS[key]}")
    if r["mem_min"] is not None and r["mem_min"] < LIMITS["mem_min"]:
        why.append(f"mem_min {r['mem_min']:.2f} < {LIMITS['mem_min']}")
    return ("FULL" if why else "ROOM"), why


def fmt(v, spec):
    return "-" if v is None else format(v, spec)


def line(host, r):
    state, why = verdict(r)
    spare = None if r["load_p95"] is None or not r["cpus"] else (LIMITS["load_p95"] - r["load_p95"]) * r["cpus"]
    return (f"{host:<22} {state:<4}  cpus {fmt(r['cpus'], '.0f')}  load/cpu p95 {fmt(r['load_p95'], '.2f')}"
            f" max {fmt(r['load_max'], '.2f')}  spare cpus {fmt(spare, '.1f')}  temp max {fmt(r['temp_max'], '.0f')}C"
            f"  mem avail min {fmt(r['mem_min'], '.0%')}  oom {fmt(r['oom_kills'], '.0f')}"
            + (f"  [{'; '.join(why)}]" if why else ""))


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    known = hosts()
    if not known:
        sys.exit("FAIL: no cc:host_instance series; the mapping is broken, not the hosts")
    names = sorted(known) if argv[1] == "--all" else [argv[1]]
    rc = 0
    for host in names:
        if host not in known:
            sys.exit(f"FAIL: {host} is not a cc:host_instance host: {', '.join(sorted(known))}")
        r = readings(known[host])
        rc |= verdict(r)[0] != "ROOM"
        print(line(host, r))
    print(f"window {WINDOW}, limits {LIMITS}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
