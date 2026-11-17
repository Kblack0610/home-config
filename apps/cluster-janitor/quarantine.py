#!/usr/bin/env python3
"""Quarantine: taint a node that faults out of new work (host-tiers P3, docs/host-tiers.md).

A node is tainted kblab.me/degraded=true:NoSchedule when, since the latest of LOOKBACK_H ago,
its kblab.me/qualified-at annotation and its last quarantine (kblab.me/degraded-at), it shows
any of:
  - HostSoftLockup or HostMachineCheck: one 10m window (the Loki rule) that fired
  - HostGpuFirmwareFault: two such windows
  - its Ready condition dropped FLAPS_MIN or more times (kube-state-metrics)
  - a Mains supply offline for the last 5m (on battery)

The fault rules are run as written in Loki's ruler (apps/loki/rules-host-kernel-faults.yaml),
so their regexes live only there. The taint blocks new pods and never evicts: moving state
off a node is a human's call (fence first, docs/runbooks/move-local-path-pvc.md). Only
scripts/host-qualify.py removes it, after a fresh passing record, and stamps qualified-at.
A human may also remove it by hand (kubectl taint node <n> kblab.me/degraded-): faults from
before the last quarantine are not counted again, so it stays off until a new fault. The NodeQuarantined alert pages
ntfy-critical and, with fleet: fix, opens the queue item.

A query that fails exits non-zero (the Job fails, KubeJobFailed pages): never taint on a guess.
"""

import datetime as dt
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

TAINT = "kblab.me/degraded"
REASON = "kblab.me/degraded-reason"
DEGRADED_AT = "kblab.me/degraded-at"
QUALIFIED_AT = "kblab.me/qualified-at"
# Provisional, medium confidence (2026-11-16, host-tiers plan P3); revisit after the first
# quarantine, or when a real fault slips past these.
LOOKBACK_H = 24
STEP_S = 600                # the Loki rules count over [10m]: one point per window
RULE_HITS = {"HostSoftLockup": 1, "HostMachineCheck": 1, "HostGpuFirmwareFault": 2}
FLAPS_MIN = 2
MAX_QUARANTINED = int(os.environ.get("MAX_QUARANTINED", "2"))  # a bad query must not taint the cluster

PROM = os.environ.get("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc:9090").rstrip("/")
LOKI = os.environ.get("LOKI_URL", "http://loki.monitoring.svc:3100").rstrip("/")
NTFY = os.environ.get("NTFY_URL", "")
DRY_RUN = os.environ.get("DRY_RUN", "false") == "true"


def get(base, path, **params):
    url = f"{base}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    with urllib.request.urlopen(url, timeout=60) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        raise RuntimeError(f"{url}: {body.get('error')}")
    return body["data"]


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def since(node, now):
    """Count only what happened within the lookback, after the last requalification or quarantine."""
    notes = node["metadata"].get("annotations") or {}
    return max([now - LOOKBACK_H * 3600] + [parse_ts(notes.get(k)) or 0 for k in (QUALIFIED_AT, DEGRADED_AT)])


def tainted(node):
    return any(t.get("key") == TAINT for t in node.get("spec", {}).get("taints") or [])


def ip_index(nodes):
    return {a["address"]: n["metadata"]["name"] for n in nodes
            for a in n.get("status", {}).get("addresses", []) if a.get("type") == "InternalIP"}


def decide(nodes, rule_points, flaps, battery_instances, now):
    """Return {node: [reasons]} for untainted nodes that meet a trigger.

    rule_points: {rule: {host: [timestamps of windows that fired]}}; flaps: {node: Ready drops};
    battery_instances: node-exporter instances whose Mains supply has been offline for 5m.
    """
    by_ip = ip_index(nodes)
    on_battery = {by_ip.get(i.rsplit(":", 1)[0]) for i in battery_instances} - {None}
    out = {}
    for n in nodes:
        name = n["metadata"]["name"]
        if tainted(n):
            continue
        start = since(n, now)
        reasons = []
        for rule, need in RULE_HITS.items():
            hits = sum(1 for t in rule_points.get(rule, {}).get(name, []) if t > start)
            if hits >= need:
                reasons.append(f"{rule} x{hits}")
        if flaps.get(name, 0) >= FLAPS_MIN:
            reasons.append(f"Ready dropped {flaps[name]}x")
        if name in on_battery:
            reasons.append("on battery")
        if reasons:
            out[name] = reasons
    return out


