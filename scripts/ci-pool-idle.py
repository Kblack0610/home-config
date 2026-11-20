#!/usr/bin/env python3
"""Idle CI slots while another kind waited, and queue wait per pool, over a window.

The before and after reading of plan ci-one-pool-per-host (each host's cores are units lent
to whichever kind waits). Per host: slot-hours a slot sat idle while a job of a kind it cannot
take waited, which is what lending would recover; a slot's kinds are its caps, read from
github_runner_pool_slot (one row per cap), so a slot that takes every kind never counts. Per
pool: hours with a job waiting, and the p50 and p90 of the oldest wait over those minutes.
Waiting is fleet-wide (a waiting Unity job could run on any Unity host), so the idle column is
an upper bound per host.

Prometheus keeps about 3.5 days; for 7, read VictoriaMetrics:

    kubectl --context home-k3s -n monitoring port-forward svc/victoria-metrics 18428:8428
    scripts/ci-pool-idle.py --url http://localhost:18428 --days 7

Stdlib only. Tests: test_ci_pool_idle.py beside this file.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

STEP = 300  # seconds per sample: a job waits minutes, and 7 days stays under the point limits
TOPOLOGY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps/ci-topology/topology.json")


def shared_hosts(path=TOPOLOGY):
    """(kinds, hosts) from topology.json: the LAN hosts whose slots take more than one kind, the
    ones lending shares between kinds, and every kind they take."""
    with open(path, encoding="utf-8") as f:
        topo = json.load(f)["hosts"]
    caps = {h: {c for s in e["slots"] for c in s["caps"]} for h, e in topo.items() if e["site"] != "cloud"}
    hosts = sorted(h for h, c in caps.items() if len(c) > 1)
    return tuple(sorted(set().union(*(caps[h] for h in hosts)))), hosts


KINDS, _HOSTS = shared_hosts()  # pools our own hosts serve
HOSTS = "|".join(_HOSTS)
STRIP = "instance, pod, job, namespace, service, endpoint, container"


def query_range(url, expr, start, end, step=STEP):
    """{labels dict as tuple: {t: value}}, a day per request."""
    out = defaultdict(dict)
    t = start
    while t < end:
        stop = min(t + 86400, end)
        q = urllib.parse.urlencode({"query": expr, "start": t, "end": stop, "step": step})
        with urllib.request.urlopen(f"{url}/api/v1/query_range?{q}", timeout=120) as r:
            body = json.load(r)
        if body.get("status") != "success":
            sys.exit(f"FAIL: {expr}: {body.get('error')}")
        for s in body["data"]["result"]:
            key = tuple(sorted(s["metric"].items()))
            for ts, v in s["values"]:
                out[key][int(float(ts)) // step * step] = float(v)
        t = stop + step
    return out


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo = int(k)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (k - lo)


def idle_slots(pool_slot):
    """{(host, name, cap): {t: v}} from github_runner_pool_slot -> {(host, name): {t: {caps}}}:
    each idle slot at each sample, with every kind it takes."""
    idle = defaultdict(lambda: defaultdict(set))
    for (host, name, cap), series in pool_slot.items():
        for t in series:
            idle[(host, name)][t].add(cap)
    return idle


def tally(idle, waiting, oldest, times):
    """idle {(host, name): {t: {caps}}}, waiting {pool: {t: n}}, oldest {pool: {t: s}} ->
    ({host: idle slot-hours while a kind the slot cannot take waited}, {pool: (hours waiting, p50, p90)})."""
    lost = defaultdict(float)
    for (host, _), series in idle.items():
        for t, caps in series.items():
            if any(waiting.get(p, {}).get(t, 0) > 0 for p in KINDS if p not in caps):
                lost[host] += STEP / 3600
    waits = {}
    for pool in KINDS:
        at = [t for t in times if waiting.get(pool, {}).get(t, 0) > 0]
        ages = [oldest[pool][t] for t in at if t in oldest.get(pool, {})]
        waits[pool] = (len(at) * STEP / 3600, pct(ages, .5), pct(ages, .9))
    return dict(lost), waits


def by(series, *names):
    return {tuple(dict(k).get(n) for n in names) if len(names) > 1 else dict(k).get(names[0]): v
            for k, v in series.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("PROMETHEUS_URL", "https://prometheus.kblab.me"))
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--hosts", default=HOSTS, help="host regex")
    args = ap.parse_args(argv)
    end = int(time.time()) // STEP * STEP
    start = end - int(args.days * 86400) // STEP * STEP
    url = args.url.rstrip("/")
    idle = idle_slots(by(query_range(url, f'max by (host, name, pool) '
                                          f'(github_runner_pool_slot{{state="idle",host=~"{args.hosts}"}})', start, end),
                         "host", "name", "pool"))
    jobs = f'max without ({STRIP}) (github_actions_job_waiting{{stuck!="true"}})'
    waiting = by(query_range(url, f"count by (pool) ({jobs})", start, end), "pool")
    oldest = by(query_range(url, f"max by (pool) ({jobs})", start, end), "pool")
    times = range(start, end + STEP, STEP)  # query_range includes end
    seen = {t for s in waiting.values() for t in s} | {t for s in idle.values() for t in s}
    lost, waits = tally(idle, waiting, oldest, times)
    print(f"window {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(start))}..{time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(end))}"
          f" ({args.days:g} days, samples {len(seen) / len(times):.0%} of {STEP}s steps)")
    print("\nIdle slot-hours while a kind the slot cannot take waited")
    print(f"{'host':<18}{'slot-hours':>13}")
    for host in sorted({h for h, _ in idle}):
        print(f"{host:<18}{lost.get(host, 0):>13.1f}")
    print("\nQueue wait per pool (oldest waiting job, minutes, over the minutes a job waited)")
    print(f"{'pool':<14}{'hours waiting':>14}{'p50':>8}{'p90':>8}")
    for pool, (hours, p50, p90) in waits.items():
        fmt = lambda v: "-" if v is None else f"{v / 60:.0f}"
        print(f"{pool:<14}{hours:>14.1f}{fmt(p50):>8}{fmt(p90):>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
