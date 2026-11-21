#!/usr/bin/env python3
"""Each CI job kind's measured share of a slot: cores and memory p50 and p90, and its weight.

The numbers that size the standing small-job seats (ci_small_job_size).
A sample is one busy runner at one step (cc:job_kind_cores and cc:job_kind_mem, which carry
the kind github-runner-exporter's ci_runner_job gives it). Its share is cores over the CPUs of
the slot it runs on and memory over that slot's MemoryMax,
both from ansible/inventory.yml (ci_unity_slot_cpus, ci_unity_slot_memory_max). A kind's weight
is the larger of its p90 cores share and p90 memory share, at most 1. A kind with no samples
prints "-", never 0. Mac runners have no slot, so they get figures and no share.

Prometheus keeps about 3.5 days; for 7, read VictoriaMetrics:

    kubectl --context home-k3s -n monitoring port-forward svc/victoria-metrics 18428:8428
    scripts/ci-kind-weights.py --url http://localhost:18428 --days 7

Stdlib plus PyYAML (the inventory). Tests: test_ci_kind_weights.py beside this file.
"""
import argparse
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


idle = _load("ci_pool_idle", "ci-pool-idle.py")
topo = _load("render_ci_topology", "render-ci-topology.py")

# A job runs minutes, and a light one can finish inside idle's 300s step; a day of 60s steps
# is 1,440 points a series, under the query point limits.
STEP = 60
# The kinds always listed, so one with no samples shows as "-" rather than vanishing.
KINDS = ("unity", "unity-build", "unity-wide", "heavy", "light", "forgejo", "repo", "macos")
GIB = 2 ** 30


def slot_sizes(topology, hosts):
    """{runner name: (CPUs, memory cap in bytes)} for every Unity slot the inventory sizes. Either
    figure is None where the inventory gives none."""
    sizes = {}
    for host in topology:
        v = hosts.get(host, {})
        total, _ = topo.unity_layout(v)
        cpus, mem = topo.slot_budget(v, total)
        for n in range(1, total + 1):
            c = len(cpus[n - 1]) if n <= len(cpus) else None
            m = mem[n - 1] * GIB if n <= len(mem) and mem[n - 1] else None
            sizes[topo.unity_slot(host, n)] = (c, m)
    return sizes


def weights(cores, mem, sizes):
    """cores and mem {(runner, kind): {t: value}} -> {kind: figures}: samples, cores p50/p90,
    memory p50/p90 in bytes, p90 of each share, and the weight. A missing figure is None."""
    def samples(series, kind, size=None):
        """Every sample of a kind, or with size (0 CPUs, 1 memory) each over its slot's, where known."""
        return [v / sizes[r][size] if size is not None else v for (r, k), s in series.items()
                if k == kind and (size is None or sizes.get(r, (None, None))[size]) for v in s.values()]

    out = {}
    for kind in sorted({k for _, k in cores} | {k for _, k in mem}):
        c, m, cs, ms = samples(cores, kind), samples(mem, kind), samples(cores, kind, 0), samples(mem, kind, 1)
        share = [x for x in (idle.pct(cs, .9), idle.pct(ms, .9)) if x is not None]
        out[kind] = {"samples": len(c), "cores_p50": idle.pct(c, .5), "cores_p90": idle.pct(c, .9),
                     "mem_p50": idle.pct(m, .5), "mem_p90": idle.pct(m, .9),
                     "cores_share_p90": idle.pct(cs, .9), "mem_share_p90": idle.pct(ms, .9),
                     "weight": min(1.0, max(share)) if share else None}
    return out


def table(w):
    """The report's rows, every KINDS entry first, then any other kind seen."""
    def f(v, fmt):
        return "-" if v is None else fmt.format(v)
    lines = [f"{'kind':<12}{'samples':>8}{'cores p50':>10}{'p90':>6}{'mem GiB p50':>12}{'p90':>6}"
             f"{'cores/slot':>11}{'mem/slot':>9}{'weight':>8}"]
    for kind in list(KINDS) + sorted(set(w) - set(KINDS)):
        r = w.get(kind, {})
        mg = {k: None if r.get(k) is None else r[k] / GIB for k in ("mem_p50", "mem_p90")}
        lines.append(f"{kind:<12}{r.get('samples', 0) or '-':>8}{f(r.get('cores_p50'), '{:.2f}'):>10}"
                     f"{f(r.get('cores_p90'), '{:.2f}'):>6}{f(mg['mem_p50'], '{:.1f}'):>12}{f(mg['mem_p90'], '{:.1f}'):>6}"
                     f"{f(r.get('cores_share_p90'), '{:.2f}'):>11}{f(r.get('mem_share_p90'), '{:.2f}'):>9}"
                     f"{f(r.get('weight'), '{:.2f}'):>8}")
    return lines


def by_runner_kind(series):
    return {(dict(k).get("runner"), dict(k).get("kind")): v for k, v in series.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("PROMETHEUS_URL", "https://prometheus.kblab.me"))
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--inventory", default=topo.INVENTORY)
    ap.add_argument("--topology", default=topo.OUT)
    args = ap.parse_args(argv)
    end = int(time.time()) // STEP * STEP
    start = end - int(args.days * 86400) // STEP * STEP
    url = args.url.rstrip("/")
    with open(args.topology) as f:
        topology = json.load(f)["hosts"]
    hosts, _ = topo.load_inventory(args.inventory)
    sizes = slot_sizes(topology, hosts)
    q = lambda m: by_runner_kind(idle.query_range(url, f"max by (runner, kind) ({m})", start, end, STEP))
    w = weights(q("cc:job_kind_cores"), q("cc:job_kind_mem"), sizes)
    print(f"window {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(start))}..{time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(end))}"
          f" ({args.days:g} days, {STEP}s steps, one sample per busy runner per step)")
    print("\n".join(table(w)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
