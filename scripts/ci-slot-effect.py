#!/usr/bin/env python3
"""Compare platform CI job times and host load before and after a Linux CI slot change.

Run it a day or more after changing a Linux CI host's slots or CI_SLOT_CPUS, and quote its
output in the PR or plan that made the change. It needs `gh` logged in and Prometheus.

  scripts/ci-slot-effect.py --before 2026-11-13T17:30Z..2026-11-14T22:00Z \\
                            --after 2026-11-15T00:00Z..2026-11-17T04:00Z

Jobs: each successful Build, Type Check and Test job of platform's ci.yml that ran on one of
--hosts, with its main step's time (the turbo build or typecheck, the vitest run), its run time,
its queue time and how many other light or heavy slots on the same host were busy while it ran
(github_runner_state, so jobs of every repo count). "lone" counts jobs with none busy. Scope
"full" is a push run (the whole repo) or a coverage test shard; "affected" is a scoped PR run.
Compare a scope with itself only. The Test job runs vitest, which CI_SLOT_CPUS does not cap,
so it is the control for a cap change.

Hosts: CI hours a day, slots busy, load15 per CPU, the platform slice's cores busy (from
ci_linux_cpus in ansible/inventory.yml) and host CPU pressure while CI ran, and minutes of
NodeLoadHigh. The slice's own cpu.pressure is not exported, so it is not here.

Stdlib plus PyYAML (for the inventory). PROMETHEUS_URL overrides https://prometheus.kblab.me.

Why: CI_SLOT_CPUS (ar-2435, #985) capped each platform job's turbo concurrency to its slot's
share of the slice. Whether lone jobs lost more than the queueing saved was hand-counted the
first time; this keeps the next reading comparable.
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from host_inventory import cpu_set, load_inventory  # noqa: E402

PROM = os.environ.get("PROMETHEUS_URL", "https://prometheus.kblab.me").rstrip("/")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO_ROOT, "ansible/inventory.yml")
STEP = 60  # seconds per sample; Prometheus caps a range at 11000 points, so 7 days
MAX_WINDOW = 7 * 86400
LONE_BELOW = 0.25  # mean other busy slots under which a job ran alone
# platform ci.yml job name prefix -> (label, main step name prefixes)
JOBS = {
    "\U0001f528 Build": ("Build", ("Build all packages",)),
    "\U0001f50d Type Check": ("Typecheck", ("Build shared packages", "Type check")),
    "\U0001f9ea Test (Shard": ("Test", ("Run tests", "Run affected tests")),
}


def ts(text):
    """Epoch seconds of an ISO time; one with no offset is UTC, never local time."""
    when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return (when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)).timestamp()


def window(text):
    start, sep, end = text.partition("..")
    if not sep:
        raise argparse.ArgumentTypeError(f"{text}: expected START..END")
    a, b = ts(start), ts(end)
    if not 0 < b - a <= MAX_WINDOW:
        raise argparse.ArgumentTypeError(f"{text}: a window runs forward and spans at most 7 days")
    return a, b


def pct(xs, p):
    """Linearly interpolated percentile, so pct(xs, .5) is the median."""
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo = int(k)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (k - lo)


def gh_json(path):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=120)
    if out.returncode:
        sys.exit(f"FAIL: gh api {path}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def paged(path, key):
    """Every item under key across the pages of a GitHub list endpoint."""
    items, page = [], 1
    while True:
        batch = gh_json(f"{path}&per_page=100&page={page}")[key]
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def fetch_jobs(repo, workflow, start, end):
    """[(run event, job)] of every run of the workflow created in [start - 1h, end]: a run queued
    just before the window can start its jobs inside it."""
    iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = paged(f"repos/{repo}/actions/workflows/{workflow}/runs?created={iso(start - 3600)}..{iso(end)}", "workflow_runs")
    if len(runs) >= 1000:
        sys.exit(f"FAIL: {len(runs)} runs in one window; GitHub returns at most 1000 for a created filter, so use a shorter window")
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        bodies = pool.map(lambda r: paged(f"repos/{repo}/actions/runs/{r['id']}/jobs?filter=all", "jobs"), runs)
        return [(run["event"], job) for run, jobs in zip(runs, bodies) for job in jobs]


def prom_range(expr, start, end):
    """[(labels, {sample time: value})] for expr over [start, end] at STEP."""
    url = f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": expr, "start": int(start) // STEP * STEP, "end": int(end), "step": STEP})
    with urllib.request.urlopen(url, timeout=60) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        sys.exit(f"FAIL: Prometheus said {body.get('error')} for {expr}")
    return [(r["metric"], {int(t): float(v) for t, v in r["values"]}) for r in body["data"]["result"]]


def busy_slots(host, start, end):
    """{runner name: set of sample times it ran a job} for the host's light and heavy slots."""
    expr = (f'max by (name) (github_runner_state{{forge="github",state="running",host="{host}",'
            f'pool=~"light|heavy"}})')
    return {m["name"]: set(vals) for m, vals in prom_range(expr, start, end)}


