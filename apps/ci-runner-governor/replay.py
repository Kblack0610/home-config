#!/usr/bin/env python3
"""Replay the governor's home-host decisions, live, over a window of recorded readings.

Runs governor.decide() once a minute over the last --days of the same series the governor
reads (its QUERIES, the pool runners' busy state, the waiting pool jobs), with the slot
layout from config.json and apps/ci-topology/topology.json, every LAN host live (no
restore) and no pins: what turning DRY_RUN off would have done. Prometheus keeps about
3.5 days at its 5GB cap, so point --url at VictoriaMetrics (apps/victoria-metrics, 2y):

    kubectl --context home-k3s -n monitoring port-forward svc/victoria-metrics 18428:8428
    python3 apps/ci-runner-governor/replay.py --url http://localhost:18428

Per host it prints the verdicts, withholds and admits, flaps (an admit within 30 minutes
of a withhold), the most slots held at once, slot-minutes held, and slot-minutes held while
pool jobs waited and the host was not HOT or FULL that minute (a slot held for nothing).
A host with no CPU pressure series reads STEADY "no reading" and never acts; the
coverage column says how many minutes had every reading. Stdlib only.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TOPOLOGY", os.path.join(HERE, "..", "ci-topology", "topology.json"))
import governor  # noqa: E402

STEP = 60  # the governor's CYCLE_SECONDS
FLAP_SECONDS = 1800


def query_range(url, expr, start, end):
    """{labels tuple: {t: value}}, fetched a day at a time to stay under the point limits."""
    out = defaultdict(dict)
    t = start
    while t < end:
        stop = min(t + 86400, end)
        q = urllib.parse.urlencode({"query": expr, "start": t, "end": stop, "step": STEP})
        with urllib.request.urlopen(f"{url}/api/v1/query_range?{q}", timeout=120) as r:
            body = json.load(r)
        if body.get("status") != "success":
            raise ValueError(f"{expr}: {body.get('error')}")
        for s in body["data"]["result"]:
            key = tuple(sorted(s["metric"].items()))
            for ts, v in s["values"]:
                out[key][int(float(ts)) // STEP * STEP] = float(v)
        t = stop + STEP
    return out


def replay(url, days, cfg):
    end = int(time.time()) // STEP * STEP
    start = end - days * 86400
    lan = set(cfg["hosts"])
    readings = {}
    for k, q in governor.QUERIES.items():
        per = defaultdict(dict)
        for key, series in query_range(url, q, start, end).items():
            m = dict(key)
            per[m.get("nodename")] = series
        readings[k] = per
    # The LAN hosts' pool slots, from the topology the live governor reads.
    with open(os.environ["TOPOLOGY"]) as f:
        topo = json.load(f)["hosts"]
    slots = {s["name"]: h for h in lan for s in topo[h]["slots"] if cfg["pool_label"] in s["caps"]}
    busy = defaultdict(dict)
    for key, series in query_range(url, 'max by (name) (github_runner_state{state="running"})', start, end).items():
        name = dict(key).get("name")
        if name in slots:
            busy[name] = series
    waiting = defaultdict(list)
    q = f'github_actions_job_waiting{{pool=~"{"|".join(governor.pools(cfg))}",stuck!="true"}}'
    for key, series in query_range(url, q, start, end).items():
        labels = governor.label_set(dict(key).get("labels", "") or cfg["pool_label"])
        for t, v in series.items():
            if v > 0:
                waiting[t].append(labels)

    runners = {n: {"host": h, "busy": False, "elapsed": 0.0, "withheld": False, "labels": frozenset()}
               for n, h in slots.items()}
    last_action, since = {}, {}
    st = {h: Counter() for h in lan}
    for t in range(start, end, STEP):
        hosts = {k: {h: s[t] for h, s in per.items() if t in s} for k, per in readings.items()}
        for n, r in runners.items():
            r["busy"] = busy[n].get(t, 0) > 0
        for h in lan:
            if all(hosts[k].get(h) is not None for k in ("idle", "psi", "mem")):
                st[h]["coverage"] += 1
        q = waiting.get(t, [])
        decisions = governor.decide(cfg, hosts, runners, q, last_action, t, {}, {}, ())
        queued = len(governor.pool_jobs(q, governor.build_marks(cfg))) > 0
        for host, action, runner, v, why in decisions:
            if host not in lan:
                continue
            st[host][f"verdict {v}"] += 1
            if action == "withhold":
                runners[runner]["withheld"] = True
                since[runner] = t
                last_action[host] = t
                st[host]["withholds"] += 1
            elif action == "admit":
                if t - since.get(runner, -FLAP_SECONDS - 1) <= FLAP_SECONDS:
                    st[host]["flaps"] += 1
                runners[runner]["withheld"] = False
                last_action[host] = t
                st[host]["admits"] += 1
            held = sum(1 for r in runners.values() if r["host"] == host and r["withheld"])
            st[host]["held_max"] = max(st[host]["held_max"], held)
            st[host]["slot_minutes_held"] += held
            if queued and v not in ("HOT", "FULL"):
                st[host]["slot_minutes_held_for_nothing"] += held
    return st, (end - start) // STEP, slots


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("PROMETHEUS_URL", "http://localhost:18428"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    a = ap.parse_args()
    cfg = governor.load_config(a.config)
    st, minutes, slots = replay(a.url.rstrip("/"), a.days, cfg)
    print(f"replay: {a.days}d, {minutes} cycles, {len(slots)} LAN pool slots, cooldown {cfg['cooldown_seconds']}s")
    for h in sorted(st):
        c = st[h]
        verdicts = ", ".join(f"{k[8:]} {v}" for k, v in sorted(c.items()) if k.startswith("verdict "))
        print(f"{h}: coverage {c['coverage'] / minutes:.0%}; {verdicts}; withholds {c['withholds']}, admits {c['admits']}, "
              f"flaps {c['flaps']}, held max {c['held_max']}, slot-min held {c['slot_minutes_held']}, "
              f"held while jobs waited and not hot {c['slot_minutes_held_for_nothing']}")


if __name__ == "__main__":
    main()