def capped(nodes, decided):
    """Split decided into (taint, held) so no more than MAX_QUARANTINED nodes carry the taint."""
    room = max(0, MAX_QUARANTINED - sum(1 for n in nodes if tainted(n)))
    names = sorted(decided)
    return names[:room], names[room:]


def rule_points(now):
    groups = get(LOKI, "/prometheus/api/v1/rules")["groups"]
    rules = {r["name"]: r["query"] for g in groups if g["name"] == "host-kernel-faults"
             for r in g["rules"] if r["name"] in RULE_HITS}
    missing = set(RULE_HITS) - set(rules)
    if missing:
        raise RuntimeError(f"Loki ruler has no {', '.join(sorted(missing))}")
    out = {}
    for rule, query in rules.items():
        data = get(LOKI, "/loki/api/v1/query_range", query=query, step=STEP_S,
                   start=int((now - LOOKBACK_H * 3600) * 1e9), end=int(now * 1e9))
        out[rule] = {s["metric"].get("host", "?"): [float(t) for t, v in s["values"] if float(v) > 0]
                     for s in data["result"]}
    return out


def flap_counts(nodes, now):
    out = {}
    for n in nodes:
        name = n["metadata"]["name"]
        window = int(now - since(n, now))
        if window < STEP_S:
            continue
        res = get(PROM, "/api/v1/query", query=f'max(resets(kube_node_status_condition'
                  f'{{condition="Ready",status="true",node="{name}"}}[{window}s]))')["result"]
        if res:
            out[name] = int(float(res[0]["value"][1]))
    return out


def battery():
    res = get(PROM, "/api/v1/query", query='max_over_time(node_power_supply_online[5m]) == 0 and on '
              '(instance, power_supply) node_power_supply_info{type="Mains"}')["result"]
    return {s["metric"]["instance"] for s in res}


def post(priority, msg):
    print(f"JANITOR {msg}", flush=True)
    if not NTFY:
        return
    req = urllib.request.Request(NTFY, data=msg.encode(), headers={
        "Title": "cluster-janitor", "Priority": str(priority), "Tags": "broom"})
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except OSError as e:
        print(f"WARN ntfy post failed: {e}: {msg}", file=sys.stderr)


def kubectl(*args):
    if DRY_RUN:
        print("DRY-RUN kubectl " + " ".join(args))
        return
    subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True)


def quarantine(node, reasons, now):
    stamp = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    kubectl("annotate", "node", node, "--overwrite", f"{DEGRADED_AT}={stamp}", f"{REASON}={'; '.join(reasons)}")
    kubectl("taint", "node", node, f"{TAINT}=true:NoSchedule", "--overwrite")


def main():
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    nodes = json.loads(subprocess.run(["kubectl", "get", "nodes", "-o", "json"], check=True,
                                      capture_output=True, text=True).stdout)["items"]
    decided = decide(nodes, rule_points(now), flap_counts(nodes, now), battery(), now)
    act, held = capped(nodes, decided)
    for name in act:
        quarantine(name, decided[name], now)
        post(4, f"quarantined node {name} ({'; '.join(decided[name])}): tainted {TAINT}=true:NoSchedule, "
                "nothing evicted. Requalify with scripts/host-qualify.py to release it")
    for name in held:
        post(4, f"NEEDS YOU: node {name} meets quarantine ({'; '.join(decided[name])}) but {MAX_QUARANTINED} "
                "nodes are already or newly quarantined - not tainted; check the trigger queries")
    already = sorted(n["metadata"]["name"] for n in nodes if tainted(n))
    print(f"quarantine pass: evaluated {len(nodes)} nodes, quarantined {act or 'none'}, held {held or 'none'}, "
          f"already degraded {already or 'none'} (dry_run={str(DRY_RUN).lower()})")


if __name__ == "__main__":
    main()