def others_busy(busy, runner, start, end):
    """Mean number of the host's other slots running a job over [start, end]."""
    grid = range(int(start) // STEP * STEP, int(end) // STEP * STEP + 1, STEP)
    counts = [sum(t in times for name, times in busy.items() if name != runner) for t in grid]
    return sum(counts) / len(counts)


def classify(event, job):
    """(label, scope, main step seconds) of a platform CI job, or None when it is not one we time."""
    label_steps = next((v for k, v in JOBS.items() if job["name"].startswith(k)), None)
    if not label_steps or job.get("conclusion") != "success":
        return None
    label, prefixes = label_steps
    steps = [s for s in job.get("steps") or [] if s["name"].startswith(prefixes) and s.get("conclusion") == "success"]
    if not steps:  # its main step was skipped or renamed: no time to compare
        return None
    secs = sum(ts(s["completed_at"]) - ts(s["started_at"]) for s in steps)
    if label == "Test":
        full = any(s["name"].startswith("Run tests") for s in steps)
    else:
        full = event == "push"
    return label, "full" if full else "affected", secs


def job_rows(jobs, hosts, windows, busy):
    rows = []
    for event, job in jobs:
        runner = job.get("runner_name") or ""
        host = next((h for h in hosts if runner.startswith(h + "-")), None)
        kind = classify(event, job) if host else None
        if not kind:
            continue
        start, end = ts(job["started_at"]), ts(job["completed_at"])
        win = next((w for w, (a, b) in windows.items() if a <= start < b), None)
        if not win:
            continue
        label, scope, step = kind
        rows.append({"host": host, "job": label, "scope": scope, "win": win, "step": step,
                     "run": end - start, "queue": start - ts(job["created_at"]),
                     "others": others_busy(busy[host], runner, start, end)})
    return rows


def minutes(v):
    return "-" if v is None else f"{v / 60:.1f}"


def print_jobs(rows, hosts, windows):
    print("Jobs (minutes; others = mean other light/heavy slots busy on the host while it ran)")
    print(f"{'host':<18}{'job':<10}{'scope':<9}{'window':<8}{'n':>4}{'lone':>5}{'step p50':>9}{'p90':>6}"
          f"{'job p50':>8}{'queue p50':>10}{'p90':>6}{'others':>7}")
    for host in hosts:
        for label, _ in JOBS.values():
            for scope in ("full", "affected"):
                for win in windows:
                    xs = [r for r in rows if (r["host"], r["job"], r["scope"], r["win"]) == (host, label, scope, win)]
                    if not xs:
                        continue
                    lone = sum(r["others"] < LONE_BELOW for r in xs)
                    print(f"{host:<18}{label:<10}{scope:<9}{win:<8}{len(xs):>4}{lone:>5}"
                          f"{minutes(pct([r['step'] for r in xs], .5)):>9}{minutes(pct([r['step'] for r in xs], .9)):>6}"
                          f"{minutes(pct([r['run'] for r in xs], .5)):>8}"
                          f"{minutes(pct([r['queue'] for r in xs], .5)):>10}{minutes(pct([r['queue'] for r in xs], .9)):>6}"
                          f"{statistics.mean(r['others'] for r in xs):>7.1f}")


def host_summary(load, slice_busy, psi, alert, busy, start, end):
    """Readings of one host over one window; CI-time readings only where a slot ran a job."""
    samples = [t for t in load if start <= t < end]
    if not samples:
        return None
    ci = [t for t in samples if any(t in times for times in busy.values())]
    hours = (end - start) / 3600
    lo = [load[t] for t in samples]
    sl = [slice_busy[t] for t in ci if t in slice_busy]
    ps = [psi[t] for t in ci if t in psi]
    return {
        "coverage": len(samples) * STEP / (end - start),
        "ci_h_day": len(ci) * STEP / 3600 / hours * 24,
        "slots_busy": sum(sum(t in times for times in busy.values()) for t in ci) / len(ci) if ci else None,
        "load_p50": pct(lo, .5), "load_p95": pct(lo, .95), "load_max": max(lo),
        "over1_day": sum(x > 1.0 for x in lo) * STEP / 60 / hours * 24,
        "slice_mean": sum(sl) / len(sl) if sl else None, "slice_p95": pct(sl, .95),
        "psi_mean": sum(ps) / len(ps) if ps else None,
        "alert_min": sum(start <= t < end for t in alert) * STEP / 60,
    }


def host_rows(host, inst, slice_cpus, windows, busy):
    i = f'instance="{inst}"'
    cpus = "|".join(map(str, sorted(cpu_set(slice_cpus))))
    out = {}
    for win, (start, end) in windows.items():
        one = lambda e: (prom_range(e, start, end) or [({}, {})])[0][1]
        out[win] = host_summary(
            one(f'node_load15{{{i}}} / scalar(count(node_cpu_seconds_total{{{i},mode="idle"}}))'),
            one(f'sum(1 - rate(node_cpu_seconds_total{{{i},mode="idle",cpu=~"{cpus}"}}[2m]))'),
            one(f'max(rate(node_pressure_cpu_waiting_seconds_total{{{i}}}[2m]))'),
            one(f'max(ALERTS{{alertname="NodeLoadHigh",alertstate="firing",{i}}})'),
            busy, start, end)
    return out


def num(v, spec):
    return "-" if v is None else format(v, spec)


def print_hosts(summaries, slice_cpus):
    print("\nHosts (slice = the platform slice's cores busy while CI ran; psi = host CPU pressure, s/s, while CI ran)")
    print(f"{'host':<18}{'window':<8}{'cover':>6}{'CI h/day':>9}{'slots':>6}{'load p50':>9}{'p95':>6}{'max':>6}"
          f"{'>1.0 min/day':>13}{'slice mean':>11}{'p95':>6}{'psi':>6}{'NodeLoadHigh min':>17}")
    for host, wins in summaries.items():
        for win, s in wins.items():
            if not s:
                print(f"{host:<18}{win:<8}  no samples")
                continue
            print(f"{host:<18}{win:<8}{s['coverage']:>6.0%}{s['ci_h_day']:>9.1f}{num(s['slots_busy'], '.2f'):>6}"
                  f"{s['load_p50']:>9.2f}{s['load_p95']:>6.2f}{s['load_max']:>6.2f}{s['over1_day']:>13.0f}"
                  f"{num(s['slice_mean'], '.2f'):>11}{num(s['slice_p95'], '.2f'):>6}{num(s['psi_mean'], '.2f'):>6}"
                  f"{s['alert_min']:>17.0f}")
        print(f"{'':<18}slice cpus {slice_cpus[host]} ({len(cpu_set(slice_cpus[host]))})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--before", type=window, required=True, help="START..END, ISO UTC")
    ap.add_argument("--after", type=window, required=True, help="START..END, ISO UTC")
    ap.add_argument("--hosts", default="razer-laptop,windows-media-ci")
    ap.add_argument("--repo", default="BlackNBrownStudios/platform")
    ap.add_argument("--workflow", default="ci.yml")
    args = ap.parse_args(argv)
    windows = {"before": args.before, "after": args.after}
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    inv, _ = load_inventory(INVENTORY)
    slice_cpus = {}
    for h in hosts:
        if not inv.get(h, {}).get("ci_linux_cpus"):
            sys.exit(f"FAIL: {h} has no ci_linux_cpus in {INVENTORY}")
        slice_cpus[h] = inv[h]["ci_linux_cpus"]
    inst = {r["metric"]["host"]: r["metric"]["instance"] for r in json.load(urllib.request.urlopen(
        f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": "cc:host_instance"}), timeout=30))["data"]["result"]}
    if missing := [h for h in hosts if h not in inst]:
        sys.exit(f"FAIL: {', '.join(missing)} not in cc:host_instance")
    busy = {h: {} for h in hosts}
    for win, (a, b) in windows.items():
        for h in hosts:
            slots = busy_slots(h, a, b)
            if not slots:
                sys.exit(f"FAIL: no github_runner_state for {h} in the {win} window (past retention?);"
                         " without it every job would read as lone")
            for name, times in slots.items():
                busy[h].setdefault(name, set()).update(times)
    jobs = [j for a, b in windows.values() for j in fetch_jobs(args.repo, args.workflow, a, b)]
    jobs = list({j["id"]: (event, j) for event, j in jobs}.values())  # windows may share runs
    rows = job_rows(jobs, hosts, windows, busy)
    print_jobs(rows, hosts, windows)
    print_hosts({h: host_rows(h, inst[h], slice_cpus[h], windows, busy[h]) for h in hosts}, slice_cpus)
    return 0


if __name__ == "__main__":
    sys.exit(main())
