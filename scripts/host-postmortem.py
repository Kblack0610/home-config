#!/usr/bin/env python3
"""Print what happened to a host around a time: metrics, kernel faults, its last words.

Run it first when a host hangs, reboots or goes NotReady. It prints, for the window before and
after the time:

  1. A 5-minute table from Prometheus: up, CPU busy, load15 per CPU, available memory, swap,
     memory and IO pressure, NIC traffic and errors, CPU package and NVMe temperature.
  2. The last minute node-exporter answered a scrape.
  3. Kernel lines matching each live Loki rule in group host-kernel-faults (read from Loki's
     ruler, so the regexes stay in apps/loki/rules-host-kernel-faults.yaml only).
  4. The last journal line the host shipped to Loki.
  5. Its netconsole lines on pi5-master (roles/crash-evidence), which keep running after the
     journal stops shipping.

  scripts/host-postmortem.py hp-victus 2026-11-13T23:30Z
  scripts/host-postmortem.py razer-laptop now --before 2h

Hosts are the `host` names of cc:host_instance, the same names as the inventory. Stdlib only.
PROMETHEUS_URL overrides https://prometheus.kblab.me (7 days of retention). Loki is read
through the home-k3s API server proxy with kubectl.

Why: on 2026-11-13 hp-victus hung and it took a dozen hand-run queries to rule out the network
and memory and find the NVIDIA GSP fault and the NVMe drop that came first.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

PROM = os.environ.get("PROMETHEUS_URL", "https://prometheus.kblab.me").rstrip("/")
LOKI = "/api/v1/namespaces/monitoring/services/loki:3100/proxy"
KUBE_CONTEXT = "home-k3s"
RULE_GROUP = "host-kernel-faults"
NETCONSOLE_RECEIVER = "kblack0610@192.168.1.20"  # pi5-master, roles/netconsole-receiver
STEP = 300
LINES_PER_RULE = 20
VIRTUAL_NICS = "lo|veth.*|cni.*|flannel.*|docker.*|br-.*|virbr.*|tailscale.*|cali.*|vxlan.*"

COLUMNS = [
    ("up", "max(up{%(i)s})", "{:.0f}"),
    ("cpu%", "100 * (1 - avg(rate(node_cpu_seconds_total{%(i)s,mode=\"idle\"}[5m])))", "{:.0f}"),
    ("load/cpu", "node_load15{%(i)s} / scalar(count(node_cpu_seconds_total{%(i)s,mode=\"idle\"}))", "{:.2f}"),
    ("memGiB", "node_memory_MemAvailable_bytes{%(i)s} / 2^30", "{:.1f}"),
    ("swapGiB", "(node_memory_SwapTotal_bytes{%(i)s} - node_memory_SwapFree_bytes{%(i)s}) / 2^30", "{:.1f}"),
    ("psiMem%", "100 * rate(node_pressure_memory_waiting_seconds_total{%(i)s}[5m])", "{:.1f}"),
    ("psiIO%", "100 * rate(node_pressure_io_waiting_seconds_total{%(i)s}[5m])", "{:.1f}"),
    ("rxMB/s", "sum(rate(node_network_receive_bytes_total{%(i)s,%(nic)s}[5m])) / 1e6", "{:.1f}"),
    ("txMB/s", "sum(rate(node_network_transmit_bytes_total{%(i)s,%(nic)s}[5m])) / 1e6", "{:.1f}"),
    ("nicErr/s", "sum(rate(node_network_receive_errs_total{%(i)s,%(nic)s}[5m]) + rate(node_network_transmit_errs_total{%(i)s,%(nic)s}[5m]))", "{:.2f}"),
    # The raw form of instance:node_cpu_package_temp:celsius, which has no history before 2026-11-13.
    ("cpuC", "max(node_hwmon_temp_celsius{%(i)s} * on (instance, chip) group_left (chip_name) "
             "node_hwmon_chip_names{%(i)s,chip_name=~\"coretemp|k10temp\"})", "{:.0f}"),
    ("nvmeC", "max(node_hwmon_temp_celsius{%(i)s,chip=~\"nvme.*\",sensor=\"temp1\"})", "{:.0f}"),
]


def prom(path, **params):
    url = f"{PROM}/api/v1/{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        sys.exit(f"FAIL: Prometheus said {body.get('error')} for {params}")
    return body["data"]["result"]


def loki(path, **params):
    raw = f"{LOKI}{path}?" + urllib.parse.urlencode(params) if params else f"{LOKI}{path}"
    out = subprocess.run(["kubectl", "--context", KUBE_CONTEXT, "get", "--raw", raw],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        sys.exit(f"FAIL: Loki via kubectl: {out.stderr.strip()}")
    return json.loads(out.stdout)["data"]


def ns(t):
    return str(int(t.timestamp() * 1e9))


def utc(t):
    return t.strftime("%Y-%m-%d %H:%M:%SZ")


def parse_time(s):
    if s == "now":
        return dt.datetime.now(dt.timezone.utc)
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.astimezone()  # a bare time is local


def parse_span(s):
    unit = {"m": 60, "h": 3600}[s[-1]]
    return dt.timedelta(seconds=int(s[:-1]) * unit)


def instance_of(host, at):
    for r in prom("query", query="cc:host_instance", time=at.timestamp()):
        if r["metric"].get("host") == host:
            return r["metric"]["instance"]
    sys.exit(f"FAIL: no cc:host_instance series for host {host!r} at {utc(at)}")


def metrics_table(inst, start, end):
    sel = {"i": f'instance="{inst}"', "nic": f'device!~"{VIRTUAL_NICS}"'}
    rows = {}
    for name, expr, _ in COLUMNS:
        res = prom("query_range", query=expr % sel, start=start.timestamp(), end=end.timestamp(), step=STEP)
        for ts, val in (res[0]["values"] if res else []):
            rows.setdefault(int(ts), {})[name] = float(val)
    print("time(UTC) " + " ".join(f"{n:>8}" for n, _, _ in COLUMNS))
    t = int(start.timestamp())
    while t <= end.timestamp():
        vals = rows.get(t, {})
        cells = [fmt.format(vals[n]) if n in vals else "-" for n, _, fmt in COLUMNS]
        print(dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%m-%d %H:%M") + " "
              + " ".join(f"{c:>8}" for c in cells))
        t += STEP
    up = prom("query_range", query=f'max(up{{instance="{inst}"}}) == 1',
              start=start.timestamp(), end=end.timestamp(), step=60)
    last = max((int(ts) for r in up for ts, _ in r["values"]), default=None)
    seen = utc(dt.datetime.fromtimestamp(last, dt.timezone.utc)) if last else "never in this window"
    print(f"\nlast successful scrape: {seen}")


def fault_rules():
    groups = loki("/prometheus/api/v1/rules")["groups"]
    rules = [r for g in groups if g["name"] == RULE_GROUP for r in g["rules"]]
    if not rules:
        sys.exit(f"FAIL: Loki has no rule group {RULE_GROUP}")
    out = []
    for r in rules:
        q = r["query"]
        regex = q[q.index('|~ "') + 4:q.index('"[')]
        out.append((r["name"], regex.encode().decode("unicode_escape")))
    return out


def kernel_faults(host, start, end):
    for name, regex in fault_rules():
        expr = f'{{host="{host}",transport="kernel"}} |~ {json.dumps(regex)}'
        res = loki("/loki/api/v1/query_range", query=expr, start=ns(start), end=ns(end),
                   limit=LINES_PER_RULE, direction="backward")["result"]
        lines = sorted((int(ts), line) for s in res for ts, line in s["values"])
        print(f"\n{name}: {len(lines)} line(s){' (newest shown, capped)' if len(lines) >= LINES_PER_RULE else ''}")
        for ts, line in lines:
            print(f"  {utc(dt.datetime.fromtimestamp(ts / 1e9, dt.timezone.utc))} {line[:200]}")


def last_shipped(host, start, end):
    res = loki("/loki/api/v1/query_range", query=f'{{host="{host}"}}', start=ns(start), end=ns(end),
               limit=1, direction="backward")["result"]
    newest = max(((int(ts), line, s["stream"].get("unit", s["stream"].get("transport", "")))
                  for s in res for ts, line in s["values"]), default=None)
    if newest:
        ts, line, unit = newest
        print(f"\nlast line shipped to Loki: {utc(dt.datetime.fromtimestamp(ts / 1e9, dt.timezone.utc))} [{unit}] {line[:200]}")
    else:
        print("\nlast line shipped to Loki: none in this window")


def netconsole(inst, start, end):
    ip = inst.rsplit(":", 1)[0]
    cmd = (f"journalctl -u netconsole-receiver --utc --no-pager -o short-iso "
           f"--since '{start:%Y-%m-%d %H:%M:%S}' --until '{end:%Y-%m-%d %H:%M:%S}' | grep -F ' {ip} ' | tail -n 40")
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", NETCONSOLE_RECEIVER, cmd],
                         capture_output=True, text=True, timeout=60)
    lines = out.stdout.strip().splitlines()
    print(f"\nnetconsole on pi5-master from {ip}: {len(lines)} line(s), last 40 shown")
    for line in lines:
        print(f"  {line[:220]}")
    if out.returncode not in (0, 1):  # grep exits 1 on no match
        print(f"  (ssh failed: {out.stderr.strip()[:200]})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("host", help="inventory host name, e.g. hp-victus")
    ap.add_argument("time", help="ISO time (2026-11-13T23:30Z; no zone means local) or 'now'")
    ap.add_argument("--before", default="60m", help="window before the time (e.g. 60m, 2h)")
    ap.add_argument("--after", default="15m", help="window after the time")
    a = ap.parse_args()
    at = parse_time(a.time).astimezone(dt.timezone.utc)
    start, end = at - parse_span(a.before), at + parse_span(a.after)
    inst = instance_of(a.host, start)
    print(f"{a.host} ({inst}), {utc(start)} to {utc(end)}\n")
    metrics_table(inst, start, end)
    kernel_faults(a.host, start, end)
    last_shipped(a.host, start, end)
    netconsole(inst, start.replace(tzinfo=None), end.replace(tzinfo=None))


if __name__ == "__main__":
    main()
