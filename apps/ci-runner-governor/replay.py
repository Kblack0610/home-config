#!/usr/bin/env python3
"""Replay the governor's home-host decisions over a window of recorded readings.

Runs governor.decide() once a minute over the last --days of the same series the governor
reads (its QUERIES, the pool runners' busy state, the waiting pool jobs), with the slot
layout from config.json and apps/ci-topology/topology.json and no pins: what a change to
decide() or its thresholds would have done. Prometheus keeps about
3.5 days at its 5GB cap, so point --url at VictoriaMetrics (apps/victoria-metrics, 2y):

    kubectl --context home-k3s -n monitoring port-forward svc/victoria-metrics 18428:8428
    python3 apps/ci-runner-governor/replay.py --url http://localhost:18428

Per host it prints the verdicts, withholds and admits, flaps (an admit within 30 minutes
of a withhold), the most slots held at once, slot-minutes held, and slot-minutes held while
pool jobs waited, a slot was online and the host was not HOT or FULL that minute (a slot held
for nothing), with flaps and those per online day (the go-live bar's unit), split
by what held it: the cooldown, or the admit lines a STEADY host was short of (psi, tctl).
Free disk between the withhold and admit lines is FULL's own hysteresis and counts as held for
a reason. A host with no CPU pressure series reads STEADY "no reading" and never acts; the
coverage column says how many of the minutes the host had a pool slot online also had every
reading.

--baseline <governor.py> also runs that copy's decide() on each cycle's inputs and prints
every cycle whose actions or kept instance state differ: a restructure of decide() proves
itself with zero. --synthetic N adds N cycles of random inputs on the real slot layout, which
reach the standby, pack, pin and power paths the recorded LAN replay does not:

    git show forgejo/master:apps/ci-runner-governor/governor.py > /tmp/base.py
    python3 apps/ci-runner-governor/replay.py --baseline /tmp/base.py --synthetic 20000

Stdlib only.
"""
import argparse
import copy
import importlib.util
import json
import os
import random
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


def replay(url, days, cfg, base=None):
    """(per-host stats, cycles, the LAN pool slots, base's diffs: [] with no base)."""
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
    # Minutes a slot was registered and up (idle or running): coverage counts only those, since a
    # host that is off has nothing for the governor to hold.
    online = defaultdict(set)
    for key, series in query_range(url, 'count by (name) (github_runner_state{state=~"idle|running"})',
                                   start, end).items():
        name = dict(key).get("name")
        if name in slots:
            online[slots[name]].update(t for t, v in series.items() if v > 0)
    waiting = defaultdict(list)
    q = f'github_actions_job_waiting{{pool=~"{"|".join(governor.pools(cfg))}",stuck!="true"}}'
    for key, series in query_range(url, q, start, end).items():
        labels = governor.label_set(dict(key).get("labels", "") or cfg["pool_label"])
        for t, v in series.items():
            if v > 0:
                waiting[t].append(labels)

    runners = {n: {"host": h, "busy": False, "elapsed": 0.0, "withheld": False, "labels": frozenset()}
               for n, h in slots.items()}
    last_action, since, diffs = {}, {}, []
    st = {h: Counter() for h in lan}
    for t in range(start, end, STEP):
        hosts = {k: {h: s[t] for h, s in per.items() if t in s} for k, per in readings.items()}
        for n, r in runners.items():
            r["busy"] = busy[n].get(t, 0) > 0
        for h in lan:
            if t in online[h]:
                st[h]["online"] += 1
                if all(hosts[k].get(h) is not None for k in ("idle", "psi", "mem")):
                    st[h]["coverage"] += 1
        q = waiting.get(t, [])
        decisions = decide_both(base, diffs, cfg, hosts, runners, q, last_action, t, {}, {})
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
            short = why.partition("short of admit on ")[2].rstrip(")").split("+")  # a cooldown wraps why in (...)
            # Free disk between the withhold and admit lines is FULL's hysteresis: held for a reason.
            if (queued and t in online[host] and v not in ("HOT", "FULL")
                    and not (v == "STEADY" and "disk" in short)):
                st[host]["slot_minutes_held_for_nothing"] += held
                # Which line held it: the cooldown, or the admit lines a STEADY host is short of.
                cause = ("cooldown" if why.startswith("cooldown")
                         else "+".join(short) or why if v == "STEADY" else v)
                st[host][f"for nothing {cause}"] += held
    return st, (end - start) // STEP, slots, diffs


def load_baseline(path):
    """Another governor.py as a module, to compare decide() against (--baseline)."""
    spec = importlib.util.spec_from_file_location("governor_baseline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def decide_both(base, diffs, cfg, *args):
    """governor.decide(cfg, *args); with a baseline module, the same call on it too, each on its
    own copy of the inputs, since decide() keeps state in instances (args[5]). A differing action
    list or kept state goes in diffs as (now, this, baseline); this module's state carries on."""
    if base is None:
        return governor.decide(cfg, *args)
    mine, theirs = copy.deepcopy(args), copy.deepcopy(args)
    got = [tuple(a) for a in governor.decide(cfg, *mine)]
    want = [tuple(a) for a in base.decide(cfg, *theirs)]
    if got != want or mine[5] != theirs[5]:
        diffs.append((args[4], (got, mine[5]), (want, theirs[5])))
    args[5].update(mine[5])
    return got


def synthetic(cfg, base, cycles, seed=0):
    """decide() and base.decide() over cycles of random readings, runners, queues, pins,
    overrides and instance states on the real slot layout, each cycle's actions fed back into
    the next as GitHub and EC2 would: the paths the recorded replay never reaches (the standby
    hosts, pack, pins, pairs, power). Returns the diffs."""
    rnd = random.Random(seed)
    with open(os.environ["TOPOLOGY"]) as f:
        topo = json.load(f)["hosts"]
    governed = {cfg["pool_label"], cfg["build_label"], cfg["wide_label"]}
    slots = {s["name"]: h for h in (*cfg["hosts"], *cfg["standby"]) for s in topo[h]["slots"]
             if governed & set(s["caps"])}
    withheld = {n: rnd.random() < 0.5 for n in slots}
    busy, elapsed = dict.fromkeys(slots, False), dict.fromkeys(slots, 0.0)
    marks = sorted({l for ls in governor.build_marks(cfg).values() for l in ls})
    jobs = [frozenset({"self-hosted", cfg["pool_label"]}), frozenset({cfg["pool_label"], cfg["full_label"]}),
            *(frozenset({m}) for m in marks), frozenset({cfg["wide_label"], cfg["pool_label"]})]
    pick = lambda lo, hi: rnd.uniform(lo, hi) if rnd.random() > 0.05 else None
    readings, insts, last, pins, diffs = {}, {}, {}, {}, []
    for h, s in cfg["standby"].items():
        if "power" in s:
            insts[h] = {"state": rnd.choice(("running", "stopped")), "busy_at": 0.0, "launched": 0.0}
    for c in range(cycles):
        now = 1_800_000_000 + STEP * c
        for h in cfg["hosts"]:
            if c == 0 or rnd.random() < 0.2:
                readings[h] = {"idle": pick(0, 1), "psi": pick(0, 0.6), "mem": pick(0, 0.6),
                               "tctl": pick(50, 95), "disk": pick(0.1, 0.4)}
        hosts = {k: {h: r[k] for h, r in readings.items() if r[k] is not None} for k in governor.QUERIES}
        if rnd.random() < 0.05:
            pins = {h: rnd.randint(0, 4) for h in rnd.sample(sorted(slots.values()), rnd.randint(0, 2))}
        for h, i in insts.items():
            if rnd.random() < 0.03:
                i["state"] = rnd.choice(("running", "stopped", "pending", "stopping"))
            i["hours_today"] = rnd.choice((0, 3, 30)) if rnd.random() < 0.05 else i.get("hours_today", 0)
            if rnd.random() < 0.03:
                i["override"] = rnd.choice((None, "on", "off"))
            if rnd.random() < 0.02:
                i["fallback_until"], i["fallback_why"] = now + rnd.choice((0, 600)), "capacity"
            labels = {l for n, sh in slots.items() if sh == h for l in governor.admit_labels(cfg, h, n)}
            i["labels"] = governor.label_set(",".join(labels | {cfg["pool_label"]})) if rnd.random() > 0.03 else frozenset()
        runners = {}
        for n, h in slots.items():
            up = insts[h]["state"] == "running" if h in insts else True
            if not up or rnd.random() < 0.05:
                busy[n] = False
                continue
            if rnd.random() < 0.15:
                busy[n] = not busy[n] and not withheld[n]
                elapsed[n] = 0.0
            elapsed[n] += STEP
            have = [governor.withheld_label(cfg, h, n)] if withheld[n] else governor.admit_labels(cfg, h, n)
            runners[n] = {"host": h, "busy": busy[n], "elapsed": elapsed[n], "withheld": withheld[n],
                          "labels": governor.label_set(",".join(have))}
        for h, i in insts.items():
            if i["state"] != "running" or any(r["busy"] for r in runners.values() if r["host"] == h):
                i["busy_at"] = now
        waiting = [rnd.choice(jobs) for _ in range(rnd.choice((0, 0, 0, 1, 3, 8)))]
        shared = [frozenset({rnd.choice(("light", "heavy"))}) for _ in range(rnd.choice((0, 0, 2)))]
        for h, action, runner, *_ in decide_both(base, diffs, cfg, hosts, runners, waiting, last, now,
                                                 insts, pins, shared):
            if action in ("admit", "withhold") and runner:
                withheld[runner] = action == "withhold"
            elif action in ("start", "stop"):
                insts[h]["state"] = {"start": "pending", "stop": "stopped"}[action]
            if action != "hold":
                last[h] = now
        for i in insts.values():
            if i["state"] == "pending" and rnd.random() < 0.5:
                i["state"] = "running"
    return diffs


def report(diffs, what):
    print(f"baseline: {len(diffs)} of {what} differ")
    for now, (got, kept), (want, theirs) in diffs[:5]:
        print(f"  at {now}: this {got}\n    baseline {want}")
        for h in sorted(set(kept) | set(theirs)):
            if kept.get(h) != theirs.get(h):
                print(f"    {h} kept: this {kept.get(h)}\n      baseline {theirs.get(h)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("PROMETHEUS_URL", "http://localhost:18428"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--baseline", help="another governor.py whose decide() every cycle is compared against")
    ap.add_argument("--synthetic", type=int, default=0, metavar="N",
                    help="with --baseline, first compare N cycles of random inputs")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    cfg = governor.load_config(a.config)
    base = load_baseline(a.baseline) if a.baseline else None
    if base and a.synthetic:
        diffs = synthetic(cfg, base, a.synthetic, a.seed)
        report(diffs, f"{a.synthetic} synthetic cycles (seed {a.seed})")
        if diffs:
            sys.exit(1)
    st, minutes, slots, diffs = replay(a.url.rstrip("/"), a.days, cfg, base)
    if base:
        report(diffs, f"{minutes} recorded cycles")
    print(f"replay: {a.days}d, {minutes} cycles, {len(slots)} LAN pool slots, cooldown {cfg['cooldown_seconds']}s")
    for h in sorted(st):
        c = st[h]
        days = max(c["online"], 1) / 1440
        verdicts = ", ".join(f"{k[8:]} {v}" for k, v in sorted(c.items()) if k.startswith("verdict "))
        print(f"{h}: coverage {c['coverage'] / max(c['online'], 1):.0%} of {c['online']} online minutes; {verdicts}; withholds {c['withholds']}, admits {c['admits']}, "
              f"flaps {c['flaps']}, held max {c['held_max']}, slot-min held {c['slot_minutes_held']}, "
              f"held while jobs waited and not hot {c['slot_minutes_held_for_nothing']}"
              f" (per online day: flaps {c['flaps'] / days:.1f}, held for nothing {c['slot_minutes_held_for_nothing'] / days:.0f})"
              + "".join(f", {k[12:]} {v}" for k, v in sorted(c.items()) if k.startswith("for nothing ") and v))
    if base and diffs:
        sys.exit(1)


if __name__ == "__main__":
    main()
