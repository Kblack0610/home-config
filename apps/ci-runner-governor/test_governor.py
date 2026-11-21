#!/usr/bin/env python3
"""Offline tests for governor.py: canned readings, no Prometheus, no GitHub.

Covers what a live night cannot show on demand: a hot host withholds its idle
runner first and its longest-running busy one next, a host with room re-admits
only while jobs wait, the pool floor and cap hold, a cooling host is left alone,
a missing reading is never room, a full disk empties a host past its floor and cooldown
but not the pool floor, a standby host takes only the jobs the others cannot, a powered
standby host is started and stopped by a fake EC2 (no network), it is started only for a waiting job whose labels its runners carry, the
standby list starts Spot first and falls back to the next host on no capacity or a
reclaim but not on a budget deny, and a live action adds the new label before it removes the old. Stdlib
only; CI runs it in the checks workflow.
"""
import copy
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TOPOLOGY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ci-topology", "topology.json"))
import governor  # noqa: E402

# The live thresholds with a fixed host list and the pool floor that roster was written
# for, so a change to the real roster (the topology, config.json "hosts", "pool_min_admitted")
# does not change what these tests mean.
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
LIVE = governor.load_config(CONFIG)
CFG = governor.with_defaults({**LIVE, "pool_min_admitted": 2, "standby": {"aws-build-1": {"max_admitted": 6}}, "hosts": {
    "cachyos-x8664-main": {"min_admitted": 0, "admit": {"tctl": 80}, "withhold": {"idle": 0.25, "tctl": 90}},
    "deskslim-1": {},
    "deskslim-2": {},
}})
COOL = {"idle": 0.6, "psi": 0.05, "tctl": 60, "mem": 0.5, "disk": 0.5}
BUSY = {"idle": 0.3, "psi": 0.3, "tctl": 70, "mem": 0.4, "disk": 0.5}
HOT = {"idle": 0.1, "psi": 0.5, "tctl": 70, "mem": 0.4, "disk": 0.5}
FULL = {**COOL, "disk": 0.19}


def readings(**per_host):
    return {k: {h: r[k] for h, r in per_host.items() if k in r} for k in governor.QUERIES}


def pool(*specs):
    """specs: (name, host, busy, elapsed, withheld)."""
    return {n: {"host": h, "busy": b, "elapsed": e, "withheld": w} for n, h, b, e, w in specs}


RUNNERS = pool(("deskslim-2-1", "deskslim-2", True, 900, False), ("deskslim-2-2", "deskslim-2", True, 100, False),
               ("deskslim-2-3", "deskslim-2", False, 0, False), ("cachyos-x8664-main-1", "cachyos-x8664-main", True, 300, False),
               ("deskslim-1-1", "deskslim-1", True, 50, False), ("deskslim-1-2", "deskslim-1", False, 0, True))


UNITY_JOB = frozenset({"self-hosted", "unity"})
LAN_JOB = frozenset({"self-hosted", "unity", "lan"})


def decide(hosts, runners=RUNNERS, queue=5, last=None, cfg=CFG, instances=None, waiting=None, pins=None):
    waiting = [UNITY_JOB] * queue if waiting is None else waiting
    return governor.decide(cfg, hosts, runners, waiting, last or {}, 10_000, instances, pins)


def actions(*args, **kw):
    return {h: (a, r) for h, a, r, _, _ in decide(*args, **kw)}


# Starts on the first job past the others, at once: what these tests check is which jobs
# and slots count, not the cost gate. HEAVY below is the default gate (POWER_DEFAULTS).
POWER = {"provider": "aws", "instance_id": "i-0abc", "region": "us-east-2", "idle_off_seconds": 1200,
         "start_min_excess": 1, "start_after_seconds": 0}
AWS = {**CFG, "standby": {"aws-spot-1": {"max_admitted": 8, "power": POWER}}}
LIVE_POWER = {**governor.POWER_DEFAULTS, "max_hours_day": 24}
# A 12h day cap of its own: the live hosts run uncapped (24h), and the cap tests need one.
HEAVY = {**CFG, "standby": {"aws-spot-1": {"max_admitted": 6, "power": {**LIVE_POWER, "instance_id": "i-0abc",
                                                                       "max_hours_day": 12}}}}


# What host_labels() reads for aws-spot-1: its runners' labels (off the LAN, so no `lan`)
# plus the pool labels.
AWS_LABELS = frozenset({"self-hosted", "linux", "x64", "host-aws-spot-1", "unity-withheld", "unity", "unity-full"})


def instance(state, idle=0, labels=AWS_LABELS, **more):
    return {"aws-spot-1": {"state": state, "busy_at": 10_000 - idle, "labels": labels, **more}}


class FakeEc2:
    """fail: True for a network error, or an EC2 error code. state, reason and fail may be
    dicts by instance id, for a config with more than one powered host."""
    def __init__(self, state="stopped", fail=False, launched=0.0, reason=""):
        self.st, self.fail, self.launched, self.reason, self.calls = state, fail, launched, reason, []

    @staticmethod
    def per(v, iid):
        return v.get(iid, "") if isinstance(v, dict) else v

    def state(self, iid):
        self.calls.append(("state", iid))
        return self.per(self.st, iid), self.launched, self.per(self.reason, iid)

    def start(self, iid):
        self.calls.append(("start", iid))
        fail = self.per(self.fail, iid)
        if fail is True:
            raise urllib.error.URLError("connection reset")
        if fail:
            raise governor.Ec2Error(fail, "no")

    def stop(self, iid):
        self.calls.append(("stop", iid))


# aws-build-1's nine slots as the play registers them: 1-3 build slots (1-2 also unity-release),
# 4-9 test slots, paired 1: 4-5, 2: 6-7, 3: 8-9 (inventory unity_ci_slot_groups).
BUILD_JOB = frozenset({"self-hosted", "linux", "unity-build"})
PAIRS = {1: [4, 5], 2: [6, 7], 3: [8, 9]}
PAIRED_BASE = frozenset({"self-hosted", "linux", "x64", "host-aws-build-1"})
PAIRED_OWN = {s: frozenset({"unity-build"} | ({"unity-release"} if s <= 2 else set())) if s <= 3
              else frozenset({"unity", "unity-full", "unity-gate"}) for s in range(1, 10)}
PAIRED_LABELS = PAIRED_BASE.union(*PAIRED_OWN.values()) | {"unity-withheld", "unity-build-withheld"}



def slot_name(s):
    return f"aws-build-1-unity-pool-{s}"


def paired_labels(s, withheld):
    """Slot s's labels: as registered, or with the governor's withhold swap."""
    if not withheld:
        return PAIRED_BASE | PAIRED_OWN[s]
    if s <= 3:
        return PAIRED_BASE | PAIRED_OWN[s] - {"unity-build"} | {"unity-build-withheld"}
    return PAIRED_BASE | PAIRED_OWN[s] - {"unity", "unity-full"} | {"unity-withheld"}


def paired_slots(spec):
    """spec: one character per slot 1-9. "." withheld and idle, "h" withheld and busy, "i"
    admitted and idle, "b" admitted and busy, "-" rostered but not registered yet."""
    return {slot_name(s): {"host": "aws-build-1", "busy": c in "hb", "elapsed": 100 - s, "withheld": c in ".h",
                           "labels": paired_labels(s, c in ".h")}
            for s, c in enumerate(spec, 1) if c != "-"}


def spec_of(runners):
    out = ""
    for s in range(1, 10):
        r = runners.get(slot_name(s))
        out += "-" if r is None else ".hib"[2 * (not r["withheld"]) + r["busy"]]
    return out


def settle(cfg, spec, waiting, pins=None, cycles=12):
    """spec after cycles of decide() and its label changes, with no job starting or ending."""
    runners = paired_slots(spec)
    inst = {"aws-build-1": {"state": "running", "busy_at": 10_000, "labels": PAIRED_LABELS}}
    for _ in range(cycles):
        for h, a, r, _, _ in governor.decide(cfg, STEADY, runners, waiting, {}, 10_000, inst, pins):
            if h == "aws-build-1" and a in ("admit", "withhold"):
                held = a == "withhold"
                runners[r].update(withheld=held, labels=paired_labels(int(r.rsplit("-", 1)[1]), held))
    return spec_of(runners)


def simulate(cfg, seed, cycles=400):
    """aws-build-1's nine slots through cycles of random arrivals and finishes: decide() and
    apply() against a fake GitHub that holds each runner's labels, and each waiting job goes to
    an idle runner whose labels cover it, as GitHub assigns them. The slots start as the play
    registers them (every one admitted). Returns (the first (cycle, build slot) where a build
    slot and one of its test slots both run a job, or None; counts of what ran)."""
    rnd = random.Random(seed)
    names = [slot_name(s) for s in range(1, 10)]
    labels = {slot_name(s): set(paired_labels(s, False)) for s in range(1, 10)}
    busy, waiting = {}, []
    stats = {"build_cycles": 0, "four_tests_beside_a_build": 0, "six_tests": 0}

    def gh(method, path, body=None):
        if method == "GET":
            return {"runners": [{"id": i, "name": n, "labels": [{"name": l} for l in sorted(labels[n])]}
                                for i, n in enumerate(names)] if "page=1" in path else []}
        n = names[int(path.split("/runners/")[1].split("/")[0])]
        if method == "POST":
            labels[n] |= set(body["labels"])
        else:
            labels[n].discard(urllib.parse.unquote(path.rsplit("/", 1)[1]))
        return None

    inst = {"aws-build-1": {"state": "running", "busy_at": 10_000, "labels": PAIRED_LABELS}}
    pool_jobs = [UNITY_JOB, UNITY_JOB | {"unity-gate"}, UNITY_JOB | {"unity-full"}]
    with mock.patch.object(governor, "gh", gh):
        for c in range(cycles):
            now = 10_000 + 60 * c
            inst["aws-build-1"]["busy_at"] = now
            if len(waiting) < 20:
                waiting += [rnd.choice(pool_jobs) for _ in range(rnd.choice((0, 0, 1, 2, 4)))]
            if rnd.random() < 0.08:
                waiting.append(rnd.choice((BUILD_JOB, BUILD_JOB | {"unity-release"})))
            runners = {n: {"host": "aws-build-1", "busy": n in busy, "elapsed": now - busy.get(n, now),
                           "withheld": bool(labels[n] & {"unity-withheld", "unity-build-withheld"}),
                           "labels": frozenset(labels[n])} for n in names}
            for h, a, r, _, _ in governor.decide(cfg, STEADY, runners, list(waiting), {}, now, inst, None):
                if h == "aws-build-1" and r:
                    governor.apply(cfg, a, r, h)
            for need in list(waiting):
                free = [n for n in names if n not in busy and need <= labels[n]]
                if free:
                    busy[rnd.choice(free)] = now
                    waiting.remove(need)
            running = {int(n.rsplit("-", 1)[1]) for n in busy}
            for b, tests in PAIRS.items():
                if b in running and running & set(tests):
                    return (c, b), stats
            tests = len(running - set(PAIRS))
            stats["build_cycles"] += bool(running & set(PAIRS))
            stats["four_tests_beside_a_build"] += bool(running & set(PAIRS)) and tests >= 4
            stats["six_tests"] += tests == 6
            for n in list(busy):
                if rnd.random() < (0.05 if int(n.rsplit("-", 1)[1]) in PAIRS else 0.25):
                    del busy[n]
    return None, stats


RESTORED = governor.restored


def setUpModule():
    # No test reads Prometheus: a first sight restores nothing unless a test says so.
    p = mock.patch.object(governor, "restored", return_value=(0.0, "", False))
    p.start()
    unittest.addModuleCleanup(p.stop)
    p = mock.patch.object(governor, "observe_shared", return_value=[])
    p.start()
    unittest.addModuleCleanup(p.stop)
    p = mock.patch.object(governor, "observe_machines", return_value={})
    p.start()
    unittest.addModuleCleanup(p.stop)


D1, D2, CX = "deskslim-1", "deskslim-2", "cachyos-x8664-main"
# One verdict cycle per row: (name, readings, decide() keywords, {host: (action, runner)}).
# readings maps each LAN host to COOL, BUSY, HOT or FULL; the keywords are decide()'s here
# (runners, queue, last, cfg, instances, waiting, pins). Floors in CFG: deskslim-2 and
# deskslim-1 keep 1 admitted, cachyos-x8664-main 0, the pool 2.
SCENARIOS = [
    ("a hot host withholds its idle runner first", {D2: HOT, CX: BUSY, D1: BUSY}, {},
     {D2: ("withhold", "deskslim-2-3")}),
    ("then the busy runner nearest its end", {D2: HOT, CX: BUSY, D1: BUSY},
     {"runners": {n: r for n, r in RUNNERS.items() if n != "deskslim-2-3"}}, {D2: ("withhold", "deskslim-2-1")}),
    ("room re-admits while jobs wait", {D2: BUSY, CX: BUSY, D1: COOL}, {}, {D1: ("admit", "deskslim-1-2")}),
    ("room admits nothing with no job waiting", {D2: BUSY, CX: BUSY, D1: COOL}, {"queue": 0}, {D1: ("hold", "")}),
    ("each host keeps its own floor", {D2: HOT, CX: HOT, D1: BUSY},
     {"runners": pool(("deskslim-2-1", D2, True, 9, False), ("cachyos-x8664-main-1", CX, True, 9, False),
                      ("deskslim-1-1", D1, True, 9, False))},
     {D2: ("hold", ""), CX: ("withhold", "cachyos-x8664-main-1")}),
    ("the pool floor keeps the watch green", {D2: BUSY, CX: HOT, D1: BUSY},
     {"runners": pool(("cachyos-x8664-main-1", CX, True, 9, False), ("deskslim-2-1", D2, True, 9, False),
                      ("deskslim-2-2", D2, False, 0, True), ("deskslim-1-1", D1, True, 9, True))},
     {CX: ("hold", "")}),
    ("the pool cap holds an admit", {D2: BUSY, CX: BUSY, D1: COOL}, {"cfg": {**CFG, "pool_max_admitted": 5}},
     {D1: ("hold", "")}),
    ("a withhold makes room under the cap", {D2: HOT, CX: BUSY, D1: COOL}, {"cfg": {**CFG, "pool_max_admitted": 5}},
     {D2: ("withhold", "deskslim-2-3"), D1: ("admit", "deskslim-1-2")}),
    ("a cooling host is left alone", {D2: HOT, CX: BUSY, D1: BUSY}, {"last": {D2: 10_000 - 60}}, {D2: ("hold", "")}),
    ("a missing reading is never room", {D2: BUSY, CX: BUSY, D1: {"tctl": 50}}, {}, {D1: ("hold", "")}),
    ("a full disk takes the host's last slot", {D2: BUSY, CX: BUSY, D1: FULL},
     {"runners": pool(("deskslim-1-1", D1, True, 9, False), ("deskslim-2-1", D2, True, 9, False),
                      ("deskslim-2-2", D2, True, 9, False), ("cachyos-x8664-main-1", CX, True, 9, False))},
     {D1: ("withhold", "deskslim-1-1")}),
    ("a full disk does not wait for the cooldown", {D2: FULL, CX: BUSY, D1: BUSY}, {"last": {D2: 10_000 - 60}},
     {D2: ("withhold", "deskslim-2-3")}),
    ("a full disk stops at the pool floor", {D2: FULL, CX: BUSY, D1: FULL},
     {"runners": pool(("deskslim-2-1", D2, True, 9, False), ("deskslim-1-1", D1, True, 9, False))},
     {D2: ("hold", ""), D1: ("hold", "")}),
]


class ScenarioTest(unittest.TestCase):
    def test_scenarios(self):
        for name, per_host, kw, want in SCENARIOS:
            with self.subTest(name):
                got = actions(readings(**per_host), **kw)
                self.assertEqual({h: got[h] for h in want}, want)


class GovernorTest(unittest.TestCase):
    def test_a_waiting_job_on_a_shared_cap_is_room_for_a_withheld_slot_that_takes_it(self):
        hosts = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": COOL})
        cfg = {**CFG, "shared": {"deskslim-1-2": ["light", "heavy"]}}
        light = frozenset({"self-hosted", "light"})
        got = lambda shared: {h: (a, r) for h, a, r, _, _ in governor.decide(
            cfg, hosts, RUNNERS, [], {}, 10_000, shared_waiting=shared)}["deskslim-1"]
        self.assertEqual(got([light]), ("admit", "deskslim-1-2"))
        self.assertEqual(got([frozenset({"self-hosted", "heavy"})]), ("admit", "deskslim-1-2"))
        self.assertEqual(got([]), ("hold", ""))
        self.assertEqual(got([frozenset({"self-hosted", "macos"})]), ("hold", ""))
        # A slot with no shared cap gains nothing from a light job.
        self.assertEqual(governor.decide({**CFG, "shared": {}}, hosts, RUNNERS, [], {}, 10_000,
                                         shared_waiting=[light])[-1][1:3], ("hold", ""))

    def test_full_disk_counts_with_cpu_readings_missing(self):
        self.assertEqual(governor.verdict({"disk": 0.19}, CFG["hosts"]["deskslim-2"])[0], "FULL")

    def test_disk_between_the_lines_withholds_nothing_and_admits_nothing(self):
        between = {**COOL, "disk": 0.22}
        v, why = governor.verdict(between, CFG["hosts"]["deskslim-1"])
        self.assertEqual(v, "STEADY")
        self.assertTrue(why.endswith("short of admit on disk"), why)
        got = actions(readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": between}))
        self.assertEqual(got["deskslim-1"], ("hold", ""))

    def test_a_missing_disk_reading_is_never_room(self):
        self.assertEqual(governor.verdict({k: v for k, v in COOL.items() if k != "disk"},
                                          CFG["hosts"]["deskslim-1"])[0], "STEADY")

    def test_cpu_pressure_on_a_half_idle_host_is_not_hot(self):
        # deskslim-2 2026-11-19 14:45: three Unity jobs, psi 0.61, idle 0.67, 65 C.
        bursty = {**COOL, "idle": 0.55, "psi": 0.6}
        self.assertEqual(governor.verdict(bursty, CFG["hosts"]["deskslim-2"])[0], "ROOM")
        self.assertEqual(governor.verdict({**bursty, "psi": 0.3}, CFG["hosts"]["deskslim-2"])[0], "ROOM")

    def test_cpu_pressure_on_a_busy_host_is_still_hot(self):
        v, why = governor.verdict({**COOL, "idle": 0.2, "psi": 0.5}, CFG["hosts"]["deskslim-2"])
        self.assertEqual((v, why), ("HOT", "cpu pressure 0.50 > 0.4"))
        v, why = governor.verdict({**COOL, "idle": 0.3, "psi": 0.3}, CFG["hosts"]["deskslim-2"])
        self.assertEqual(v, "STEADY")
        self.assertTrue(why.endswith("short of admit on idle+psi"), why)

    def test_cachyos_runs_hot_sooner(self):
        warm = {**COOL, "tctl": 91}
        self.assertEqual(governor.verdict(warm, CFG["hosts"]["cachyos-x8664-main"])[0], "HOT")
        self.assertEqual(governor.verdict(warm, CFG["hosts"]["deskslim-2"])[0], "STEADY")

    def mac(self, on_busy=0, on_idle=0, off=2):
        specs = [(f"mac-{i}", "aws-build-1", True, 100 - i, False) for i in range(on_busy)]
        specs += [(f"mac-idle-{i}", "aws-build-1", False, 0, False) for i in range(on_idle)]
        specs += [(f"mac-off-{i}", "aws-build-1", False, 0, True) for i in range(off)]
        return pool(*specs)

    FULL = pool(("deskslim-2-1", "deskslim-2", True, 900, False), ("deskslim-1-1", "deskslim-1", True, 50, False))
    STEADY = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": BUSY})
    CAPPED = {**CFG, "standby": {"aws-build-1": {"max_admitted": 6}}}

    def test_standby_stays_out_while_idle_slots_cover_the_queue(self):
        runners = {**self.mac(), **RUNNERS}  # deskslim-2-3 is admitted and idle
        self.assertEqual(actions(self.STEADY, runners, queue=1)["aws-build-1"], ("hold", ""))

    def test_standby_takes_one_when_more_jobs_wait_than_idle_slots(self):
        runners = {**self.mac(), **RUNNERS}  # deskslim-2-3 is admitted and idle
        self.assertEqual(actions(self.STEADY, runners, queue=9)["aws-build-1"], ("admit", "mac-off-0"))

    def test_standby_takes_one_when_the_other_hosts_are_full(self):
        runners = {**self.mac(), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners)["aws-build-1"], ("admit", "mac-off-0"))

    def test_standby_waits_for_a_host_with_room_to_admit_first(self):
        runners = {**self.mac(), **self.FULL, "deskslim-1-2": RUNNERS["deskslim-1-2"]}
        hosts = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": COOL})
        got = actions(hosts, runners, queue=1)
        self.assertEqual(got["deskslim-1"], ("admit", "deskslim-1-2"))
        self.assertEqual(got["aws-build-1"], ("hold", ""))

    def test_standby_counts_an_idle_slot_and_an_admit_together(self):
        runners = {**self.mac(), **RUNNERS}  # deskslim-2-3 is idle, deskslim-1-2 comes in this cycle
        hosts = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": COOL})
        self.assertEqual(actions(hosts, runners, queue=2)["aws-build-1"], ("hold", ""))
        got = actions(hosts, runners, queue=3)
        self.assertEqual(got["deskslim-1"], ("admit", "deskslim-1-2"))
        self.assertEqual(got["aws-build-1"], ("admit", "mac-off-0"))

    def test_standby_never_passes_its_own_cap(self):
        runners = {**self.mac(on_busy=6), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, cfg=self.CAPPED)["aws-build-1"], ("hold", ""))

    def test_standby_over_its_cap_gives_one_back(self):
        runners = {**self.mac(on_busy=8, off=0), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, cfg=self.CAPPED)["aws-build-1"], ("withhold", "mac-0"))

    def test_standby_respects_the_pool_cap(self):
        runners = {**self.mac(on_busy=1), **self.FULL}
        got = actions(self.STEADY, runners, cfg={**CFG, "pool_max_admitted": 3})
        self.assertEqual(got["aws-build-1"], ("hold", ""))

    def test_standby_goes_back_when_nothing_waits(self):
        runners = {**self.mac(on_busy=1, on_idle=1), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, queue=0)["aws-build-1"], ("withhold", "mac-idle-0"))

    def test_standby_leaves_a_busy_slot_when_nothing_waits(self):
        runners = {**self.mac(on_busy=1), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, queue=0)["aws-build-1"], ("hold", ""))

    def test_standby_slots_do_not_hold_the_pool_floor(self):
        runners = {**self.mac(on_busy=4, off=0), "cachyos-x8664-main-1": RUNNERS["cachyos-x8664-main-1"], "deskslim-2-1": RUNNERS["deskslim-2-1"]}
        hosts = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": HOT, "deskslim-1": BUSY})
        self.assertEqual(actions(hosts, runners)["cachyos-x8664-main"], ("hold", ""))

    def aws(self, on_busy=0, on_idle=0, off=0):
        specs = [(f"aws-{i}", "aws-spot-1", True, 100 - i, False) for i in range(on_busy)]
        specs += [(f"aws-idle-{i}", "aws-spot-1", False, 0, False) for i in range(on_idle)]
        specs += [(f"aws-off-{i}", "aws-spot-1", False, 0, True) for i in range(off)]
        return pool(*specs)

    def test_power_on_when_jobs_wait_and_the_instance_is_stopped(self):
        got = actions(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped"))
        self.assertEqual(got["aws-spot-1"], ("start", ""))

    def test_no_power_on_while_idle_slots_cover_the_queue(self):
        got = actions(self.STEADY, RUNNERS, queue=1, cfg=AWS, instances=instance("stopped"))  # deskslim-2-3 is idle
        self.assertEqual(got["aws-spot-1"], ("hold", ""))

    def test_a_start_is_not_room(self):
        # aws-build-1 sorts after aws-spot-1: it still admits, since a start covers no job.
        cfg = {**CFG, "standby": {**AWS["standby"], "aws-build-1": {"max_admitted": 6}}}
        got = actions(self.STEADY, {**self.mac(), **self.FULL}, queue=1, cfg=cfg, instances=instance("stopped"))
        self.assertEqual(got["aws-spot-1"], ("start", ""))
        self.assertEqual(got["aws-build-1"], ("admit", "mac-off-0"))

    def test_online_slots_come_in_one_per_cycle(self):
        got = actions(self.STEADY, {**self.aws(off=8), **self.FULL}, cfg=AWS, instances=instance("running"))
        self.assertEqual(got["aws-spot-1"], ("admit", "aws-off-0"))

    def test_stop_after_idle_off_seconds(self):
        withheld = {**self.aws(off=8), **RUNNERS}  # deskslim-2-3 covers the one job
        cold = {**AWS, "warm_idle_off_seconds": 0}  # no warm host: its own idle-off alone
        for idle, want in ((1199, ("hold", "")), (1200, ("stop", ""))):
            got = actions(self.STEADY, withheld, queue=1, cfg=cold, instances=instance("running", idle))
            self.assertEqual(got["aws-spot-1"], want)
        # Its slots go first, even with jobs waiting that it has not taken in that time.
        got = actions(self.STEADY, {**self.aws(on_idle=1, off=7), **self.FULL}, queue=9, cfg=cold,
                      instances=instance("running", 1200))
        self.assertEqual(got["aws-spot-1"], ("withhold", "aws-idle-0"))

    def test_a_lan_job_alone_does_not_start_an_off_lan_instance(self):
        # It would start, sit idle for idle_off_seconds, stop and start again, forever.
        (got,) = [d for d in decide(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped"),
                                    waiting=[LAN_JOB] * 3) if d[0] == "aws-spot-1"]
        self.assertEqual(got[1], "hold")
        self.assertEqual(got[4], "3 jobs wait, none it can take")

    def test_a_job_it_can_take_starts_it_beside_lan_jobs(self):
        got = actions(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped"),
                      waiting=[LAN_JOB, frozenset({"self-hosted", "unity-full", "linux"})])
        self.assertEqual(got["aws-spot-1"], ("start", ""))

    def test_a_withheld_only_host_still_takes_a_unity_job(self):
        # Stopped, so every runner is offline, and the governor withheld all of them.
        rows = [{"metric": {"name": f"aws-{i}", "host": "aws-spot-1", "state": "offline",
                            "labels": "host-aws-spot-1,Linux,self-hosted,unity-withheld,X64"}, "value": [0, "0"]}
                for i in range(2)]
        with mock.patch.object(governor, "prom", return_value=rows) as prom:
            labels = governor.host_labels(AWS, "aws-spot-1")
        self.assertIn('host="aws-spot-1"', prom.call_args.args[0])
        self.assertNotIn("state=", prom.call_args.args[0])
        got = actions(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped", labels=labels), queue=1)
        self.assertEqual(got["aws-spot-1"], ("start", ""))
        self.assertEqual(actions(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped", labels=labels),
                                 waiting=[LAN_JOB])["aws-spot-1"], ("hold", ""))

    def test_no_runner_labels_read_never_starts(self):
        with mock.patch.object(governor, "prom", return_value=[]):
            self.assertEqual(governor.host_labels(AWS, "aws-spot-1"), frozenset())
        ec2 = FakeEc2("stopped")
        _, log, _ = self.power_cycle(ec2, cycles=2, labels=frozenset())
        self.assertNotIn(("start", "i-0abc"), ec2.calls)
        self.assertEqual(len([line for line in log if "no runner labels" in line]), 1)
        self.assertIn('ci_governor_decision{host="aws-spot-1",verdict="STANDBY",action="hold"} 1',
                      governor.State.metrics)

    def test_power_on_never_past_the_pool_cap(self):
        got = actions(self.STEADY, self.FULL, cfg={**AWS, "pool_max_admitted": 2}, instances=instance("stopped"))
        self.assertEqual(got["aws-spot-1"], ("hold", ""))

    # The cost gate (config.json's live values): a start costs money, so only a deep queue,
    # held, starts it; a day's running time is capped; the Infra page's override beats both.
    def heavy(self, inst, queue=5, runners=None, waiting=None):
        got = {h: (a, r, why) for h, a, r, _, why in decide(self.STEADY, self.FULL if runners is None else runners,
                                                         queue=queue, cfg=HEAVY, instances=inst, waiting=waiting)}
        return got["aws-spot-1"]

    def test_live_gate_values(self):
        self.assertEqual({k: LIVE_POWER[k] for k in governor.POWER_DEFAULTS},
                         {"idle_off_seconds": 900, "start_min_excess": 4, "start_after_seconds": 300,
                          "start_now_excess": 8, "max_hours_day": 24, "fallback_seconds": 3600})
        self.assertEqual(LIVE["warm_idle_off_seconds"], 2700)

    def test_no_start_below_the_excess(self):
        inst = instance("stopped", want_since=1)
        action, _, why = self.heavy(inst, queue=3)
        self.assertEqual(action, "hold")
        self.assertIn("starts at 4", why)
        self.assertIsNone(inst["aws-spot-1"]["want_since"])

    def test_the_excess_counts_only_jobs_it_can_take(self):
        waiting = [LAN_JOB] * 6 + [UNITY_JOB] * 3
        self.assertEqual(self.heavy(instance("stopped", want_since=1), waiting=waiting)[0], "hold")

    def test_no_start_before_the_hold_time_then_a_start(self):
        inst = instance("stopped")
        action, _, why = self.heavy(inst)
        self.assertEqual((action, inst["aws-spot-1"]["want_since"]), ("hold", 10_000))
        self.assertIn("held 0s of 300s", why)
        inst["aws-spot-1"]["want_since"] = 10_000 - 299
        self.assertEqual(self.heavy(inst)[0], "hold")
        inst["aws-spot-1"]["want_since"] = 10_000 - 300
        self.assertEqual(self.heavy(inst)[0], "start")
        self.assertEqual(inst["aws-spot-1"]["next_cause"], "queue")

    def test_a_dip_below_the_excess_restarts_the_hold(self):
        inst = instance("stopped", want_since=1)
        self.heavy(inst, queue=2)
        self.assertEqual(self.heavy(inst)[0], "hold")
        self.assertEqual(inst["aws-spot-1"]["want_since"], 10_000)

    def test_the_daily_cap_blocks_a_start(self):
        action, _, why = self.heavy(instance("stopped", want_since=1, hours_today=12.0))
        self.assertEqual(action, "hold")
        self.assertIn("12h cap", why)

    def test_the_daily_cap_drains_then_stops_without_killing_a_job(self):
        def at_cap(runners):
            return self.heavy(instance("running", hours_today=12.5), runners={**runners, **self.FULL}, queue=9)
        self.assertEqual(at_cap(self.aws(on_busy=1, off=5))[:2], ("withhold", "aws-0"))
        action, _, why = at_cap({"aws-0": {**self.aws(on_busy=1)["aws-0"], "withheld": True}, **self.aws(off=5)})
        self.assertEqual(action, "hold")
        self.assertIn("1 jobs finishing", why)
        inst = instance("running", hours_today=12.5)
        self.assertEqual(self.heavy(inst, runners={**self.aws(off=6), **self.FULL}, queue=9)[0], "stop")
        self.assertEqual(inst["aws-spot-1"]["next_cause"], "cap")
        self.assertTrue(inst["aws-spot-1"]["draining"])

    def test_idle_off_is_900s_live_and_the_warm_host_waits_2700s(self):
        runners = {**self.aws(off=6), **RUNNERS}
        # Another burst host busy later is the warm one, so aws-spot-1 stops at 900s.
        later = {"aws-spot-2": {"state": "running", "busy_at": 10_000, "labels": AWS_LABELS}}
        two = {**HEAVY, "standby": {**HEAVY["standby"], "aws-spot-2": {"max_admitted": 6, "power": {
            **LIVE_POWER, "instance_id": "i-0def"}}}}
        for idle, want in ((899, "hold"), (900, "stop")):
            got = {h: a for h, a, *_ in decide(self.STEADY, runners, queue=1, cfg=two,
                                               instances={**instance("running", idle), **later})}
            self.assertEqual(got["aws-spot-1"], want)
        # Alone, it is the warm host (2026-11-14: all four stopped by 08:59, 14 gates at 09:02).
        self.assertEqual(self.heavy(instance("running", 2699), runners=runners, queue=1)[0], "hold")
        self.assertEqual(self.heavy(instance("running", 2700), runners=runners, queue=1)[0], "stop")

    def test_a_packed_host_is_never_the_warm_one(self):
        insts = {**instance("running", 0, packed=True),
                 "aws-spot-2": {"state": "running", "busy_at": 5_000, "labels": AWS_LABELS}}
        cfg = {**HEAVY, "standby": {**HEAVY["standby"], "aws-spot-2": {"max_admitted": 6, "power": {
            **LIVE_POWER, "instance_id": "i-0def"}}}}
        self.assertEqual(governor.warm_host(cfg, insts), "aws-spot-2")
        self.assertIsNone(governor.warm_host(cfg, instance("stopped")))

    def test_a_deep_queue_starts_at_once(self):
        # start_now_excess (8) past the other hosts skips the start hold; 7 still waits it out.
        for queue, want in ((7, "hold"), (8, "start")):
            inst = instance("stopped", want_since=10_000)
            self.assertEqual(self.heavy(inst, queue=queue, runners=self.FULL)[0], want)

    def test_override_on_starts_with_nothing_waiting(self):
        inst = instance("stopped", override="on", hours_today=13.0)
        self.assertEqual(self.heavy(inst, queue=0)[:2], ("start", ""))
        self.assertEqual(inst["aws-spot-1"]["next_cause"], "override")

    def test_override_on_survives_idle_and_the_cap(self):
        runners = {**self.aws(on_idle=1, off=5), **self.FULL}
        inst = instance("running", 5000, override="on", hours_today=13.0)
        self.assertEqual(self.heavy(inst, runners=runners, queue=0)[:2], ("admit", "aws-off-0"))
        full = {**self.aws(on_idle=6), **self.FULL}
        self.assertEqual(self.heavy(inst, runners=full, queue=0)[0], "hold")

    def test_a_pin_drains_a_standby_host_under_override_on(self):
        inst = instance("running", override="on")
        busy = {**self.aws(on_busy=2, on_idle=1, off=3), **self.FULL}
        got = decide(self.STEADY, busy, cfg=HEAVY, instances=inst, pins={"aws-spot-1": 0})
        self.assertIn(("aws-spot-1", "withhold", "aws-idle-0", "PINNED", "pinned to 0"), got)
        drained = {**self.aws(off=6), **self.FULL}
        self.assertEqual(self.heavy(inst, runners=drained)[:2], ("admit", "aws-off-0"))  # no pin: override on admits
        got = decide(self.STEADY, drained, cfg=HEAVY, instances=inst, pins={"aws-spot-1": 0})
        self.assertIn(("aws-spot-1", "hold", "", "PINNED", "overflow"), got)

    def test_a_pin_caps_a_standby_host_admits(self):
        inst = instance("running")
        two = {**self.aws(on_busy=2, off=4), **self.FULL}
        self.assertEqual(self.heavy(inst, runners=two, queue=9)[0], "admit")
        got = decide(self.STEADY, two, cfg=HEAVY, instances=inst, pins={"aws-spot-1": 2})
        self.assertEqual([d[1] for d in got if d[0] == "aws-spot-1"], ["hold"])
        got = decide(self.STEADY, two, cfg=HEAVY, instances=inst, pins={"aws-spot-1": 9})
        self.assertIn(("aws-spot-1", "admit", "aws-off-0", "STANDBY"), [d[:4] for d in got])

    def test_override_off_drains_then_stops(self):
        inst = instance("running", override="off")
        busy = {**self.aws(on_busy=2, off=4), **self.FULL}
        self.assertEqual(self.heavy(inst, runners=busy, queue=9)[:2], ("withhold", "aws-0"))
        self.assertEqual(self.heavy(inst, runners={**self.aws(off=6), **self.FULL}, queue=9)[0], "stop")
        self.assertEqual(inst["aws-spot-1"]["next_cause"], "override")

    def test_override_off_blocks_a_start(self):
        action, _, why = self.heavy(instance("stopped", want_since=1, override="off"), queue=20)
        self.assertEqual((action, why), ("hold", "override off"))

    def test_read_override(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d), \
                mock.patch("builtins.print") as out:
            self.assertEqual(governor.read_override("aws-spot-1", 100), ("", 0))  # no ConfigMap
            path = os.path.join(d, "aws-spot-1.power")
            for body, want in (('{"action": "on", "until": 200}', ("on", 200)),
                               ('{"action": "off", "until": 200}', ("off", 200)),
                               ('{"action": "on", "until": 50}', ("", 0)),     # ran out
                               ('{"action": "start", "until": 200}', ("", 0)),  # not an action
                               ('{"action": "on", "until": "200"}', ("", 0)),
                               ('{"action": "on", "until": 200, "slots": 0}', ("", 0)),  # another family's field
                               ("", ("", 0)), ("{}", ("", 0)), ("not json", ("", 0)), ("[1]", ("", 0))):
                with open(path, "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_override("aws-spot-1", 100), want, body)
        self.assertEqual(len([c for c in out.call_args_list if "unreadable" in c.args[0]]), 6)

    def test_only_the_family_key_counts_so_a_null_patch_lifts_it(self):
        # The bare <host> key the writers used before hc-651 is never read: a family key that
        # a merge-patch nulls away leaves nothing behind it.
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d), \
                mock.patch("builtins.print") as out:
            with open(os.path.join(d, "aws-spot-1"), "w") as f:
                f.write('{"action": "off", "until": 5000, "slots": 0}')
            self.assertEqual(governor.read_override("aws-spot-1", 1000), ("", 0))
            self.assertEqual(governor.read_pins(AWS, 1000), ({}, {}))
            out.assert_not_called()

    def test_effective_slots_follows_the_rule_that_moves_the_slots(self):
        held = lambda h: [governor.Action(h, "hold", "", "PINNED", "pinned")]
        pins = ({"deskslim-1": 1}, {"deskslim-1": "slots"})
        self.assertEqual(governor.effective_slots(CFG, "deskslim-1", held("deskslim-1"), pins, 3, 2), (1, "slots"))
        # A FULL host keeps its own verdict under a pin it is already within.
        full = [governor.Action("deskslim-1", "hold", "", "FULL", "full")]
        self.assertEqual(governor.effective_slots(CFG, "deskslim-1", full, pins, 3, 0), (0, "governor"))
        # A pin above what is registered is held to it.
        self.assertEqual(governor.effective_slots(CFG, "deskslim-1", held("deskslim-1"), ({"deskslim-1": 5}, {"deskslim-1": "slots"}), 3, 3), (3, "slots"))
        # A standby pin at or over its cap is no pin; under it, it is, and a reservation names itself.
        self.assertEqual(governor.effective_slots(AWS, "aws-spot-1", [], ({"aws-spot-1": 8}, {"aws-spot-1": "slots"}), 8, 6), (6, "governor"))
        self.assertEqual(governor.effective_slots(AWS, "aws-spot-1", [], ({"aws-spot-1": 0}, {"aws-spot-1": "reserve"}), 8, 6), (0, "reserve"))
        self.assertEqual(governor.effective_slots(AWS, "aws-spot-1", [], ({}, {}), 8, 6), (6, "governor"))

    def test_check_override_holds_each_key_to_the_schema(self):
        ok = [("aws-spot-1.power", '{"action": "off", "until": 1796112000}'),
              ("cachyos-x8664-main.slots", '{"slots": 0, "by": "machine-mode work"}'),
              ("cachyos-x8664-main.slots", '{"slots": 0, "local": 1, "by": "machine-mode reserved"}'),
              ("ms-a2.reserve", '{"until": 1796112000.5}'), ("Ms_A2.reserve", '{"until": 1}')]
        bad = [("aws-spot-1", '{"action": "off", "until": 1}'),          # the old bare key
               ("aws-spot-1.demand", '{"n": 1}'),                         # no such family
               ("aws.spot.power", '{"action": "on", "until": 1}'),          # one dot, before the family
               ("aws-spot-1.power", '{"action": "on", "until": Infinity}'),
               ("h.reserve", '{"until": NaN}'),
               ("aws-spot-1.power", '{"action": "on"}'),
               ("aws-spot-1.power", '{"action": "on", "until": 1, "by": "x"}'),
               ("h.slots", '{"slots": 2}'), ("h.slots", '{"slots": -1, "by": "x"}'),
               ("h.slots", '{"slots": true, "by": "x"}'), ("h.slots", '{"slots": 1.5, "by": "x"}'),
               ("h.slots", '{"slots": 0, "local": -1, "by": "x"}'), ("h.slots", '{"slots": 0, "local": "1", "by": "x"}'),
               ("h.reserve", '{"until": "2000"}'), ("h.reserve", "[1]"), ("h.reserve", "not json")]
        for key, raw in ok:
            self.assertEqual(governor.check_override(key, raw), "", (key, raw))
        for key, raw in bad:
            self.assertNotEqual(governor.check_override(key, raw), "", (key, raw))
        # The writers' tests call it as a command.
        run = lambda *a: subprocess.run([sys.executable, governor.__file__, "check-override", *a],
                                        capture_output=True, text=True)
        self.assertEqual(run(*ok[1]).returncode, 0)
        got = run("h.slots", '{"slots": 2}')
        self.assertEqual((got.returncode, got.stderr.strip()), (1, "override h.slots: no by"))

    def test_cap_override_holds_on_to_the_cap_from_first_sight(self):
        cap = 12 * 3600
        with mock.patch("builtins.print") as out:
            t = {}
            # 19 h "on", the daily hand-written one: held to 12 h from the cycle it is first seen,
            # and later cycles keep that first sight, so it ends then and does not slide.
            self.assertEqual(governor.cap_override("h", t, "on", 19 * 3600, 0, cap), ("on", cap))
            self.assertEqual(governor.cap_override("h", t, "on", 19 * 3600, 8 * 3600, cap), ("on", cap))
            self.assertEqual(governor.cap_override("h", t, "on", 19 * 3600, cap, cap), ("", 0))
            # A new end time is a new override; a short one runs to its own end.
            self.assertEqual(governor.cap_override("h", t, "on", cap + 7200, cap, cap), ("on", cap + 7200))
            # "off" is never capped, and none passes through.
            self.assertEqual(governor.cap_override("h", t, "off", 30 * 3600, 0, cap), ("off", 30 * 3600))
            self.assertEqual(governor.cap_override("h", t, "", 0, 0, cap), ("", 0))
        self.assertEqual(len([c for c in out.call_args_list if "held to 12 h" in c.args[0]]), 1)

    # The human's machine-mode: a pinned host goes to its slots whatever the readings say.
    PINNED = pool(("deskslim-1-1", "deskslim-1", True, 50, False), ("deskslim-1-2", "deskslim-1", False, 0, False),
                  ("deskslim-2-1", "deskslim-2", False, 0, False), ("deskslim-2-2", "deskslim-2", False, 0, False),
                  ("cachyos-x8664-main-1", "cachyos-x8664-main", False, 0, False))
    PIN_HOSTS = readings(**{"deskslim-2": COOL, "cachyos-x8664-main": COOL, "deskslim-1": COOL})

    def test_pin_withholds_the_idle_slot_first_on_a_cool_host(self):
        self.assertEqual(actions(self.PIN_HOSTS, self.PINNED, pins={"deskslim-1": 1})["deskslim-1"],
                         ("withhold", "deskslim-1-2"))

    def test_pin_withholds_a_busy_slot_past_the_cooldown(self):
        runners = {**self.PINNED, "deskslim-1-2": {**self.PINNED["deskslim-1-2"], "withheld": True}}
        self.assertEqual(actions(self.PIN_HOSTS, runners, pins={"deskslim-1": 0}, last={"deskslim-1": 9_999})["deskslim-1"],
                         ("withhold", "deskslim-1-1"))

    def test_pin_admits_with_no_queue_on_a_hot_host(self):
        runners = {**self.PINNED, "deskslim-1-2": {**self.PINNED["deskslim-1-2"], "withheld": True}}
        hosts = readings(**{"deskslim-2": COOL, "cachyos-x8664-main": COOL, "deskslim-1": HOT})
        self.assertEqual(actions(hosts, runners, queue=0, pins={"deskslim-1": 2})["deskslim-1"], ("admit", "deskslim-1-2"))

    def test_pin_met_holds_even_when_hot(self):
        hosts = readings(**{"deskslim-2": COOL, "cachyos-x8664-main": COOL, "deskslim-1": HOT})
        self.assertEqual(actions(hosts, self.PINNED, pins={"deskslim-1": 2})["deskslim-1"], ("hold", ""))

    def test_pin_stops_at_the_pool_floor(self):
        runners = pool(("deskslim-1-1", "deskslim-1", False, 0, False), ("deskslim-1-2", "deskslim-1", False, 0, False))
        out = {h: (a, w) for h, a, _, _, w in decide(self.PIN_HOSTS, runners, pins={"deskslim-1": 0})}
        self.assertEqual(out["deskslim-1"][0], "hold")
        self.assertIn("at the pool floor", out["deskslim-1"][1])

    def test_pin_stops_at_the_pool_cap(self):
        runners = {**self.PINNED, "deskslim-1-2": {**self.PINNED["deskslim-1-2"], "withheld": True}}
        cfg = {**CFG, "pool_max_admitted": 4}
        self.assertEqual(actions(self.PIN_HOSTS, runners, cfg=cfg, pins={"deskslim-1": 2})["deskslim-1"], ("hold", ""))

    def test_a_full_host_keeps_its_verdict_over_a_pin(self):
        hosts = readings(**{"deskslim-2": COOL, "cachyos-x8664-main": COOL, "deskslim-1": FULL})
        out = decide(hosts, self.PINNED, pins={"deskslim-1": 2})
        self.assertEqual([(a, v) for h, a, _, v, _ in out if h == "deskslim-1"], [("withhold", "FULL")])

    def test_a_pin_withholds_on_a_full_host(self):
        # A release reservation (pin 0) on a full host: the withhold is the pin's, not FULL's.
        hosts = readings(**{"deskslim-2": COOL, "cachyos-x8664-main": COOL, "deskslim-1": FULL})
        out = decide(hosts, self.PINNED, pins={"deskslim-1": 0})
        self.assertEqual([(a, r, v) for h, a, r, v, _ in out if h == "deskslim-1"], [("withhold", "deskslim-1-2", "PINNED")])

    def test_an_unpinned_host_is_unchanged(self):
        hosts = readings(**{"deskslim-2": HOT, "cachyos-x8664-main": COOL, "deskslim-1": COOL})
        self.assertEqual(actions(hosts, self.PINNED, pins={"deskslim-1": 2})["deskslim-2"], ("withhold", "deskslim-2-1"))


    def test_read_pins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d), \
                mock.patch("builtins.print") as out:
            self.assertEqual(governor.read_pins(CFG, 1000), ({}, {}))  # no ConfigMap
            path = os.path.join(d, "deskslim-1.slots")
            for body, want in (('{"slots": 1, "by": "m"}', {"deskslim-1": 1}), ('{"slots": 0, "by": "m"}', {"deskslim-1": 0}),
                               ('{"slots": -1, "by": "m"}', {}), ('{"slots": "2", "by": "m"}', {}),
                               ('{"slots": true, "by": "m"}', {}), ('{"slots": 1.5, "by": "m"}', {}),
                               ('{"slots": 1}', {}), ("{}", {}), ("", {}), ("not json", {}), ("[1]", {})):
                with open(path, "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_pins(CFG, 1000)[0], want, body)
            # A release reservation: its own key, a pin to 0 until its end, over the host's own pin.
            with open(path, "w") as f:
                f.write('{"slots": 2, "by": "m"}')
            for body, want, source in (('{"until": 2000}', 0, "reserve"), ('{"until": 1000.5}', 0, "reserve"),
                                       ('{"until": 1000}', 2, "slots"), ('{"until": 500}', 2, "slots"),
                                       ('{"until": "2000"}', 2, "slots"), ('{"until": true}', 2, "slots"),
                                       ("{}", 2, "slots"), ("", 2, "slots")):
                with open(os.path.join(d, "deskslim-1.reserve"), "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_pins(CFG, 1000), ({"deskslim-1": want}, {"deskslim-1": source}), body)
        lines = [c.args[0] for c in out.call_args_list]
        self.assertEqual(len([l for l in lines if "override unreadable" in l]), 11)

    def test_read_pins_covers_standby_hosts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d):
            with open(os.path.join(d, "aws-spot-1.slots"), "w") as f:
                f.write('{"slots": 0, "by": "m"}')
            with open(os.path.join(d, "aws-spot-1.power"), "w") as f:
                f.write('{"action": "on", "until": 5000}')
            self.assertEqual(governor.read_pins(AWS, 1000), ({"aws-spot-1": 0}, {"aws-spot-1": "slots"}))
            self.assertEqual(governor.read_override("aws-spot-1", 1000), ("on", 5000.0))

    def test_run_time_counts_per_utc_day(self):
        t, day = {}, 1_762_214_400  # 2025-11-04T00:00:00Z
        governor.count_run_time(t, "running", day + 60)
        self.assertEqual(t["run_seconds"], 0)  # the first sight books nothing
        governor.count_run_time(t, "running", day + 120)
        governor.count_run_time(t, "stopped", day + 180)
        governor.count_run_time(t, "running", day + 3600)  # a blind hour books two cycles at most
        self.assertEqual(t["run_seconds"], 60 + 120)
        governor.count_run_time(t, "running", day + 86_400 + 30)
        self.assertEqual((t["run_seconds"], t["day"]), (30, "2025-11-05"))  # only the part after midnight

    def test_a_restart_counts_the_day_from_the_launch_time(self):
        day = 1_762_214_400
        t = {}
        governor.count_run_time(t, "running", day + 5 * 3600, launched=day + 3600)
        self.assertEqual(t["hours_today"], 4)
        t = {}
        governor.count_run_time(t, "running", day + 3600, launched=day - 7200)  # started yesterday
        self.assertEqual(t["hours_today"], 1)
        t = {}
        governor.count_run_time(t, "stopped", day + 3600, launched=day + 60)  # stopped: nothing to book
        self.assertEqual(t["hours_today"], 0)

    def test_a_restart_restores_the_run_time_and_cause_earlier_pods_exported(self):
        day = 1_762_214_400
        pods = [{"metric": {}, "value": [0, "7.9"]}]
        last = [{"metric": {"host": "aws-spot-1", "state": "running", "reason": "queue"}, "value": [0, "1"]}]
        with mock.patch.object(governor, "prom", side_effect=[pods, last]) as prom:
            seconds, cause, up = RESTORED("aws-spot-1", day + 10 * 3600)
        self.assertAlmostEqual(seconds, 7.9 * 3600)
        self.assertEqual((cause, up), ("queue", True))
        self.assertIn(f"[{10 * 3600 - 2 * governor.CYCLE_SECONDS}s]", prom.call_args_list[0].args[0])
        with mock.patch.object(governor, "prom", side_effect=[[], []]):  # no cause on record
            self.assertEqual(RESTORED("aws-spot-1", day + 3600), (0.0, "", False))
        with mock.patch.object(governor, "prom") as prom:  # just past midnight, not up: ask again
            self.assertIsNone(RESTORED("aws-spot-1", day + 60))
        prom.assert_not_called()
        with mock.patch.object(governor, "prom", side_effect=urllib.error.URLError("down")), \
                mock.patch("builtins.print"):
            self.assertIsNone(RESTORED("aws-spot-1", day + 3600))

    def test_a_restart_finds_the_start_cause_since_the_launch_past_none_and_midnight(self):
        # hc-223: started by an override before midnight, then pods that exported "none".
        day = 1_762_214_400
        last = [{"metric": {"host": "aws-spot-1", "state": "running", "reason": "override"}, "value": [0, "1"]}]
        with mock.patch.object(governor, "prom", side_effect=[last]) as prom:
            back = RESTORED("aws-spot-1", day + 60, launched=day - 8 * 3600)
        self.assertEqual(back, (0.0, "override", True))
        query = prom.call_args.args[0]  # the day's hours are not read this close to midnight
        self.assertIn('reason!="none"', query)
        self.assertIn(f"[{8 * 3600 + 60 + 2 * governor.CYCLE_SECONDS}s:]", query)
        with mock.patch.object(governor, "prom", side_effect=[[], last]) as prom:  # restart 10s after a start
            self.assertEqual(RESTORED("aws-spot-1", day + 3600, launched=day + 3590), (0.0, "override", True))
        self.assertIn(f"[{10 + 2 * governor.CYCLE_SECONDS}s:]", prom.call_args.args[0])

    def test_a_stopped_host_reads_todays_stop_reason_not_back_to_its_old_launch(self):
        now = time.time()
        with mock.patch.object(governor, "restored", return_value=(0.0, "idle", False)) as back, \
                mock.patch.object(governor, "power_provider",
                                  return_value=(FakeEc2("stopped", launched=now - 21 * 86_400), "")), \
                mock.patch.object(governor, "host_labels", return_value=AWS_LABELS):
            t = {}
            governor.power_readings(AWS, {}, {"aws-spot-1": t}, now)
        self.assertEqual(back.call_args.args[2], 0.0)
        self.assertEqual(t["cause"], "idle")

    def test_a_restart_in_the_start_call_keeps_its_cause_though_the_sample_read_stopped(self):
        # hc-223: aws-build-1's last sample before a deploy was "stopped queue", the start
        # call's own cycle; the instance was running by the new pod's first cycle.
        now = time.time()
        with mock.patch.object(governor, "restored", return_value=(0.0, "queue", False)), \
                mock.patch.object(governor, "power_provider",
                                  return_value=(FakeEc2("running", launched=now - 30), "")), \
                mock.patch.object(governor, "host_labels", return_value=AWS_LABELS):
            t = {}
            governor.power_readings(AWS, {}, {"aws-spot-1": t}, now)
        self.assertEqual(t["cause"], "queue")
        with mock.patch.object(governor, "restored", return_value=(0.0, "idle", False)), \
                mock.patch.object(governor, "power_provider",
                                  return_value=(FakeEc2("running", launched=now - 30), "")), \
                mock.patch.object(governor, "host_labels", return_value=AWS_LABELS):
            t = {}
            governor.power_readings(AWS, {}, {"aws-spot-1": t}, now)
        self.assertNotIn("cause", t)  # the stop before this launch is not why it started

    def test_a_first_sight_keeps_the_larger_day_and_a_cause_that_matches_the_state(self):
        def first_sight(state, back, launched=0.0, now=time.time()):
            t = {}
            with mock.patch.object(governor, "restored", return_value=back), \
                    mock.patch.object(governor, "power_provider", return_value=(FakeEc2(state, launched=launched), "")), \
                    mock.patch.object(governor, "host_labels", return_value=AWS_LABELS):
                governor.power_readings(AWS, {}, {"aws-spot-1": t}, now)
            return t
        now = time.time()
        midnight = now - now % 86_400
        t = first_sight("running", (5 * 3600.0, "queue", True), launched=midnight + 60, now=midnight + 3600)
        self.assertEqual((t["hours_today"], t["cause"]), (5, "queue"))  # earlier pods counted more
        t = first_sight("running", (60.0, "queue", True), launched=midnight + 60, now=midnight + 3600)
        self.assertAlmostEqual(t["hours_today"], (3600 - 60) / 3600)  # the launch time counts more
        t = first_sight("stopped", (2 * 3600.0, "queue", True))
        self.assertEqual(t["hours_today"], 2)
        self.assertNotIn("cause", t)  # it was up, it is down now: the old cause is not this state's

    def test_a_failed_restore_is_asked_again_until_one_answers(self):
        t, ec2 = {}, FakeEc2("running")
        with mock.patch.object(governor, "restored", side_effect=[None, (3 * 3600.0, "queue", True)]) as back, \
                mock.patch.object(governor, "power_provider", return_value=(ec2, "")), \
                mock.patch.object(governor, "host_labels", return_value=AWS_LABELS):
            for _ in range(3):
                governor.power_readings(AWS, {}, {"aws-spot-1": t}, time.time())
        self.assertEqual(back.call_count, 2)  # unread, then read once and never again
        self.assertEqual((t["cause"], round(t["hours_today"])), ("queue", 3))

    def test_power_metrics_after_a_live_stop(self):
        ec2 = FakeEc2("running")
        hosts = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": BUSY})
        tracked = {"aws-spot-1": {"busy_at": 0, "day": time.strftime("%Y-%m-%d", time.gmtime()), "run_seconds": 7200.0}}
        self.power_cycle(ec2, observed=(hosts, {**self.aws(off=8), **self.FULL}, []), tracked=tracked)
        self.assertIn(("stop", "i-0abc"), ec2.calls)
        m = governor.State.metrics
        self.assertIn('ci_governor_power_state{host="aws-spot-1",state="draining",reason="idle"} 1', m)
        self.assertIn('ci_governor_power_hours_today{host="aws-spot-1"} 2.0', m)
        self.assertIn('ci_governor_power_max_hours_day{host="aws-spot-1"} 12', m)
        self.assertIn('ci_governor_power_override{host="aws-spot-1",action="none"} 0', m)

    def test_a_failed_start_keeps_the_old_reason(self):
        tracked = {}
        self.power_cycle(FakeEc2("stopped", fail=True), tracked=tracked)
        self.assertIn('ci_governor_power_state{host="aws-spot-1",state="stopped",reason="none"} 1',
                      governor.State.metrics)
        self.power_cycle(FakeEc2("stopped"), tracked=tracked)
        self.assertEqual(tracked["aws-spot-1"]["cause"], "queue")

    def test_a_power_host_reports_its_state(self):
        self.power_cycle(FakeEc2("pending"))
        self.assertIn('ci_governor_power_state{host="aws-spot-1",state="starting",reason="none"} 1',
                      governor.State.metrics)

    def power_cycle(self, ec2, cfg=AWS, observed=None, cycles=1, tracked=None, labels=AWS_LABELS, budget=None):
        hosts = readings(**{"deskslim-2": HOT, "cachyos-x8664-main": BUSY, "deskslim-1": BUSY})
        observed = observed or (hosts, dict(self.FULL, **{"deskslim-2-3": RUNNERS["deskslim-2-3"]}), [UNITY_JOB] * 9)
        last, tracked = {}, {} if tracked is None else tracked
        with mock.patch.object(governor, "observe", return_value=observed), \
                mock.patch.object(governor, "power_provider", return_value=(ec2, "")), \
                mock.patch.object(governor, "host_labels", return_value=labels), \
                mock.patch.object(governor, "job_durations", return_value={}), \
                mock.patch.object(governor, "apply") as apply, mock.patch("builtins.print") as out:
            for _ in range(cycles):
                governor.cycle(cfg, last, tracked, budget)
        return apply, [c.args[0] for c in out.call_args_list], last

    def test_a_cycle_acts_on_power_and_runners_alike(self):
        ec2 = FakeEc2("stopped")
        apply, log, _ = self.power_cycle(ec2)
        self.assertIn(("start", "i-0abc"), ec2.calls)
        apply.assert_called_once_with(AWS, "withhold", "deskslim-2-3", "deskslim-2")
        self.assertTrue(any(line.startswith("withhold deskslim-2-3 on deskslim-2: HOT") for line in log), log)

    def test_a_pin_and_a_verdict_act_in_one_cycle(self):
        hosts = readings(**{"deskslim-2": HOT, "cachyos-x8664-main": COOL, "deskslim-1": COOL})
        observed = (hosts, self.PINNED, [UNITY_JOB] * 2)
        with mock.patch.object(governor, "observe", return_value=observed), \
                mock.patch.object(governor, "read_pins", return_value=({"deskslim-1": 1}, {"deskslim-1": "slots"})), \
                mock.patch.object(governor, "apply") as apply, mock.patch("builtins.print") as out:
            governor.cycle(CFG, {})
        self.assertEqual(sorted(c.args[1:] for c in apply.call_args_list),
                         [("withhold", "deskslim-1-2", "deskslim-1"), ("withhold", "deskslim-2-1", "deskslim-2")])
        log = [c.args[0] for c in out.call_args_list]
        self.assertTrue(any(line.startswith("withhold deskslim-1-2 on deskslim-1: PINNED") for line in log), log)
        # What machine-mode reads instead of re-deriving the target, and agent-web's button limit.
        m = governor.State.metrics
        self.assertIn('ci_governor_effective_slots{host="deskslim-1",source="slots"} 1', m)
        self.assertIn('ci_governor_effective_slots{host="deskslim-2",source="governor"} 2', m)
        self.assertIn(f'ci_governor_override_max_hours {CFG["override_on_max_hours"]}', m)

    def test_a_failed_start_is_logged_and_retried_never_counted(self):
        ec2 = FakeEc2("stopped", fail=True)
        _, log, last = self.power_cycle(ec2, cycles=2)
        self.assertEqual([c for c in ec2.calls if c[0] == "start"], [("start", "i-0abc")] * 2)
        self.assertNotIn("aws-spot-1", last)
        self.assertTrue(any(line.startswith("start i-0abc failed") for line in log))
        self.assertIn('ci_governor_admitted{host="aws-spot-1"} 0', governor.State.metrics)

    def test_a_busy_slot_restarts_the_idle_clock(self):
        hosts = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": BUSY})
        tracked = {"aws-spot-1": {"busy_at": 0}}  # idle for hours by the clock
        busy = (hosts, {**self.aws(on_busy=1, off=7), **self.FULL}, [])
        with mock.patch.object(governor.time, "time", return_value=10_000):
            self.power_cycle(FakeEc2("running"), observed=busy, tracked=tracked)
        self.assertEqual(tracked["aws-spot-1"]["busy_at"], 10_000)

    def test_no_credentials_no_aws_call(self):
        hosts = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": BUSY})
        with mock.patch.object(governor, "AWS_KEY", ""), mock.patch.object(governor, "AWS_SECRET", ""), \
                mock.patch.object(governor, "observe", return_value=(hosts, self.FULL, [UNITY_JOB] * 9)), \
                mock.patch.object(governor, "job_durations", return_value={}), \
                mock.patch.object(governor.urllib.request, "urlopen") as urlopen, \
                mock.patch.object(governor, "apply"), mock.patch("builtins.print") as out:
            tracked = {}
            governor.cycle(AWS, {}, tracked)
            governor.cycle(AWS, {}, tracked)
        urlopen.assert_not_called()
        said = [c.args[0] for c in out.call_args_list if "power hook off" in c.args[0]]
        self.assertEqual(len(said), 1)
        self.assertNotIn('action="start"', governor.State.metrics)

    def test_sigv4_matches_botocore(self):
        # The Authorization botocore's SigV4Auth gives for the same request and time.
        t = time.strptime("20261103T120000Z", "%Y%m%dT%H%M%SZ")
        body = "Action=DescribeInstances&Version=2016-11-15&InstanceId.1=i-049219a501808a9cf"  # copy-guard: a SigV4 test vector, no host's instance
        got = governor.Ec2("us-east-2", "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY").sign(
            "ec2.us-east-2.amazonaws.com", body, t)
        self.assertTrue(got["authorization"].endswith(
            "Signature=3320628ae21eda068fe54f2eb85a7427cb9b6ee695233eeb39d0dc6c2261a413"))

    def test_budgets_sigv4_matches_botocore(self):
        # botocore SigV4Auth("budgets", "us-east-1") for the same DescribeBudget call and time.
        t = time.strptime("20261104T191920Z", "%Y%m%dT%H%M%SZ")
        body = json.dumps({"AccountId": "311141527647", "BudgetName": "ci-burst-monthly"})
        got = governor.sigv4("budgets", "us-east-1", "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                             "budgets.amazonaws.com", body, t,
                             {"x-amz-target": "AWSBudgetServiceGateway.DescribeBudget"}, "application/x-amz-json-1.1")
        self.assertTrue(got["authorization"].endswith(
            "Signature=2c9034076a6852e450ea063740e16dfd413b40727c55066bdea06f3c6517b6b0"))

    # The live budget's answers on 2026-11-04, trimmed.
    BUDGET = {"BudgetLimit": {"Amount": "200.0", "Unit": "USD"}, "LastUpdatedTime": 1793804782.188,
              "CalculatedSpend": {"ActualSpend": {"Amount": "12.035", "Unit": "USD"},
                                  "ForecastedSpend": {"Amount": "2.587", "Unit": "USD"}}}
    NOTIFICATIONS = [{"NotificationType": t, "Threshold": v, "ThresholdType": "ABSOLUTE_VALUE"}
                     for t in ("ACTUAL", "FORECASTED") for v in (100.0, 150.0)]
    ACTIONS = [{"ActionType": a, "Status": "STANDBY",
                "ActionThreshold": {"ActionThresholdValue": 180.0, "ActionThresholdType": "ABSOLUTE_VALUE"}}
               for a in ("APPLY_IAM_POLICY", "RUN_SSM_DOCUMENTS")]

    def test_budget_reading_in_dollars(self):
        b = governor.budget_reading(self.BUDGET, self.NOTIFICATIONS + [
            {"NotificationType": "ACTUAL", "Threshold": 80.0, "ThresholdType": "PERCENTAGE"}], self.ACTIONS)
        self.assertEqual((b["limit"], b["actual"], b["forecast"]), (200.0, 12.035, 2.587))
        self.assertEqual(b["alerts"], [("actual", 100.0), ("actual", 150.0), ("actual", 160.0),
                                       ("forecasted", 100.0), ("forecasted", 150.0)])
        self.assertEqual(b["actions"], [("APPLY_IAM_POLICY", "STANDBY", 180.0), ("RUN_SSM_DOCUMENTS", "STANDBY", 180.0)])
        self.assertEqual(governor.epoch("2026-11-04T07:06:22.188000-08:00"), 1793804782.188)
        self.assertIsNone(governor.budget_reading({**self.BUDGET, "CalculatedSpend": {}}, [], [])["forecast"])

    def budget_cycle(self, budget, answers):
        """One live cycle with the budget API answering from answers (op -> body, or an error)."""
        def urlopen(req, timeout):
            op = req.headers["X-amz-target"].split(".")[1]
            if isinstance(answers, Exception):
                raise answers
            resp = mock.MagicMock()
            resp.__enter__.return_value.read.return_value = json.dumps(answers[op]).encode()
            return resp
        cfg = {**AWS, "budget": {"name": "ci-burst-monthly", "account_id": "311141527647"}}
        with mock.patch.object(governor, "AWS_KEY", "k"), mock.patch.object(governor, "AWS_SECRET", "s"), \
                mock.patch.object(governor.urllib.request, "urlopen", side_effect=urlopen) as calls:
            out = self.power_cycle(FakeEc2("stopped"), cfg, budget=budget)
        return out, calls

    ANSWERS = property(lambda self: {"DescribeBudget": {"Budget": self.BUDGET},
                                     "DescribeNotificationsForBudget": {"Notifications": self.NOTIFICATIONS},
                                     "DescribeBudgetActionsForBudget": {"Actions": self.ACTIONS}})

    def test_budget_metrics_and_one_read_an_hour(self):
        budget = {}
        _, calls = self.budget_cycle(budget, self.ANSWERS)
        m = governor.State.metrics
        for line in ('ci_governor_budget_ok 1', 'ci_governor_budget_usd{kind="actual"} 12.04',
                     'ci_governor_budget_usd{kind="forecast"} 2.59', 'ci_governor_budget_usd{kind="limit"} 200.00',
                     'ci_governor_budget_updated 1793804782', 'ci_governor_budget_alert{basis="actual",usd="150"} 1',
                     'ci_governor_budget_action_usd{type="RUN_SSM_DOCUMENTS",status="STANDBY"} 180'):
            self.assertIn(line, m)
        self.assertEqual(calls.call_count, 3)
        _, calls = self.budget_cycle(budget, self.ANSWERS)  # the same hour: no read
        self.assertEqual(calls.call_count, 0)

    def test_a_failed_budget_read_keeps_the_numbers_and_changes_no_decision(self):
        budget = {}
        self.budget_cycle(budget, self.ANSWERS)
        budget["tried_at"] -= governor.BUDGET_POLL_SECONDS
        (_, log, _), _ = self.budget_cycle(budget, urllib.error.URLError("AccessDenied"))
        m = governor.State.metrics
        self.assertIn("ci_governor_budget_ok 0", m)
        self.assertIn('ci_governor_budget_usd{kind="actual"} 12.04', m)
        self.assertTrue(any(line.startswith("budget ci-burst-monthly unread") for line in log))
        self.assertTrue(any(line.startswith("start i-0abc on aws-spot-1") for line in log))

    def test_no_budget_block_no_budget_series(self):
        cfg = {k: v for k, v in AWS.items() if k != "budget"}
        self.power_cycle(FakeEc2("stopped"), cfg, budget={})
        self.assertNotIn("ci_governor_budget", governor.State.metrics)

    def test_rule_progress_while_idle(self):
        inst = instance("running", idle=600)
        decide(readings(), queue=0, cfg=HEAVY, instances=inst)
        self.assertEqual((inst["aws-spot-1"]["idle_seconds"], inst["aws-spot-1"]["want"]), (600, 0))

    def test_rule_progress_below_the_excess_and_while_held(self):
        hosts = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": BUSY})
        inst = instance("stopped")
        decide(hosts, runners=self.FULL, queue=2, cfg=HEAVY, instances=inst)
        self.assertEqual((inst["aws-spot-1"]["want"], inst["aws-spot-1"]["held_seconds"]), (2, 0))
        inst["aws-spot-1"]["want_since"] = 10_000 - 120
        decide(hosts, runners=self.FULL, queue=9, cfg=HEAVY, instances=inst)
        self.assertEqual((inst["aws-spot-1"]["want"], inst["aws-spot-1"]["held_seconds"]), (9, 120))

    def test_rule_metrics_and_last_change(self):
        cfg = {**AWS, "rates": {"aws-spot-1": {"usd_per_hour": 1.855, "basis": "on-demand"}}}
        tracked = {}
        with mock.patch.object(governor.time, "time", return_value=10_000):
            self.power_cycle(FakeEc2("stopped"), cfg, tracked=tracked)
        m = governor.State.metrics
        for line in ('ci_governor_power_idle_off_seconds{host="aws-spot-1"} 1200',
                     'ci_governor_power_start_min_excess{host="aws-spot-1"} 1',
                     'ci_governor_power_usd_per_hour{host="aws-spot-1"} 1.855',
                     'ci_governor_power_last_change{host="aws-spot-1",action="start"} 10000'):
            self.assertIn(line, m)

    def test_describe_reads_the_instance_state(self):
        xml = (b'<DescribeInstancesResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"><reservationSet>'
               b'<item><instancesSet><item><instanceState><code>80</code><name>stopped</name></instanceState>'
               b'<launchTime>2026-11-04T15:15:42.000Z</launchTime>'
               b'</item></instancesSet></item></reservationSet></DescribeInstancesResponse>')
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = xml
        with mock.patch.object(governor.urllib.request, "urlopen", return_value=resp) as urlopen:
            self.assertEqual(governor.Ec2("us-east-2", "k", "s").state("i-0abc"), ("stopped", 1793805342.0, ""))
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://ec2.us-east-2.amazonaws.com/")
        self.assertIn(b"Action=DescribeInstances", req.data)

    def test_live_admit_adds_before_it_removes(self):
        calls = []

        def gh(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                return {"runners": [{"id": 7, "name": "deskslim-1-2",
                                     "labels": [{"name": "self-hosted"}, {"name": "unity-withheld"}]}]}
            return None
        governor.gh = gh
        governor.apply(CFG, "admit", "deskslim-1-2", "deskslim-1")
        self.assertEqual([c[0] for c in calls], ["GET", "POST", "DELETE"])
        self.assertEqual(calls[1][2], {"labels": ["unity", "unity-full"]})
        self.assertTrue(calls[2][1].endswith("/7/labels/unity-withheld"))

    # A dedicated host (aws-build-1): its own label and withheld label, only its own jobs,
    # outside the shared pool's queue, floor, cap and standby order.
    BUILD_JOB = frozenset({"self-hosted", "linux", "unity-build"})
    BUILD_LABELS = frozenset({"self-hosted", "linux", "x64", "host-aws-build-1", "unity-build-withheld", "unity-build"})
    BUILD = {**CFG, "standby": {
        "aws-build-1": {"max_admitted": 3, "labels": ["unity-build"], "withheld_label": "unity-build-withheld",
                        "power": {**POWER, "instance_id": "i-0bld"}},
        **AWS["standby"]}}

    def both(self, build="stopped", burst="stopped", idle=0):
        return {"aws-build-1": {"state": build, "busy_at": 10_000 - idle, "labels": self.BUILD_LABELS},
                "aws-spot-1": {"state": burst, "busy_at": 10_000 - idle, "labels": AWS_LABELS}}

    def build(self, on_busy=0, on_idle=0, off=0):
        specs = [(f"bld-{i}", "aws-build-1", True, 100 - i, False) for i in range(on_busy)]
        specs += [(f"bld-idle-{i}", "aws-build-1", False, 0, False) for i in range(on_idle)]
        specs += [(f"bld-off-{i}", "aws-build-1", False, 0, True) for i in range(off)]
        return pool(*specs)

    def test_a_waiting_build_starts_the_build_host_only(self):
        # deskslim-2-3 is idle, which would cover one pool job: it covers no build.
        got = actions(self.STEADY, RUNNERS, cfg=self.BUILD, instances=self.both(), waiting=[self.BUILD_JOB])
        self.assertEqual(got["aws-build-1"], ("start", ""))
        self.assertEqual(got["aws-spot-1"], ("hold", ""))

    def test_gate_jobs_never_start_the_build_host(self):
        # Negative control: a full pool queue starts aws-spot-1, and aws-build-1, first in the
        # standby order, neither starts nor stands ahead of it.
        got = actions(self.STEADY, self.FULL, cfg=self.BUILD, instances=self.both(), queue=5)
        self.assertEqual(got["aws-build-1"], ("hold", ""))
        self.assertEqual(got["aws-spot-1"], ("start", ""))

    def test_a_build_does_not_admit_a_pool_slot(self):
        runners = {**self.aws(off=2), **self.FULL}
        got = actions(self.STEADY, runners, cfg=self.BUILD, instances=self.both(burst="running"),
                      waiting=[self.BUILD_JOB])
        self.assertEqual(got["aws-spot-1"], ("hold", ""))

    def test_the_build_host_admits_past_the_pool_cap(self):
        cfg = {**self.BUILD, "pool_max_admitted": 0}
        got = actions(self.STEADY, {**self.build(off=3), **self.FULL}, cfg=cfg,
                      instances=self.both(build="running"), waiting=[self.BUILD_JOB])
        self.assertEqual(got["aws-build-1"], ("admit", "bld-off-0"))

    def test_build_slots_do_not_count_toward_the_pool_cap(self):
        # Two pool runners admitted and a cap of 3: three busy build slots leave room for one more.
        cfg = {**self.BUILD, "pool_max_admitted": 3}
        runners = {**self.build(on_busy=3), **self.aws(off=2), **self.FULL}
        got = actions(self.STEADY, runners, cfg=cfg, instances=self.both(build="running", burst="running"), queue=5)
        self.assertEqual(got["aws-spot-1"], ("admit", "aws-off-0"))

    def test_the_build_host_goes_back_when_no_build_waits(self):
        # Gate jobs wait, but none is the build host's, and no build has waited for the dwell.
        inst = self.both(build="running")
        inst["aws-build-1"]["waited_at"] = 10_000 - DWELL
        got = actions(self.STEADY, {**self.build(on_idle=1), **self.FULL}, cfg=self.BUILD, instances=inst, queue=5)
        self.assertEqual(got["aws-build-1"], ("withhold", "bld-idle-0"))

    def test_the_build_host_stops_when_idle(self):
        got = actions(self.STEADY, {**self.build(off=3), **self.FULL}, cfg=self.BUILD,
                      instances=self.both(build="running", idle=1200), queue=5)
        self.assertEqual(got["aws-build-1"], ("stop", ""))

    def test_build_host_labels_swap_only_its_own(self):
        for action, has, added, removed in (("admit", "unity-build-withheld", "unity-build", "unity-build-withheld"),
                                            ("withhold", "unity-build", "unity-build-withheld", "unity-build")):
            calls = []

            def gh(method, path, body=None):
                calls.append((method, path, body))
                if method == "GET":
                    return {"runners": [{"id": 9, "name": "bld-1",
                                         "labels": [{"name": "self-hosted"}, {"name": has}]}]}
                return None
            governor.gh = gh
            governor.apply(self.BUILD, action, "bld-1", "aws-build-1")
            self.assertEqual(calls[1][2], {"labels": [added]}, action)
            self.assertEqual([c[1].rsplit("/", 1)[1] for c in calls if c[0] == "DELETE"], [removed], action)

    def test_a_waiting_build_is_not_in_the_pool_queue(self):
        jobs = [self.BUILD_JOB, UNITY_JOB, self.BUILD_JOB]
        self.assertEqual(governor.pool_jobs(jobs, governor.build_marks(self.BUILD)), [UNITY_JOB])

    def test_the_build_pool_is_read(self):
        self.assertEqual(governor.pools(self.BUILD), ["unity", "unity-build", "unity-wide"])
        with mock.patch.object(governor, "prom", return_value=[]) as prom:
            governor.host_labels(self.BUILD, "aws-build-1")
        self.assertIn('pool=~"unity|unity-build|unity-wide"', prom.call_args.args[0])

    # A paired host (aws-build-1, slot groups in the topology): build slots 1-3 each share their cores
    # and memory with two test slots, and a build and a test never run in one pair.
    PAIRED = {**CFG, "standby": {"aws-build-1": {
        "max_admitted": 9, "labels": ["unity-build"], "withheld_label": "unity-build-withheld",
        "groups": {"1": [4, 5], "2": [6, 7], "3": [8, 9]}, "power": {**POWER, "instance_id": "i-0bld"}}}}
    RELEASE_JOB = BUILD_JOB | {"unity-release"}

    def paired(self, spec, waiting, cfg=None, pins=None, **inst):
        """This cycle's actions on aws-build-1, as (action, slot number)."""
        up = {"aws-build-1": {"state": "running", "busy_at": 10_000, "labels": PAIRED_LABELS, **inst}}
        return sorted((a, int(r.rsplit("-", 1)[1]) if r else 0)
                      for h, a, r, _, _ in decide(self.STEADY, paired_slots(spec), cfg=cfg or self.PAIRED,
                                                  instances=up, waiting=waiting, pins=pins)
                      if h == "aws-build-1" and a != "hold")

    def test_no_build_admits_six_test_slots_and_no_build_slot(self):
        self.assertEqual(settle(self.PAIRED, ".........", [UNITY_JOB] * 10), "...iiiiii")
        # As the play registers them, every slot admitted: the build slots step aside.
        self.assertEqual(settle(self.PAIRED, "iiiiiiiii", [UNITY_JOB] * 10), "...iiiiii")

    def test_a_waiting_build_takes_pair_1_once_both_its_tests_end(self):
        # Both of pair 1's test slots step aside in one cycle, and the other four are left alone.
        self.assertEqual(self.paired("...bbbbbb", [BUILD_JOB]), [("withhold", 4), ("withhold", 5)])
        self.assertEqual(self.paired("...hhbbbb", [BUILD_JOB]), [])
        self.assertEqual(self.paired("....hbbbb", [BUILD_JOB]), [])
        self.assertEqual(self.paired(".....bbbb", [BUILD_JOB]), [("admit", 1)])

    def test_a_build_takes_the_pair_with_the_fewest_busy_tests(self):
        self.assertEqual(self.paired("...bbbbii", [BUILD_JOB]), [("withhold", 8), ("withhold", 9)])
        # A release build asks unity-release, which slot 3 lacks, so it takes pair 1 instead.
        self.assertEqual(self.paired("...bbbbii", [self.RELEASE_JOB] + [UNITY_JOB] * 2), [("withhold", 4), ("withhold", 5)])

    def test_two_builds_leave_two_test_slots(self):
        self.assertEqual(settle(self.PAIRED, "...iiiiii", [BUILD_JOB] * 2 + [UNITY_JOB] * 10), "ii.....ii")
        self.assertEqual(settle(self.PAIRED, ".........", [BUILD_JOB] * 3 + [UNITY_JOB] * 10), "iii......")

    def test_a_busy_build_keeps_its_pair(self):
        # No build waits and pair 1 has the most idle test slots, yet its build keeps it.
        self.assertEqual(settle(self.PAIRED, "b....iibb", [UNITY_JOB] * 10), "b....iibb")
        # A second build takes pair 2, the one with no busy test, and pair 1 is untouched.
        self.assertEqual(settle(self.PAIRED, "b....iibb", [BUILD_JOB] + [UNITY_JOB] * 10), "bi.....bb")

    def test_an_idle_build_slot_steps_aside_when_no_build_waits(self):
        self.assertEqual(self.paired("i....bbbb", [UNITY_JOB] * 5), [("withhold", 1)])
        # Its test slots come in only once it reads withheld.
        self.assertEqual(self.paired(".....bbbb", [UNITY_JOB] * 5), [("admit", 4)])

    def test_slots_not_registered_yet(self):
        # The roster is deployed before the play registers the slots.
        self.assertEqual(self.paired("...------", [BUILD_JOB]), [("admit", 1)])
        self.assertEqual(settle(self.PAIRED, "---......", [UNITY_JOB] * 10), "---iiiiii")
        self.assertEqual(self.paired("---iiiiii", [BUILD_JOB] + [UNITY_JOB] * 6), [])

    def test_a_pin_caps_the_test_slots_only(self):
        self.assertEqual(settle(self.PAIRED, ".........", [UNITY_JOB] * 10, pins={"aws-build-1": 2}), "...ii....")
        self.assertEqual(settle(self.PAIRED, "...iiiiii", [BUILD_JOB] + [UNITY_JOB] * 10, pins={"aws-build-1": 0}),
                         "i........")
        # A pin at or over the six test slots caps nothing, so it is not reported as PINNED.
        up = {"aws-build-1": {"state": "running", "busy_at": 10_000, "labels": PAIRED_LABELS}}
        verdicts = {v for h, _, _, v, _ in decide(self.STEADY, paired_slots("...iiiiii"), cfg=self.PAIRED,
                                                  instances=up, waiting=[UNITY_JOB] * 10, pins={"aws-build-1": 7})
                    if h == "aws-build-1"}
        self.assertNotIn("PINNED", verdicts)

    def test_test_slots_count_toward_the_pool_cap(self):
        cfg = {**self.PAIRED, "pool_max_admitted": 2}
        self.assertEqual(self.paired("...bb....", [UNITY_JOB] * 5, cfg=cfg), [])

    def test_idle_off_drains_every_slot_then_stops(self):
        self.assertEqual(self.paired("i..iiiiii", [], busy_at=10_000 - 1200), [("withhold", 1)])
        self.assertEqual(self.paired(".........", [], busy_at=10_000 - 1200), [("stop", 0)])

    def test_a_build_and_a_test_never_share_a_pair(self):
        # Random arrivals and finishes against the live config. Run against #823's lend mode it
        # failed at once: it lent build slot 1 to the pool beside busy test slots 4 and 5.
        cfg = self.PAIRED
        total = {}
        for seed in range(20):
            clash, stats = simulate(cfg, seed)
            self.assertIsNone(clash, f"seed {seed}: (cycle, build slot) running beside its test slot")
            total = {k: total.get(k, 0) + v for k, v in stats.items()}
        # Not vacuous: builds ran, four test slots ran beside one, and all six ran with none.
        self.assertTrue(all(total.values()), total)

    def test_paired_slots_swap_their_own_labels(self):
        for action, name, has, added, removed in (
                ("admit", slot_name(1), "unity-build-withheld", ["unity-build"], ["unity-build-withheld"]),
                ("withhold", slot_name(1), "unity-build", ["unity-build-withheld"], ["unity-build"]),
                ("admit", slot_name(4), "unity-withheld", ["unity", "unity-full"], ["unity-withheld"]),
                ("withhold", slot_name(4), "unity", ["unity-withheld"], ["unity"])):
            calls = []

            def gh(method, path, body=None):
                calls.append((method, path, body))
                if method == "GET":
                    return {"runners": [{"id": 9, "name": name,
                                         "labels": [{"name": "self-hosted"}, {"name": "unity-gate"}, {"name": has}]}]}
                return None
            with mock.patch.object(governor, "gh", gh):
                governor.apply(self.PAIRED, action, name, "aws-build-1")
            self.assertEqual(calls[1][2], {"labels": added}, (action, name))
            self.assertEqual([c[1].rsplit("/", 1)[1] for c in calls if c[0] == "DELETE"], removed, (action, name))

    def test_a_step_aside_renders_one_decision_row(self):
        runners = paired_slots("...bbbbbb")
        up = {"aws-build-1": {"state": "running", "busy_at": 10_000, "labels": PAIRED_LABELS}}
        decisions = decide(self.STEADY, runners, cfg=self.PAIRED, instances=up, waiting=[BUILD_JOB])
        out = governor.render(self.PAIRED, self.STEADY, runners, 0, decisions, True)
        self.assertEqual(sum(1 for l in out.splitlines() if l.startswith('ci_governor_decision{host="aws-build-1"')), 1)

    def test_a_slot_held_for_its_pair_is_lent_and_one_held_for_load_is_not(self):
        lent = lambda spec: {governor.slot_of(n): to for n, (_, to) in governor.lent(self.PAIRED, paired_slots(spec)).items()}
        # No build: the build slots are lent to tests; pair 3's tests are both held, so slot 3 is not.
        self.assertEqual(lent("...iiii.."), {1: "tests", 2: "tests"})
        # A build runs in pair 1: its test slots are lent to it; pair 2 lends its build slot.
        self.assertEqual(lent("b....i..."), {4: "build", 5: "build", 2: "tests"})
        # A slot still busy finishing a job uses the cores, withheld or not.
        self.assertEqual(lent("h........"), {4: "build", 5: "build"})
        self.assertEqual(lent("...h....."), {1: "tests"})
        # A drained host lends nothing, and so renders no lent row.
        self.assertEqual(lent("........."), {})
        out = governor.render(self.PAIRED, self.STEADY, paired_slots("...iiiiii"), 0, [], True)
        self.assertIn('ci_governor_lent{host="aws-build-1",name="%s",to="tests"} 1' % slot_name(1), out)
        self.assertEqual(sum(l.startswith("ci_governor_lent{") for l in out.splitlines()), 3)

    def test_a_stopped_paired_host_starts_for_tests_on_pool_thresholds(self):
        # Its build thresholds are 1 job and 60 s; for tests it waits for 4 and 300 s, as the
        # burst hosts do.
        cfg = {**self.PAIRED, "standby": {"aws-build-1": {
            **self.PAIRED["standby"]["aws-build-1"], "power": {**POWER, "start_min_excess": 1, "start_after_seconds": 60}}}}

        def start(held, **kw):
            stopped = {"aws-build-1": {"state": "stopped", "busy_at": 10_000, "labels": PAIRED_LABELS,
                                       "want_since": 10_000 - held}}
            return actions(self.STEADY, self.FULL, cfg=cfg, instances=stopped, **kw)["aws-build-1"][0]
        # Held past both waits, so only the job count can hold it: 3 is under the pool's 4.
        self.assertEqual(start(400, queue=3), "hold")
        self.assertEqual(start(400, queue=4), "start")
        # Held 120 s: under the pool's 300 s, past the build's 60 s.
        self.assertEqual(start(120, queue=6), "hold")
        self.assertEqual(start(120, waiting=[BUILD_JOB]), "start")

    def test_a_cloud_build_host_takes_its_pairs_from_the_topology(self):
        self.assertEqual(governor.pairs(PAID, "aws-build-1"), PAIRS)

    def test_admit_leaves_full_off_a_host_that_takes_no_full_runs(self):
        calls = []

        def gh(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                return {"runners": [{"id": 7, "name": "ws-1",
                                     "labels": [{"name": "self-hosted"}, {"name": "unity-withheld"}]}]}
            return None
        governor.gh = gh
        cfg = {**CFG, "hosts": {**CFG["hosts"], "ws": {"full": False}}}
        governor.apply(cfg, "admit", "ws-1", "ws")
        self.assertEqual(calls[1][2], {"labels": ["unity"]})

    def test_no_live_slot_takes_a_small_kind_its_hosts_seats_serve(self):
        # One path for a small job (hc-693): every host that takes light or heavy has seats for them,
        # so no Unity slot shares a kind, and a swap still drops one a slot registered with ("moved").
        cfg = governor.load_config(CONFIG)
        self.assertIn("ms-a2-unity-pool-1-heavy-1", cfg["seats"])
        self.assertFalse({c for caps in cfg["shared"].values() for c in caps} & {"light", "heavy"})
        self.assertTrue({"light", "heavy"} <= set(cfg["moved"]))

    def every_cap(self, action, have, name="ms-a2-unity-pool-1", host="ms-a2"):
        calls = []

        def gh(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                return {"runners": [{"id": 7, "name": name, "labels": [{"name": l} for l in have]}]}
            return None
        governor.gh = gh
        governor.apply(LIVE, action, name, host)
        return calls

    def test_withhold_drops_every_cap_the_slot_takes(self):
        calls = self.every_cap("withhold", ["self-hosted", "unity", "unity-full", "light", "lan"])
        self.assertEqual(calls[1][2], {"labels": ["unity-withheld"]})
        self.assertEqual(sorted(c[1].rsplit("/", 1)[1] for c in calls if c[0] == "DELETE"), ["light", "unity", "unity-full"])

    def test_admit_gives_every_cap_back_but_the_kinds_its_seats_serve(self):
        # ms-a2's light and heavy run on its seats, so a slot that registered with them sheds them.
        calls = self.every_cap("admit", ["self-hosted", "unity-withheld", "light", "heavy", "lan"])
        self.assertEqual(calls[1][2], {"labels": ["unity", "unity-full"]})
        self.assertEqual(sorted(c[1].rsplit("/", 1)[1] for c in calls if c[0] == "DELETE"),
                         ["heavy", "light", "unity-withheld"])

    # windows-media-ci (retired since) lost heavy in the topology (no /dev/kvm, #1269), but pool-2 kept the label
    # it registered with (live 2026-11-19): admit and withhold both drop a cap the slot no longer takes.
    STALE = {"name": "windows-media-ci-unity-pool-2", "host": "windows-media-ci"}

    def test_admit_drops_a_cap_the_topology_took_away(self):
        self.assertNotIn(self.STALE["name"], LIVE["shared"])
        calls = self.every_cap("admit", ["self-hosted", "heavy", "unity-withheld", "lan"], **self.STALE)
        self.assertEqual(calls[1][2], {"labels": ["unity", "unity-full"]})
        self.assertEqual(sorted(c[1].rsplit("/", 1)[1] for c in calls if c[0] == "DELETE"), ["heavy", "unity-withheld"])

    def test_withhold_drops_a_cap_the_topology_took_away(self):
        calls = self.every_cap("withhold", ["self-hosted", "heavy", "unity", "unity-full", "light", "lan"], **self.STALE)
        self.assertEqual(calls[1][2], {"labels": ["unity-withheld"]})
        self.assertEqual(sorted(c[1].rsplit("/", 1)[1] for c in calls if c[0] == "DELETE"),
                         ["heavy", "light", "unity", "unity-full"])

    def test_the_live_config_keeps_full_runs_off_the_workstation(self):
        cfg = governor.load_config(CONFIG)
        self.assertEqual(governor.admit_labels(cfg, "cachyos-x8664-main"), ["unity"])
        self.assertEqual(governor.admit_labels(cfg, "deskslim-2"), ["unity", "unity-full"])

    def test_disk_held_only_with_withheld_slots_under_the_admit_line(self):
        def gauge(disk, withheld):
            hosts = readings(**{"deskslim-2": {**COOL, "disk": disk}})
            runners = pool(("r-1", "deskslim-2", False, 0, withheld))
            return 'ci_governor_disk_held{host="deskslim-2"} 1' in governor.render(CFG, hosts, runners, 3, [], True)
        self.assertTrue(gauge(0.23, True))
        self.assertFalse(gauge(0.23, False))
        self.assertFalse(gauge(0.25, True))

    def test_no_pool_runners_in_the_metrics_is_a_blind_cycle(self):
        # 2026-11-01: GitHub's limit emptied the exporter's org runner list, and the governor
        # reported ok with 0 admitted on every host while the runners were all still working.
        with mock.patch.object(governor, "prom", return_value=[]), \
                mock.patch.object(governor, "apply") as apply, mock.patch("builtins.print"):
            governor.cycle(CFG, {})
        self.assertEqual(governor.State.metrics, "ci_governor_up 1\nci_governor_ok 0\n")
        apply.assert_not_called()

    def test_a_stuck_run_is_not_queue_demand(self):
        # A queued run GitHub never gave jobs (the exporter's stuck="true") holds no slot's
        # worth of work; counting it kept standby admitted for nothing. Same filter as cc:pool_waiting.
        def fake(query):
            if query.startswith("github_runner_state"):
                return [{"metric": {"scope": "org", "name": "r-1", "host": "deskslim-2", "state": "idle"},
                         "value": [0, "0"]}]
            if "github_actions_job_waiting" in query:
                job = {"metric": {"labels": "self-hosted,Unity"}, "value": [0, "60"]}
                return [job] if 'stuck!="true"' in query else [job] * 3
            return []
        with mock.patch.object(governor, "prom", side_effect=fake):
            _, _, waiting = governor.observe("unity")
        self.assertEqual(waiting, [UNITY_JOB])

    def test_a_job_naming_no_kind_is_demand_when_a_pool_runner_carries_its_labels(self):
        # runs-on: [self-hosted, linux, host-ms-a2] names no kind, so the exporter waits it as
        # pool="other" (a slot that takes unity, light and heavy is no one kind's). It is this
        # pool's job because a pool runner carries every label; one only another runner carries,
        # and an unread run's empty labels, are not.
        def fake(query):
            if query.startswith("github_runner_state"):
                return [{"metric": {"scope": "org", "name": "ms-a2-unity-pool-1", "host": "ms-a2", "state": "idle",
                                    "labels": "self-hosted,Linux,host-ms-a2,unity,light,heavy"}, "value": [0, "0"]}]
            if query.startswith("github_actions_job_waiting"):
                self.assertIn('pool=~"unity|other"', query)
                return [{"metric": {"pool": "other", "labels": ls}, "value": [0, "60"]}
                        for ls in ("self-hosted,linux,host-ms-a2", "self-hosted,gpu", "")]
            return []
        with mock.patch.object(governor, "prom", side_effect=fake):
            _, _, waiting = governor.observe(["unity"])
        self.assertEqual(waiting, [frozenset({"self-hosted", "linux", "host-ms-a2"})])



# Two powered standby hosts in start order, Spot first, as load_config orders them by rate.
SPOT = {**POWER, "instance_id": "i-0spot"}
TWO = {**CFG, "standby": {"aws-spot-2": {"max_admitted": 6, "power": SPOT},
                          "aws-spot-1": {"max_admitted": 6, "power": POWER}}}
SPOT_LABELS = frozenset(AWS_LABELS - {"host-aws-spot-1"} | {"host-aws-spot-2"})
STEADY = readings(**{"deskslim-2": BUSY, "cachyos-x8664-main": BUSY, "deskslim-1": BUSY})
HOME_BUSY = pool(("deskslim-2-1", "deskslim-2", True, 900, False), ("deskslim-1-1", "deskslim-1", True, 50, False))


def two(spot="stopped", od="stopped", **spot_more):
    return {"aws-spot-2": {"state": spot, "busy_at": 10_000, "labels": SPOT_LABELS, **spot_more},
            "aws-spot-1": {"state": od, "busy_at": 10_000, "labels": AWS_LABELS}}


def live_config():
    with open(CONFIG) as f:
        return json.load(f)


def live_topology():
    with open(os.environ["TOPOLOGY"]) as f:
        return json.load(f)


def load_files(conf, topo):
    """load_config over a config.json and a topology.json written from these dicts; a topology
    with no "labels" gets the rendered one's."""
    topo = {"labels": live_topology()["labels"], **topo}
    with tempfile.TemporaryDirectory() as d:
        for name, body in (("c.json", conf), ("t.json", topo)):
            with open(os.path.join(d, name), "w") as f:
                json.dump(body, f)
        return governor.load_config(os.path.join(d, "c.json"), os.path.join(d, "t.json"))


# The live roster plus a paid build host as aws-build-1 rendered until the AWS hosts were retired
# (2026-11-19), so the LAN build slot's tests still check what it leaves to a paid host.
PAID_BUILD = {"site": "cloud", "full": True, "power": {"provider": "aws", "instance_id": "i-0bld", "region": "us-east-2"},
              "slots": [{"name": slot_name(b), "caps": ["unity-build"], "group": [slot_name(t) for t in tests]}
                        for b, tests in PAIRS.items()] + [{"name": slot_name(t), "caps": ["unity"]} for t in range(4, 10)]}


def with_paid_build(edit=None):
    conf, topo = live_config(), live_topology()
    conf["power"] = {"aws-build-1": {"max_hours_day": 24, "start_min_excess": 1, "start_after_seconds": 60}}
    conf["rates"] = {"aws-build-1": {"usd_per_hour": 1.855, "basis": "on-demand"}}
    topo["hosts"]["aws-build-1"] = PAID_BUILD
    if edit:
        edit(topo)
    return load_files(conf, topo)


PAID = with_paid_build()


class StandbyOrderTest(unittest.TestCase):
    def got(self, queue, inst, runners=HOME_BUSY):
        return {h: (a, why) for h, a, _, _, why in decide(STEADY, runners, queue=queue, cfg=TWO, instances=inst)}

    def test_standby_starts_cheapest_first_with_its_slot_count(self):
        topo = {"hosts": {"b": {"site": "cloud", "slots": [{"name": "b-unity-pool-1", "caps": ["unity"]}]},
                          "a": {"site": "cloud", "slots": [{"name": f"a-unity-pool-{i}", "caps": ["unity"]} for i in (1, 2)]},
                          "lan": {"site": "local", "slots": [{"name": "lan-light-1", "caps": ["light"]}]}}}
        cfg = load_files({**live_config(), "hosts": {}, "power": {}, "rates": {
            "a": {"usd_per_hour": 0.9, "basis": "on-demand"}, "b": {"usd_per_hour": 0.1, "basis": "spot-estimate"}}}, topo)
        self.assertEqual(list(cfg["standby"]), ["b", "a"])
        self.assertEqual(cfg["standby"]["a"], {"max_admitted": 2})
        self.assertEqual(cfg["hosts"], {})
        self.assertEqual(cfg["rates"]["lan"], {"usd_per_hour": 0, "basis": "owned"})

    def test_spot_starts_first_and_on_demand_waits(self):
        got = self.got(6, two())
        self.assertEqual(got["aws-spot-2"][0], "start")
        self.assertEqual(got["aws-spot-1"][0], "hold")
        self.assertIn("aws-spot-2 ahead of it take 6", got["aws-spot-1"][1])

    def test_a_queue_past_the_first_host_starts_both(self):
        got = self.got(7, two())
        self.assertEqual((got["aws-spot-2"][0], got["aws-spot-1"][0]), ("start", "start"))

    def test_a_booting_spot_holds_the_next_back(self):
        # Running, runners not online yet: all six slots are still to come.
        self.assertEqual(self.got(6, two("running"))["aws-spot-1"][0], "hold")
        self.assertEqual(self.got(6, two("pending"))["aws-spot-1"][0], "hold")

    def test_a_full_spot_lets_the_next_start(self):
        spot_busy = pool(*[(f"spot-{i}", "aws-spot-2", True, 100, False) for i in range(6)])
        got = self.got(3, two("running"), {**HOME_BUSY, **spot_busy})
        self.assertEqual(got["aws-spot-1"][0], "start")

    def test_a_passed_over_spot_lets_the_next_start_at_once(self):
        got = self.got(2, two(fallback_until=10_001, fallback_why="capacity"))
        self.assertEqual(got["aws-spot-2"], ("hold", "passed over after a capacity for 1s more"))
        self.assertEqual(got["aws-spot-1"][0], "start")

    def test_the_pass_over_runs_out(self):
        got = self.got(2, two(fallback_until=10_000, fallback_why="capacity"))
        self.assertEqual((got["aws-spot-2"][0], got["aws-spot-1"][0]), ("start", "hold"))

    def test_a_pass_over_beats_override_on(self):
        # hc-277: an "on" restarted a just-reclaimed or refused host every cycle.
        got = self.got(0, two(fallback_until=20_000, fallback_why="reclaim", override="on"))
        self.assertEqual(got["aws-spot-2"], ("hold", "passed over after a reclaim for 10000s more"))
        got = self.got(0, two(fallback_until=10_000, fallback_why="reclaim", override="on"))
        self.assertEqual(got["aws-spot-2"][0], "start")  # negative control: the pass-over ran out

    def test_a_spot_told_off_or_past_its_day_is_not_ahead(self):
        self.assertEqual(self.got(2, two(override="off"))["aws-spot-1"][0], "start")
        self.assertEqual(self.got(2, two(hours_today=12))["aws-spot-1"][0], "start")

    def cycles(self, ec2, n=1, tracked=None, waiting=None):
        tracked = {} if tracked is None else tracked
        labels = {"aws-spot-2": SPOT_LABELS, "aws-spot-1": AWS_LABELS}
        observed = (STEADY, HOME_BUSY, [UNITY_JOB] * 2 if waiting is None else waiting)
        with mock.patch.object(governor, "observe", return_value=observed), \
                mock.patch.object(governor, "power_provider", return_value=(ec2, "")), \
                mock.patch.object(governor, "host_labels", side_effect=lambda cfg, h: labels[h]), \
                mock.patch.object(governor, "apply"), mock.patch("builtins.print") as out:
            for _ in range(n):
                governor.cycle(TWO, {}, tracked)
        return tracked, [c.args[0] for c in out.call_args_list]

    def starts(self, ec2):
        return [iid for op, iid in ec2.calls if op == "start"]

    def test_no_spot_capacity_falls_back_to_on_demand(self):
        ec2 = FakeEc2("stopped", fail={"i-0spot": "InsufficientInstanceCapacity"})
        tracked, log = self.cycles(ec2)
        self.assertEqual(self.starts(ec2), ["i-0spot"])  # the next host waited for it
        self.assertEqual(tracked["aws-spot-2"]["fallback_why"], "capacity")
        self.assertIn("aws-spot-2: start capacity (InsufficientInstanceCapacity), passed over for the next standby host", log)
        self.cycles(ec2, tracked=tracked)
        self.assertEqual(self.starts(ec2), ["i-0spot", "i-0abc"])
        m = governor.State.metrics
        self.assertIn('ci_governor_fallback_total{host="aws-spot-2",reason="capacity"} 1', m)
        self.assertIn('ci_governor_fallback_total{host="aws-spot-1",reason="reclaim"} 0', m)
        self.assertIn(f'ci_governor_power_fallback_until{{host="aws-spot-2"}} '
                      f'{int(tracked["aws-spot-2"]["fallback_until"])}', m)

    def test_a_budget_deny_starts_nothing(self):
        # Negative control: the deny policy covers every host, so it is no fallback.
        ec2 = FakeEc2("stopped", fail={"i-0spot": "UnauthorizedOperation"})
        tracked, log = self.cycles(ec2, n=3)
        self.assertEqual(self.starts(ec2), ["i-0spot"] * 3)
        self.assertNotIn("fallback_until", tracked["aws-spot-2"])
        self.assertIn('ci_governor_fallback_total{host="aws-spot-2",reason="capacity"} 0', governor.State.metrics)
        # hc-614: three denied tries log the decision and the failure once, not every cycle.
        spot = [line for line in log if "i-0spot" in line]
        self.assertEqual(len(spot), 2)
        self.assertTrue(spot[0].startswith("start i-0spot on aws-spot-2: STANDBY"))
        self.assertEqual(spot[1], "start i-0spot failed: UnauthorizedOperation: no; quiet while it holds")
        # The host stops asking (no queue), then asks again: a new deny is logged again.
        tracked, _ = self.cycles(ec2, tracked=tracked, waiting=[])
        tracked, log = self.cycles(ec2, tracked=tracked)
        self.assertEqual(len([line for line in log if "i-0spot" in line]), 2)
        ec2.fail = {}
        _, log = self.cycles(ec2, tracked=tracked)
        self.assertIn("start i-0spot on aws-spot-2: the deny lifted; queue 2", log)
        self.assertNotIn("denied", tracked["aws-spot-2"])

    def test_an_ec2_error_drops_the_encoded_detail(self):
        e = governor.Ec2Error("UnauthorizedOperation", "You are not authorized to perform this operation. "
                              "User: x with an explicit deny in an identity-based policy: arn:aws:iam::1:policy/"
                              "ci-burst-deny-start. Encoded authorization failure message: AbC123" + "x" * 1000)
        self.assertTrue(str(e).endswith("policy/ci-burst-deny-start."))
        self.assertEqual(str(governor.Ec2Error("InsufficientInstanceCapacity", "none left")),
                         "InsufficientInstanceCapacity: none left")

    def test_a_refused_spot_start_falls_back_too(self):
        # A Spot-only refusal (price, request state, vCPU limit) must not starve the next host.
        ec2 = FakeEc2("stopped", fail={"i-0spot": "SpotMaxPriceTooLow"})
        tracked, _ = self.cycles(ec2, n=2)
        self.assertEqual(self.starts(ec2), ["i-0spot", "i-0abc"])
        self.assertEqual(tracked["aws-spot-2"]["fallbacks"], {"refused": 1})

    def test_a_start_that_aws_stops_again_falls_back(self):
        # A Spot start can succeed, then stop with no capacity after all.
        tracked, _ = self.cycles(FakeEc2({"i-0spot": "pending", "i-0abc": "stopped"}), waiting=[])
        ec2 = FakeEc2("stopped", reason={"i-0spot": "Server.InsufficientInstanceCapacity"})
        tracked, _ = self.cycles(ec2, tracked=tracked)
        self.assertEqual(self.starts(ec2), ["i-0abc"])
        self.assertEqual(tracked["aws-spot-2"]["fallback_why"], "reclaim")

    def test_a_spot_aws_restarted_in_its_pass_over_is_ahead(self):
        # A persistent Spot instance comes back by itself when capacity returns.
        got = self.got(6, two("running", fallback_until=20_000, fallback_why="reclaim"))
        self.assertEqual(got["aws-spot-1"][0], "hold")

    def test_a_retried_start_counts_one_pass_over(self):
        t, p = {}, {"fallback_seconds": 3600}
        governor.fall_back(t, p, "capacity", 0)
        governor.fall_back(t, p, "capacity", 60)
        self.assertEqual((t["fallbacks"], t["fallback_until"]), ({"capacity": 1}, 3660))
        governor.fall_back(t, p, "capacity", 3660)
        self.assertEqual(t["fallbacks"], {"capacity": 2})

    def test_a_network_error_is_no_fallback(self):
        ec2 = FakeEc2("stopped", fail={"i-0spot": True})
        tracked, _ = self.cycles(ec2, n=2)
        self.assertEqual(self.starts(ec2), ["i-0spot"] * 2)

    def test_a_reclaim_falls_back_in_the_same_cycle(self):
        tracked, _ = self.cycles(FakeEc2({"i-0spot": "running", "i-0abc": "stopped"}), waiting=[])
        ec2 = FakeEc2("stopped", reason={"i-0spot": "Server.SpotInstanceShutdown"})
        tracked, log = self.cycles(ec2, tracked=tracked)
        self.assertEqual(self.starts(ec2), ["i-0abc"])
        self.assertEqual(tracked["aws-spot-2"]["fallback_why"], "reclaim")
        m = governor.State.metrics
        self.assertIn('ci_governor_power_state{host="aws-spot-2",state="stopped",reason="reclaim"} 1', m)
        self.assertIn('ci_governor_fallback_total{host="aws-spot-2",reason="reclaim"} 1', m)
        # Still stopped by the reclaim next cycle: counted once.
        self.cycles(ec2, tracked=tracked)
        self.assertEqual(tracked["aws-spot-2"]["fallbacks"], {"reclaim": 1})

    def test_an_old_reclaim_on_first_sight_is_not_counted(self):
        ec2 = FakeEc2("stopped", reason={"i-0spot": "Server.SpotInstanceShutdown"})
        tracked, _ = self.cycles(ec2)
        self.assertEqual(self.starts(ec2), ["i-0spot"])
        self.assertNotIn("fallback_until", tracked["aws-spot-2"])

    def test_ec2_error_code_from_the_body(self):
        body = (b'<?xml version="1.0" encoding="UTF-8"?><Response><Errors><Error>'
                b'<Code>InsufficientInstanceCapacity</Code><Message>none in us-east-2a</Message>'
                b'</Error></Errors><RequestID>r</RequestID></Response>')
        err = urllib.error.HTTPError("https://ec2", 500, "x", {}, io.BytesIO(body))
        with mock.patch.object(governor.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(governor.Ec2Error) as e:
                governor.Ec2("us-east-2", "k", "s").start("i-0spot")
        self.assertEqual(e.exception.code, "InsufficientInstanceCapacity")
        self.assertIsInstance(e.exception, governor.POWER_ERRORS)

    def test_describe_reads_the_state_reason(self):
        xml = (b'<DescribeInstancesResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"><reservationSet>'
               b'<item><instancesSet><item><instanceState><code>80</code><name>stopped</name></instanceState>'
               b'<stateReason><code>Server.SpotInstanceShutdown</code><message>m</message></stateReason>'
               b'</item></instancesSet></item></reservationSet></DescribeInstancesResponse>')
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = xml
        with mock.patch.object(governor.urllib.request, "urlopen", return_value=resp):
            self.assertEqual(governor.Ec2("us-east-2", "k", "s").state("i-0spot"),
                             ("stopped", 0.0, "Server.SpotInstanceShutdown"))

# Packing (pack_plan): two running burst hosts, aws-spot-1 the less busy and started later.
DWELL = LIVE["dwell_seconds"]
WIDE_JOB = frozenset({"self-hosted", "unity", "unity-wide"})
WIDE_LABELS = AWS_LABELS | {"unity-wide", "host-aws-spot-1-wide"}
NO_KEEP = {**TWO, "pack_keep_labels": []}
THREE = {**TWO, "standby": {**TWO["standby"], "aws-spot-3": {"max_admitted": 6, "power": {**POWER, "instance_id": "i-0b3"}}}}


def burst(host, busy=0, idle=0, off=0):
    tag = host[-1]
    return pool(*[(f"b{tag}-busy-{i}", host, True, 100 - i, False) for i in range(busy)],
                *[(f"b{tag}-idle-{i}", host, False, 0, False) for i in range(idle)],
                *[(f"b{tag}-off-{i}", host, False, 0, True) for i in range(off)])


HALF = {**HOME_BUSY, **burst("aws-spot-2", busy=3, off=3), **burst("aws-spot-1", busy=1, off=4)}
DESK_WIDE = {"deskslim-2-w": {"host": "deskslim-2", "busy": True, "elapsed": 10, "withheld": False,
                          "labels": frozenset({"self-hosted", "unity", "unity-wide"})}}


class PackTest(unittest.TestCase):
    def setUp(self):
        self.inst = two("running", "running", launched=1_000)
        self.inst["aws-spot-1"]["launched"] = 2_000

    def step(self, now, waiting=(), runners=HALF, cfg=TWO):
        for host, inst in self.inst.items():  # as power_readings() keeps it
            if any(r["busy"] for r in runners.values() if r["host"] == host):
                inst["busy_at"] = now
        return governor.decide(cfg, STEADY, runners, list(waiting), {}, now, self.inst, {})

    def moves(self, got, host):
        return [(a, r) for h, a, r, _, _ in got if h == host and a != "hold"]

    def test_an_empty_queue_packs_the_least_busy_burst_host(self):
        got = self.step(10_000)
        self.assertEqual(self.moves(got, "aws-spot-1"), [("pack", ""), ("withhold", "b1-busy-0")])
        self.assertIn(("aws-spot-1", "withhold", "b1-busy-0", "PACKED", "packed"), got)
        self.assertEqual(self.moves(got, "aws-spot-2"), [])
        # Packed, no slot of it comes back while the other host has room for what waits.
        got = self.step(10_060, [UNITY_JOB])
        self.assertEqual(self.moves(got, "aws-spot-1"), [("withhold", "b1-busy-0")])
        self.assertEqual(self.moves(got, "aws-spot-2"), [("admit", "b2-off-0")])

    def test_a_tie_packs_the_one_started_last(self):
        runners = {**HOME_BUSY, **burst("aws-spot-2", busy=1, off=5), **burst("aws-spot-1", busy=1, off=4)}
        self.inst["aws-spot-2"]["launched"] = 3_000
        self.assertEqual(self.moves(self.step(10_000, runners=runners), "aws-spot-2")[0], ("pack", ""))

    def test_never_the_last_running_burst_host(self):
        self.inst["aws-spot-2"]["state"] = "stopped"
        self.assertEqual(self.moves(self.step(10_000), "aws-spot-1"), [])

    def three(self, wide=()):
        self.inst["aws-spot-3"] = {"state": "running", "busy_at": 10_000, "launched": 1_500,
                                    "labels": AWS_LABELS - {"host-aws-spot-1"} | {"host-aws-spot-3"}}
        for h in wide:
            self.inst[h]["labels"] = self.inst[h]["labels"] | {"unity-wide"}
        return {**HALF, **burst("aws-spot-3", idle=1, off=5)}

    def test_an_empty_queue_packs_every_burst_host_but_the_busiest_at_once(self):
        got = self.step(10_000, runners=self.three(), cfg=THREE)
        self.assertEqual({h for h, a, *_ in got if a == "pack"}, {"aws-spot-1", "aws-spot-3"})
        self.assertFalse(self.inst["aws-spot-2"].get("packed"))

    def test_packed_hosts_are_no_room_and_unpack_one_counted_at_a_time(self):
        runners = self.three()
        self.step(10_000, runners=runners, cfg=THREE)
        # 6 jobs; aws-spot-2 has 3 free. The busier packed host comes back first, and with its
        # 4 slots counted the other stays packed.
        got = self.step(10_060, [UNITY_JOB] * 6, runners=runners, cfg=THREE)
        self.assertEqual([h for h, a, *_ in got if a == "unpack"], ["aws-spot-1"])
        self.assertTrue(self.inst["aws-spot-3"]["packed"])

    def test_the_last_wide_host_of_a_batch_is_kept(self):
        # aws-spot-3 and aws-spot-1 carry unity-wide, nothing else does: packing the first
        # makes the second the only one, so it stays and the busiest host packs instead.
        got = self.step(10_000, runners=self.three(wide=("aws-spot-1", "aws-spot-3")), cfg=THREE)
        self.assertEqual({h for h, a, *_ in got if a == "pack"}, {"aws-spot-3", "aws-spot-2"})
        self.assertFalse(self.inst["aws-spot-1"].get("packed"))

    def test_a_job_it_can_take_blocks_the_pack_and_one_it_cannot_does_not(self):
        self.assertEqual([a for _, a, *_ in self.step(10_000, [UNITY_JOB]) if a == "pack"], [])
        self.setUp()
        self.assertEqual(self.moves(self.step(10_000, [LAN_JOB]), "aws-spot-1")[0], ("pack", ""))

    def test_a_one_cycle_gap_in_the_queue_packs_nothing(self):
        # Between bursts the queue reads 0 for a minute: no pack until it has been quiet a dwell.
        runners = self.three()
        self.step(10_000, [UNITY_JOB], runners=runners, cfg=THREE)
        for t in range(10_060, 10_000 + DWELL, 60):
            got = self.step(t, runners=runners, cfg=THREE)
            self.assertEqual([a for _, a, *_ in got if a == "pack"], [], t)
        got = self.step(10_000 + DWELL, runners=runners, cfg=THREE)
        self.assertEqual({h for h, a, *_ in got if a == "pack"}, {"aws-spot-1", "aws-spot-3"})

    def test_negative_control_a_queued_wide_job_keeps_its_only_wide_host(self):
        self.inst["aws-spot-1"]["labels"] = WIDE_LABELS
        # The queue alone protects it (no keep labels): while the wide job waits aws-spot-1
        # is never packed and never withheld (it admits a slot for the job instead).
        for t in range(10_000, 10_000 + 3 * DWELL, 60):
            got = self.step(t, [WIDE_JOB], cfg=NO_KEEP)
            self.assertEqual([a for a, _ in self.moves(got, "aws-spot-1") if a != "admit"], [], t)
        # Control: with nothing waiting the same host is packed.
        self.setUp()
        self.inst["aws-spot-1"]["labels"] = WIDE_LABELS
        self.assertEqual(self.moves(self.step(10_000, cfg=NO_KEEP), "aws-spot-1")[0], ("pack", ""))

    def test_the_only_wide_host_is_kept_for_a_wide_job_to_come(self):
        self.inst["aws-spot-1"]["labels"] = WIDE_LABELS
        got = self.step(10_000)
        self.assertEqual(self.moves(got, "aws-spot-1"), [])
        self.assertEqual(self.moves(got, "aws-spot-2")[0], ("pack", ""))
        # With deskslim-2's wide slot online it is not the only one, so it packs.
        self.setUp()
        self.inst["aws-spot-1"]["labels"] = WIDE_LABELS
        self.assertEqual(self.moves(self.step(10_000, runners={**HALF, **DESK_WIDE}), "aws-spot-1")[0],
                         ("pack", ""))

    def test_a_waiting_job_only_it_can_take_unpacks_it_at_once(self):
        self.inst["aws-spot-1"]["labels"] = WIDE_LABELS
        self.step(10_000, cfg=NO_KEEP)
        got = self.step(10_060, [WIDE_JOB], cfg=NO_KEEP)
        self.assertIn(("aws-spot-1", "unpack", "", "STANDBY", "1 waiting jobs no other host can take"), got)
        self.assertFalse(self.inst["aws-spot-1"]["packed"])

    def test_more_jobs_than_the_other_hosts_have_room_for_unpack_it_at_once(self):
        self.step(10_000)
        got = self.step(10_060, [UNITY_JOB] * 8)  # aws-spot-2 has 3 slots to admit
        self.assertIn(("aws-spot-1", "unpack", "", "STANDBY", "8 jobs it can take wait, the other hosts have 3 free slots"), got)
        self.assertEqual(self.moves(got, "aws-spot-1")[1], ("admit", "b1-off-0"))

    def test_a_flapping_queue_inside_the_dwell_does_not_flip(self):
        self.step(10_000)
        for i, t in enumerate(range(10_060, 10_000 + DWELL, 60)):
            got = self.step(t, [UNITY_JOB] if i % 2 else [])
            self.assertEqual([a for _, a, *_ in got if a in ("pack", "unpack")], [], t)
        self.assertTrue(self.inst["aws-spot-1"]["packed"])
        # Past the dwell a job it could take unpacks it, and the flapping queue cannot pack it
        # (or another host) again until the dwell has passed once more.
        got = self.step(10_000 + DWELL, [UNITY_JOB])
        self.assertIn(("aws-spot-1", "unpack", "", "STANDBY", "1 jobs it can take wait"), got)
        for i, t in enumerate(range(10_000 + DWELL + 60, 10_000 + 2 * DWELL, 60)):
            got = self.step(t, [] if i % 2 == 0 else [LAN_JOB])
            self.assertEqual([a for _, a, *_ in got if a in ("pack", "unpack")], [], t)
        self.assertEqual(self.moves(self.step(10_000 + 2 * DWELL), "aws-spot-1")[0], ("pack", ""))

    def test_a_packed_withhold_frees_its_slot_in_the_pool_cap(self):
        # The pool is at its cap (2 home + 3 + 1 admitted). aws-spot-2 comes first in the
        # order, so without the packed host's withhold counted first its admit is refused.
        cfg = {**TWO, "pool_max_admitted": 6}
        self.step(10_000, cfg=cfg)
        got = self.step(10_060, [UNITY_JOB], cfg=cfg)
        self.assertEqual(self.moves(got, "aws-spot-2"), [("admit", "b2-off-0")])
        self.assertEqual(self.moves(got, "aws-spot-1"), [("withhold", "b1-busy-0")])

    def test_a_packed_entry_without_pack_at_does_not_crash_the_cycle(self):
        self.inst["aws-spot-1"]["packed"] = True
        got = self.step(10_000, [UNITY_JOB])
        self.assertIn(("aws-spot-1", "unpack", "", "STANDBY", "1 jobs it can take wait"), got)

    def test_the_other_host_draining_does_not_unpack_it(self):
        self.step(10_000)
        self.inst["aws-spot-2"]["draining"] = True
        got = self.step(10_060)
        self.assertEqual([a for _, a, *_ in got if a in ("pack", "unpack")], [])
        self.assertTrue(self.inst["aws-spot-1"]["packed"])

    def test_a_packed_host_is_no_capacity_for_the_hosts_behind_it(self):
        # aws-spot-1 is packed with every slot withheld and one job it could take waits that a
        # home slot covers; four wide jobs only aws-spot-3 takes. Counted as 6 coming slots,
        # aws-spot-1 would keep aws-spot-3 from wanting a start.
        cfg = {**TWO, "standby": {**TWO["standby"], "aws-spot-3": {"max_admitted": 6, "power": {**POWER, "instance_id": "i-0b3"}}}}
        self.inst["aws-spot-3"] = {"state": "stopped", "busy_at": 10_000,
                                    "labels": AWS_LABELS - {"host-aws-spot-1"} | {"host-aws-spot-3", "unity-wide"}}
        self.inst["aws-spot-1"].update(packed=True, pack_at=10_000)
        runners = {**HOME_BUSY, **pool(("deskslim-2-3", "deskslim-2", False, 0, False)),
                   **burst("aws-spot-2", busy=6), **burst("aws-spot-1", off=6)}
        self.step(10_060, [UNITY_JOB] + [WIDE_JOB] * 4, runners=runners, cfg=cfg)
        self.assertTrue(self.inst["aws-spot-1"]["packed"])
        self.assertEqual(self.inst["aws-spot-3"]["want"], 4)

    def test_a_pack_and_stop_cycle_shows_in_the_log(self):
        ec2 = FakeEc2("running", launched=1_000)
        labels = {"aws-spot-2": SPOT_LABELS, "aws-spot-1": AWS_LABELS}
        done = {**HOME_BUSY, **burst("aws-spot-2", busy=3, off=3), **burst("aws-spot-1", off=5)}
        tracked, log = {}, []
        for now, runners in ((10_000, HALF), (10_060, done), (11_260, done)):
            with mock.patch.object(governor, "observe", return_value=(STEADY, runners, [])), \
                    mock.patch.object(governor, "power_provider", return_value=(ec2, "")), \
                    mock.patch.object(governor, "host_labels", side_effect=lambda cfg, h: labels[h]), \
                    mock.patch.object(governor, "restored", return_value=None), \
                    mock.patch.object(governor.time, "time", return_value=now), \
                    mock.patch.object(governor, "apply"), mock.patch("builtins.print") as out:
                governor.cycle(TWO, {}, tracked)
            log += [c.args[0] for c in out.call_args_list]
        self.assertEqual([line for line in log if "aws-spot-1" in line], [
            f"pack aws-spot-1: no job it can take waited for {DWELL}s, 1 busy slots, 1 burst hosts left unpacked; "
            "its slots go and its idle rule stops it; queue 0",
            "withhold b1-busy-0 on aws-spot-1: PACKED, packed; queue 0",
            "stop i-0abc on aws-spot-1: PACKED, no slot busy for 1260s; queue 0"])
        self.assertIn(("stop", "i-0abc"), ec2.calls)
        self.assertIn('ci_governor_decision{host="aws-spot-1",verdict="PACKED",action="stop"} 1',
                      governor.State.metrics)


class DwellTest(unittest.TestCase):
    """The standby withhold that flapped: an idle slot goes back only once nothing has waited
    for dwell_seconds."""

    def test_an_idle_slot_waits_out_the_dwell(self):
        inst = instance("running", waited_at=10_000)
        runners = {**pool(("aws-idle-0", "aws-spot-1", False, 0, False)), **HOME_BUSY}
        for t, want in ((10_060, "hold"), (10_000 + DWELL - 1, "hold"), (10_000 + DWELL, "withhold")):
            got = governor.decide(AWS, STEADY, runners, [], {}, t, inst, {})
            self.assertEqual([a for h, a, *_ in got if h == "aws-spot-1"], [want], t)

    def test_a_restart_starts_a_full_dwell(self):
        # First seen (a new pod): no waited_at yet, so the dwell starts now.
        inst = instance("running")
        runners = {**pool(("aws-idle-0", "aws-spot-1", False, 0, False)), **HOME_BUSY}
        for t, want in ((10_000, "hold"), (10_000 + DWELL - 60, "hold"), (10_000 + DWELL, "withhold")):
            got = governor.decide(AWS, STEADY, runners, [], {}, t, inst, {})
            self.assertEqual([a for h, a, *_ in got if h == "aws-spot-1"], [want], t)

    def test_a_job_it_cannot_take_does_not_restart_the_dwell(self):
        # A lan job waits a while, then goes: it was never one this host could take.
        inst = instance("running", waited_at=10_000 - DWELL)
        runners = {**pool(("aws-idle-0", "aws-spot-1", False, 0, False)), **HOME_BUSY}
        governor.decide(AWS, STEADY, runners, [LAN_JOB], {}, 10_000, inst, {})
        got = governor.decide(AWS, STEADY, runners, [], {}, 10_060, inst, {})
        self.assertEqual([a for h, a, *_ in got if h == "aws-spot-1"], ["withhold"])
        # Control: a job it could take does restart it.
        governor.decide(AWS, STEADY, runners, [UNITY_JOB], {}, 10_120, inst, {})
        got = governor.decide(AWS, STEADY, runners, [], {}, 10_180, inst, {})
        self.assertEqual([a for h, a, *_ in got if h == "aws-spot-1"], ["hold"])

    def test_a_flapping_queue_withholds_nothing(self):
        inst = instance("running")
        runners = {**pool(("aws-idle-0", "aws-spot-1", False, 0, False)), **HOME_BUSY}
        # A job every 5 minutes, under the 20-minute idle-off, so only the dwell can withhold.
        for i, t in enumerate(range(10_000, 10_000 + 2 * DWELL, 60)):
            waiting = [UNITY_JOB] if i % 5 == 0 else []
            got = governor.decide(AWS, STEADY, runners, waiting, {}, t, inst, {})
            self.assertNotIn("withhold", [a for h, a, *_ in got if h == "aws-spot-1"], t)


class RatesTest(unittest.TestCase):
    """config.json "rates": every host's $/h with its basis, in one place."""

    def test_every_rate_is_exported_with_its_basis(self):
        out = governor.render(LIVE, {"disk": {}}, {}, 0, [], True)
        for host, r in LIVE["rates"].items():
            self.assertIn(f'ci_governor_rate_usd_per_hour{{host="{host}",basis="{r["basis"]}"}} {r["usd_per_hour"]}', out)

    def test_a_rate_in_a_power_block_or_without_a_basis_is_refused(self):
        bad_power = {**LIVE, "standby": {"aws-spot-1": {"max_admitted": 5, "power": {**POWER, "usd_per_hour": 1}}}}
        with self.assertRaisesRegex(ValueError, "belongs in rates"):
            governor.check_rates(bad_power)
        with self.assertRaisesRegex(ValueError, "basis"):
            governor.check_rates({**LIVE, "rates": {"x": {"usd_per_hour": 1}}})


# ms-a2 as the topology renders it: test slots 1-4, build slot 5 on test slots 3 and 4's cores.
LAN_BASE = frozenset({"self-hosted", "linux", "x64", "host-ms-a2", "lan", "hd-library"})


def lan_slots(spec):
    """spec: one character per ms-a2 slot 1-5, as paired_slots() reads it."""
    out = {}
    for s, c in enumerate(spec, 1):
        if c == "-":
            continue
        held = c in ".h"
        own = ({"unity-build-withheld"} if held else {"unity-build"}) if s == 5 else \
            ({"unity-withheld"} if held else {"unity", "unity-full", "light"})
        out[f"ms-a2-unity-pool-{s}"] = {"host": "ms-a2", "busy": c in "hb", "elapsed": 100 - s,
                                        "withheld": held, "labels": LAN_BASE | own}
    return out


class LanPairTest(unittest.TestCase):
    BUILD = {"aws-build-1": {"state": "stopped", "busy_at": 0, "labels": PAIRED_LABELS}}

    def run_lan(self, spec, waiting, lan=COOL, last=None, pins=None, build=None):
        inst = copy.deepcopy(self.BUILD if build is None else build)
        got = governor.decide(PAID, readings(**{"ms-a2": lan}), lan_slots(spec), waiting, last or {}, 10_000, inst,
                              pins or {})
        return sorted((a, int(r.rsplit("-", 1)[1])) for h, a, r, _, _ in got if h == "ms-a2" and a != "hold"), inst

    def test_the_topology_gives_ms_a2_its_pair(self):
        self.assertEqual(governor.pairs(LIVE, "ms-a2"), {5: [3, 4]})
        self.assertEqual(governor.admit_labels(LIVE, "ms-a2", "ms-a2-unity-pool-5"), ["unity-build"])
        self.assertEqual(governor.withheld_label(LIVE, "ms-a2", "ms-a2-unity-pool-5"), "unity-build-withheld")
        self.assertEqual(governor.admit_labels(LIVE, "ms-a2", "ms-a2-unity-pool-3"), ["unity", "unity-full"])
        self.assertEqual(governor.withheld_label(LIVE, "ms-a2", "ms-a2-unity-pool-3"), "unity-withheld")
        self.assertIn("unity-build", governor.pools(LIVE))

    def test_a_waiting_build_takes_the_lan_pair_and_no_paid_start(self):
        # Pair 5's test slots step aside in one cycle; aws-build-1 is not wanted for the build.
        got, inst = self.run_lan("iiii.", [BUILD_JOB])
        self.assertEqual(got, [("withhold", 3), ("withhold", 4)])
        self.assertEqual(inst["aws-build-1"]["want"], 0)
        # Once both read withheld and idle, the build slot is admitted, past any cooldown.
        got, _ = self.run_lan("ii...", [BUILD_JOB], last={"ms-a2": 10_000})
        self.assertEqual(got, [("admit", 5)])

    def test_a_release_build_is_not_the_lan_slots(self):
        got, inst = self.run_lan("iiii.", [BUILD_JOB | {"unity-release"}])
        self.assertEqual(got, [])
        self.assertEqual(inst["aws-build-1"]["want"], 1)

    def test_a_hot_host_claims_no_build(self):
        got, inst = self.run_lan("iiii.", [BUILD_JOB], lan=HOT)
        self.assertNotIn(("withhold", 3), got)
        self.assertEqual(inst["aws-build-1"]["want"], 1)

    def test_a_hot_host_claims_the_build_no_paid_host_can_take(self):
        # 2026-11-19: aws-build-1 off by override, ms-a2 HOT for 2h20m with the Steam cut queued.
        # The load rule still withholds slot 1 for HOT; the pair's moves are what changed.
        pair = lambda got: [m for m in got if m[1] in (3, 4, 5)]
        off = {"aws-build-1": {**self.BUILD["aws-build-1"], "override": "off"}}
        self.assertEqual(pair(self.run_lan("iiii.", [BUILD_JOB], lan=HOT, build=off)[0]), [("withhold", 3), ("withhold", 4)])
        self.assertEqual(pair(self.run_lan("ii...", [BUILD_JOB], lan=HOT, build=off)[0]), [("admit", 5)])
        # Its state unread (the instance is gone): no paid host either.
        self.assertEqual(pair(self.run_lan("iiii.", [BUILD_JOB], lan=HOT, build={})[0]), [("withhold", 3), ("withhold", 4)])
        # One whose runners lack a label the build asks for cannot take it.
        short = {"aws-build-1": {**self.BUILD["aws-build-1"], "labels": PAIRED_LABELS - {"unity-build"}}}
        self.assertEqual(pair(self.run_lan("iiii.", [BUILD_JOB], lan=HOT, build=short)[0]), [("withhold", 3), ("withhold", 4)])
        # Passed over after a reclaim, at its hours cap, or draining up under override off:
        # none of them starts for it, so none is open.
        for paid in ({"fallback_until": 20_000, "fallback_why": "reclaim"}, {"hours_today": 24.0}, {"state": "running", "override": "off"}):
            closed = {"aws-build-1": {**self.BUILD["aws-build-1"], **paid}}
            self.assertEqual(pair(self.run_lan("iiii.", [BUILD_JOB], lan=HOT, build=closed)[0]),
                             [("withhold", 3), ("withhold", 4)], paid)
        # Up, or held on past its cap, it is open: a HOT ms-a2 leaves the build to it.
        for paid in ({"state": "running"}, {"hours_today": 24.0, "override": "on"}):
            open_ = {"aws-build-1": {**self.BUILD["aws-build-1"], **paid}}
            self.assertEqual(pair(self.run_lan("iiii.", [BUILD_JOB], lan=HOT, build=open_)[0]), [], paid)

    def test_a_full_or_pinned_host_claims_no_build_even_with_no_paid_host(self):
        off = {"aws-build-1": {**self.BUILD["aws-build-1"], "override": "off"}}
        self.assertNotIn(("withhold", 3), self.run_lan("iiii.", [BUILD_JOB], lan=FULL, build=off)[0])
        self.assertNotIn(("withhold", 3), self.run_lan("iiii.", [BUILD_JOB], pins={"ms-a2": 0}, build=off)[0])

    def test_no_build_waits_the_build_slot_steps_aside_and_its_tests_come_back(self):
        # As the play registers them, every slot admitted: the build slot steps aside.
        self.assertEqual(self.run_lan("iiiii", [UNITY_JOB])[0], [("withhold", 5)])
        # The test slots of a quiet pair are the pool's again.
        self.assertEqual(self.run_lan("ii...", [UNITY_JOB] * 3)[0], [("admit", 3)])

    def test_a_claim_on_busy_tests_leaves_the_build_to_the_paid_host_too(self):
        got, inst = self.run_lan("iiih.", [BUILD_JOB])
        self.assertEqual(got, [("withhold", 3)])
        self.assertEqual(inst["aws-build-1"]["want"], 1)

    def test_a_host_pinned_to_0_claims_no_build(self):
        got, inst = self.run_lan("iiii.", [BUILD_JOB], pins={"ms-a2": 0})
        self.assertNotIn(("withhold", 3), got)
        self.assertEqual(inst["aws-build-1"]["want"], 1)

    def test_a_lent_or_idle_build_slot_is_not_held_for_disk(self):
        def gauge(spec):
            hosts = readings(**{"ms-a2": {**COOL, "disk": 0.23}})
            return 'ci_governor_disk_held{host="ms-a2"} 1' in governor.render(LIVE, hosts, lan_slots(spec), 3, [], True)
        self.assertFalse(gauge("iiii."))
        self.assertFalse(gauge("ii..b"))
        self.assertTrue(gauge("i...."))

    def test_a_lan_build_slot_in_no_group_fails_the_config(self):
        with open(CONFIG) as f:
            conf = json.load(f)
        with open(os.environ["TOPOLOGY"]) as f:
            topo = json.load(f)
        for slot in topo["hosts"]["ms-a2"]["slots"]:
            slot.pop("group", None)
        with self.assertRaisesRegex(ValueError, "LAN hosts with a build slot in no slot group: ms-a2"):
            load_files(conf, topo)

    def test_a_busy_build_keeps_its_pair(self):
        self.assertEqual(self.run_lan("ii..b", [UNITY_JOB] * 3)[0], [])
        self.assertEqual(self.run_lan("iiiib", [UNITY_JOB] * 3)[0], [("withhold", 3), ("withhold", 4)])


class WideLeadTest(unittest.TestCase):
    """ms-a2 with a wide slot 6 on test slots 1 and 2's cores beside build slot 5 on 3 and 4's."""
    WIDE_JOB = frozenset({"self-hosted", "unity", "unity-wide"})

    def setUp(self):
        self.cfg = with_paid_build(lambda topo: topo["hosts"]["ms-a2"]["slots"].append(
            {"name": "ms-a2-unity-pool-6", "caps": ["unity-wide"], "group": ["ms-a2-unity-pool-1", "ms-a2-unity-pool-2"]}))

    def slots(self, spec):
        """spec: one character per slot 1-6: i idle admitted, b busy admitted, . idle withheld."""
        out = {}
        for s, c in enumerate(spec, 1):
            held = c == "."
            lead = {5: {"unity-build"}, 6: {"unity-wide", "unity", "unity-full"}}.get(s)
            if lead:
                own = {"unity-build-withheld"} if held else lead
            else:
                own = {"unity-withheld"} if held else {"unity", "unity-full", "light"}
            out[f"ms-a2-unity-pool-{s}"] = {"host": "ms-a2", "busy": c == "b", "elapsed": 100 - s,
                                            "withheld": held, "labels": LAN_BASE | own}
        return out

    def run_wide(self, spec, waiting):
        inst = {"aws-build-1": {"state": "stopped", "busy_at": 0, "labels": PAIRED_LABELS}}
        got = governor.decide(self.cfg, readings(**{"ms-a2": COOL}), self.slots(spec), waiting, {}, 10_000, inst, {})
        return sorted((a, int(r.rsplit("-", 1)[1])) for h, a, r, _, _ in got if h == "ms-a2" and a != "hold"), inst

    def test_the_wide_slot_is_a_lead_with_its_own_labels(self):
        self.assertEqual(governor.pairs(self.cfg, "ms-a2"), {5: [3, 4], 6: [1, 2]})
        self.assertEqual(governor.admit_labels(self.cfg, "ms-a2", "ms-a2-unity-pool-6"), ["unity-wide", "unity", "unity-full"])
        self.assertEqual(governor.admit_labels(self.cfg, "ms-a2", "ms-a2-unity-pool-5"), ["unity-build"])
        self.assertEqual(governor.withheld_label(self.cfg, "ms-a2", "ms-a2-unity-pool-6"), "unity-build-withheld")
        self.assertIn("unity-wide", governor.pools(self.cfg))
        self.assertFalse(governor.serves_pool(self.cfg, "ms-a2", "ms-a2-unity-pool-6"))

    def test_a_waiting_wide_job_steps_its_pair_aside_and_only_its_pair(self):
        got, inst = self.run_wide("iiii..", [self.WIDE_JOB])
        self.assertEqual(got, [("withhold", 1), ("withhold", 2)])
        self.assertEqual(inst["aws-build-1"]["want"], 0)

    def test_the_wide_slot_comes_in_once_its_pair_is_clear_and_not_before(self):
        self.assertEqual(self.run_wide("..ii..", [self.WIDE_JOB])[0], [("admit", 6)])
        self.assertNotIn(("admit", 6), self.run_wide(".bii..", [self.WIDE_JOB])[0])

    def test_no_wide_job_waits_the_wide_slot_steps_aside_and_its_tests_come_back(self):
        self.assertEqual(self.run_wide("iiii.i", [UNITY_JOB])[0], [("withhold", 6)])
        self.assertEqual(self.run_wide("..ii..", [UNITY_JOB] * 3)[0], [("admit", 1)])

    def test_a_busy_wide_slot_keeps_its_pair(self):
        self.assertEqual(self.run_wide("..ii.b", [UNITY_JOB] * 3)[0], [])

    def test_a_build_job_never_claims_the_wide_slot(self):
        got, _ = self.run_wide("iiii..", [BUILD_JOB])
        self.assertEqual(got, [("withhold", 3), ("withhold", 4)])

    def test_the_wide_job_is_not_the_pools(self):
        marks = governor.build_marks(self.cfg)
        self.assertEqual(governor.pool_jobs([self.WIDE_JOB, UNITY_JOB], marks), [UNITY_JOB])


# LENDING: one LAN host, box, with three Unity slots. Its Forgejo seat borrows slot 1 and a
# personal repo's runner borrows slot 2; slot 3 holds the pool floor.
ORG = f"orgs/{LIVE['org']}"
REPO = "Kblack0610/game"
FJ_SEAT, REPO_SEAT = "box-unity-pool-1-forgejo", "box-game-1"
S1, S2, S3 = (f"box-unity-pool-{n}" for n in (1, 2, 3))
LCFG = governor.with_defaults({**LIVE, "standby": {}, "hosts": {"box": {}}, "pool_min_admitted": 1,
                               "shared": {s: ["light"] for s in (S1, S2, S3)},
                               "seats": {FJ_SEAT: {"host": "box", "slot": S1, "forge": "forgejo", "repo": None,
                                                   "caps": ["forgejo"]},
                                         REPO_SEAT: {"host": "box", "slot": S2, "forge": "github", "repo": REPO,
                                                     "caps": ["light"]}},
                               "standing": {}})
ADMITTED = {"self-hosted", "host-box", "unity", "unity-full", "light"}
DRAINED = {"self-hosted", "host-box", "unity-withheld"}
GITHUB_JOB = (frozenset({"self-hosted", "unity"}),)
REPO_JOB = frozenset({"self-hosted", "light"})


class FakeGitHub:
    """The runners API over {scope: {name: {"labels": set, "busy": bool}}}, labels changed in place."""

    def __init__(self, scopes):
        self.scopes, self.calls = scopes, []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        scope, rest = path.split("/actions/runners")
        names = sorted(self.scopes[scope])
        if method == "GET":
            page = urllib.parse.parse_qs(urllib.parse.urlsplit(rest).query)["page"][0]
            mine = self.scopes[scope]
            return {"runners": [] if page != "1" else [
                {"id": i, "name": n, "busy": mine[n]["busy"], "labels": [{"name": l} for l in sorted(mine[n]["labels"])]}
                for i, n in enumerate(names)]}
        parts = rest.split("/")
        r = self.scopes[scope][names[int(parts[1])]]
        if method == "POST":
            r["labels"] |= set(body["labels"])
        else:
            r["labels"].discard(urllib.parse.unquote(parts[3]))
        return {}


class Box:
    """box's slots and seats as GitHub holds them, and what the exporter would read from that."""

    def __init__(self, s1=DRAINED, s2=ADMITTED, s1_busy=False, seat=frozenset({"self-hosted", "light-withheld"})):
        self.gh = FakeGitHub({ORG: {S1: {"labels": set(s1), "busy": s1_busy},
                                    S2: {"labels": set(s2), "busy": False},
                                    S3: {"labels": set(ADMITTED), "busy": False}},
                              f"repos/{REPO}": {REPO_SEAT: {"labels": set(seat), "busy": False}}})
        self.fj = {"busy": False, "online": True}
        self.leases, self.lends, self.last = governor.Leases({FJ_SEAT: S1}), {}, {}
        self.weights = None  # kind_weights(): none measured, so a lend serves one seat

    def runners(self):
        return {n: {"host": "box", "busy": r["busy"], "elapsed": 0, "labels": frozenset(r["labels"]),
                    "withheld": any(l.endswith("-withheld") for l in r["labels"])}
                for n, r in self.gh.scopes[ORG].items()}

    def seat_state(self):
        r = self.gh.scopes[f"repos/{REPO}"][REPO_SEAT]
        return {FJ_SEAT: {**self.fj, "withheld": False},
                REPO_SEAT: {"busy": r["busy"], "online": True, "withheld": "light-withheld" in r["labels"]}}

    def plan(self, waits, now=10_000, hosts=None, pins=None):
        return governor.lend_plan(LCFG, hosts or readings(box=COOL), self.runners(), self.seat_state(), waits,
                                  self.lends, self.leases, pins, self.last, now, self.weights)

    def step(self, waits, now=10_000, hosts=None):
        steps = self.plan(waits, now, hosts)
        with mock.patch.object(governor, "gh", self.gh), mock.patch("builtins.print"):
            governor.lend_apply(LCFG, steps, self.lends, self.leases, now, self.last)
        return [(s, slot or seat) for s, slot, seat, _ in steps]

    def labels(self, name=S1):
        return self.gh.scopes[ORG][name]["labels"]


class LendTest(unittest.TestCase):
    def test_both_forges_want_one_drained_slot_and_the_older_wait_wins(self):
        older_github = [("github", GITHUB_JOB[0], 300), ("forgejo", frozenset({"docker"}), 100)]
        self.assertEqual(Box().plan(older_github), [])
        box = Box()
        self.assertEqual(box.step([("github", GITHUB_JOB[0], 100), ("forgejo", frozenset({"docker"}), 300)]),
                         [("lend", S1)])
        self.assertEqual(box.labels(), DRAINED | {"unity-lent"})
        # The lend left box in its cooldown, so decide() does not admit another slot for the GitHub job.
        self.assertEqual(box.last, {"box": 10_000})

    def test_a_github_job_the_slot_cannot_take_is_no_rival(self):
        build = [("github", frozenset({"self-hosted", "unity-build"}), 900), ("forgejo", frozenset({"docker"}), 100)]
        self.assertEqual([s for s, *_ in Box().plan(build)], ["lend"])

    def test_a_forgejo_lend_runs_lend_grant_return_unlend_and_the_lease_follows_github(self):
        box, job = Box(), [("forgejo", frozenset({"docker"}), 60)]
        self.assertEqual(box.step(job), [("lend", S1)])
        self.assertEqual(box.leases.get(S1)[0], "github")
        # The exporter shows it withheld and lent: only now does the lease turn.
        self.assertEqual(box.step(job, 10_060), [("grant", S1)])
        self.assertEqual(box.leases.get(S1)[0], "forgejo")
        self.assertNotIn("unity", box.labels())
        self.assertEqual(box.leases.wait(FJ_SEAT, "forgejo", 0), "forgejo")
        box.fj["busy"] = True
        self.assertEqual(box.step([], 10_120), [])  # its job runs, so nothing is left to give back
        box.fj["busy"] = False
        self.assertEqual(box.step([], 10_180), [("return", S1)])
        self.assertEqual(box.leases.get(S1)[:2], ("github", None))
        # The mark stays until the supervisor, between jobs, says it holds github.
        self.assertEqual(box.step([], 10_240), [])
        self.assertIn("unity-lent", box.labels())
        self.assertEqual(box.leases.wait(FJ_SEAT, "forgejo", 0), "github")
        self.assertEqual(box.step([], 10_300), [])
        self.assertEqual(box.leases.wait(FJ_SEAT, "github", 0), "github")
        # box has room, so the slot comes back admitted, not withheld for decide()'s cooldown.
        self.assertEqual(box.step([], 10_360), [("readmit", S1)])
        self.assertEqual(box.labels(), ADMITTED)
        self.assertEqual(box.lends, {})
        self.assertEqual(box.last, {"box": 10_360})

    def test_a_busy_slot_is_lent_to_drain_and_never_granted_while_busy(self):
        job = [("forgejo", frozenset({"docker"}), 60)]
        # Every slot busy (the GitHub queue keeps them so): the lend drains one.
        box = Box(s1=ADMITTED, s1_busy=True)
        self.assertEqual(box.step(job), [("lend", S1)])
        self.assertEqual(box.labels(), DRAINED | {"unity-lent"})
        self.assertEqual(box.plan(job, 10_060), [])
        box.gh.scopes[ORG][S1]["busy"] = False
        self.assertEqual(box.step(job, 10_060), [("grant", S1)])
        self.assertEqual(box.leases.get(S1)[0], "forgejo")
        # An idle slot goes before a busy one.
        box = Box(s1=ADMITTED, s1_busy=True)
        box.leases = governor.Leases({FJ_SEAT: S1, "box-unity-pool-2-forgejo": S2})
        seat2 = "box-unity-pool-2-forgejo"
        state = {**box.seat_state(), seat2: {"busy": False, "online": True, "withheld": False}}
        with mock.patch.dict(LCFG["seats"], {seat2: {"host": "box", "slot": S2, "forge": "forgejo"}}), \
                mock.patch.object(box, "seat_state", return_value=state):
            self.assertEqual(box.plan(job)[0][:2], ("lend", S2))
        # GitHub says busy at grant time, though the exporter said idle: the live read wins.
        box = Box()
        box.step(job)
        box.gh.scopes[ORG][S1]["busy"] = True  # a GitHub job it took before the lend landed
        self.assertEqual(box.plan(job, 10_060), [])
        with mock.patch.object(box, "runners", return_value={**Box(s1=DRAINED | {"unity-lent"}).runners()}):
            box.step(job, 10_060)
        self.assertEqual(box.leases.get(S1)[0], "github")
        self.assertEqual(box.lends[S1]["phase"], "held")

    def test_an_admitted_slot_is_lent_only_above_the_pool_floor(self):
        job = [("forgejo", frozenset({"docker"}), 60)]
        self.assertEqual(Box(s1=ADMITTED).step(job), [("lend", S1)])
        box = Box(s1=ADMITTED, s2=DRAINED)
        box.gh.scopes[ORG][S3]["labels"] = set(DRAINED)
        self.assertEqual(box.plan(job), [])

    def test_a_hot_or_pinned_host_lends_nothing_and_gives_back_what_it_lent(self):
        job = [("forgejo", frozenset({"docker"}), 60)]
        self.assertEqual(Box().plan(job, hosts=readings(box=HOT)), [])
        self.assertEqual(Box().plan(job, pins={"box": 1}), [])
        # A cooling host still lends its idle withheld slot: that adds only load ROOM has room for.
        box = Box()
        box.last["box"] = 9_900
        self.assertEqual([s for s, *_ in box.plan(job)], ["lend"])
        box = Box()
        box.step(job)
        box.step(job, 10_060)
        self.assertEqual([s for s, *_ in box.plan(job, 10_120, readings(box=HOT))], ["return"])

    def test_an_older_github_job_takes_an_idle_lent_slot_back(self):
        box = Box()
        box.step([("forgejo", frozenset({"docker"}), 60)])
        box.step([("forgejo", frozenset({"docker"}), 120)], 10_060)
        rival = [("github", GITHUB_JOB[0], 500), ("forgejo", frozenset({"docker"}), 180)]
        self.assertEqual(box.step(rival, 10_120), [("return", S1)])
        self.assertEqual(box.leases.get(S1)[0], "github")

    def test_a_dead_supervisor_gives_its_slot_back(self):
        box, job = Box(), [("forgejo", frozenset({"docker"}), 60)]
        box.step(job)
        box.step(job, 10_060)
        self.assertEqual(box.plan(job, 10_060 + governor.LEASE_STALE_SECONDS - 1), [])
        self.assertEqual([s for s, *_ in box.plan(job, 10_060 + governor.LEASE_STALE_SECONDS)], ["return"])
        # Returned, it never acks: the slot is unlent once it is stale again, not kept lent.
        box.step(job, 10_060 + governor.LEASE_STALE_SECONDS)
        back = 10_060 + 2 * governor.LEASE_STALE_SECONDS
        self.assertEqual(box.plan([], back - 1), [])
        self.assertEqual(box.step([], back), [("readmit", S1)])
        self.assertEqual(box.labels(), ADMITTED)

    def test_a_lend_and_a_readmit_drop_a_cap_the_topology_took_away(self):
        # LCFG gives box's slots light alone, so heavy is a cap the topology no longer lists for them.
        box, job = Box(s1=ADMITTED | {"heavy"}, s1_busy=True), [("forgejo", frozenset({"docker"}), 60)]
        self.assertEqual(box.step(job), [("lend", S1)])
        self.assertEqual(box.labels(), DRAINED | {"unity-lent"})
        box.gh.scopes[ORG][S1]["busy"] = False
        box.labels().add("heavy")  # a lend whose drop failed half-way is not granted
        self.assertEqual(box.plan(job, 10_060), [])
        box.labels().discard("heavy")
        box.step(job, 10_060)
        box.step(job, 10_060 + governor.LEASE_STALE_SECONDS)
        box.labels().add("heavy")
        self.assertEqual(box.step([], 10_060 + 2 * governor.LEASE_STALE_SECONDS), [("readmit", S1)])
        self.assertEqual(box.labels(), ADMITTED)

    def test_a_slot_of_two_forgejo_seats_serves_two_jobs_on_one_lend_and_goes_back_when_both_are_done(self):
        # hc-626: two seats of weight 0.5 fill slot 1, so two waiting jobs take one lend, not
        # slot 1 and slot 3 (which they took when a lend counted as one seat).
        fj2, fj3 = f"{FJ_SEAT}-2", "box-unity-pool-3-forgejo"
        box, jobs = Box(), [("forgejo", frozenset({"docker"}), 60)] * 2
        box.weights = {"forgejo": 0.5}
        box.leases = governor.Leases({FJ_SEAT: S1, fj2: S1, fj3: S3})
        seats = {**LCFG["seats"], fj2: {**LCFG["seats"][FJ_SEAT]}, fj3: {**LCFG["seats"][FJ_SEAT], "slot": S3}}
        idle = {s: {"busy": False, "online": True, "withheld": False} for s in (fj2, fj3)}
        with mock.patch.dict(LCFG, {"seats": seats}), \
                mock.patch.object(box, "seat_state", side_effect=lambda: {**Box.seat_state(box), **idle}):
            self.assertEqual(box.step(jobs), [("lend", S1)])
            self.assertEqual(box.step(jobs, 10_060), [("grant", S1)])
            box.fj["busy"] = True  # one seat runs a job, its mate is idle and nothing waits
            self.assertEqual(box.step([], 10_120), [])
            box.fj["busy"] = False
            self.assertEqual(box.step([], 10_180), [("return", S1)])
            box.leases.wait(FJ_SEAT, "github", 0)  # one mate holds github, the other has not said
            self.assertEqual(box.step([], 10_240), [])
            box.leases.wait(fj2, "github", 0)
            self.assertEqual(box.step([], 10_300), [("readmit", S1)])
            # A mate that never asks for LEASE_STALE_SECONDS does not hold the slot forever.
            box.leases = governor.Leases({FJ_SEAT: S1, fj2: S1, fj3: S3})
            box.lends[S1] = {"seat": FJ_SEAT, "phase": "returning", "at": 10_000}
            box.gh.scopes[ORG][S1]["labels"] = DRAINED | {"unity-lent"}
            box.leases.wait(FJ_SEAT, "github", 0)
            self.assertEqual(box.plan([], 10_060), [])
            self.assertEqual(box.plan([], 10_000 + governor.LEASE_STALE_SECONDS)[0][0], "readmit")
            # A seat the topology names but the exporter does not see (no host registered it
            # yet) is no mate: it holds no return and adds no capacity.
            del idle[fj2]
            self.assertEqual(box.plan([], 10_060)[0][0], "readmit")
            box.lends.clear()
            self.assertEqual([s[:2] for s in box.plan(jobs, 10_060)], [("lend", S1), ("lend", S3)])

    def test_seats_pack_onto_a_lend_while_their_weights_sum_to_one(self):
        # hc-666: three registered seats; 0.3 fits three, 0.4 two, none measured (weight 1) one, and a kind
        # measured heavier than a slot still gets one.
        fj = [FJ_SEAT, f"{FJ_SEAT}-2", f"{FJ_SEAT}-3"]
        seats = {n: {**LCFG["seats"][FJ_SEAT]} for n in fj}
        with mock.patch.dict(LCFG, {"seats": seats}):
            got = [governor.pack(LCFG, w, S1, FJ_SEAT) for w in
                   (None, {}, {"forgejo": 0.3}, {"forgejo": 0.4}, {"forgejo": 0.5}, {"forgejo": 1.0})]
        self.assertEqual(got, [1, 1, 3, 2, 2, 1])

    def test_a_third_job_that_would_push_the_weights_past_one_takes_another_lend(self):
        fj = [FJ_SEAT, f"{FJ_SEAT}-2", f"{FJ_SEAT}-3"]
        fj3 = "box-unity-pool-3-forgejo"
        seats = {**LCFG["seats"], **{n: {**LCFG["seats"][FJ_SEAT]} for n in fj[1:]}, fj3: {**LCFG["seats"][FJ_SEAT], "slot": S3}}
        box, jobs = Box(), [("forgejo", frozenset({"docker"}), 60)] * 3
        box.weights = {"forgejo": 0.4}
        box.leases = governor.Leases({**{n: S1 for n in fj}, fj3: S3})
        idle = {n: {"busy": False, "online": True, "withheld": False} for n in (*fj[1:], fj3)}
        with mock.patch.dict(LCFG, {"seats": seats}), \
                mock.patch.object(box, "seat_state", side_effect=lambda: {**Box.seat_state(box), **idle}):
            self.assertEqual(box.step(jobs[:2]), [("lend", S1)])
            self.assertEqual([s[:2] for s in box.plan(jobs, 10_060)], [("grant", S1), ("lend", S3)])

    def test_a_seat_past_the_limit_is_handed_github_while_its_slot_is_lent(self):
        leases = governor.Leases({"a": S1, "b": S1, "c": S1})
        leases.set(S1, "forgejo")
        self.assertEqual([leases.wait(n, "github", 0) for n in "abc"], ["forgejo"] * 3)
        leases.limit({S1: 2})
        self.assertEqual([leases.of_seat(n)[0] for n in "abc"], ["forgejo", "forgejo", "github"])
        self.assertEqual(leases.wait("c", "forgejo", 0), "github")

    def test_weights_come_from_the_p90_share_of_the_smallest_slot_and_a_thin_kind_has_none(self):
        governor.WEIGHTS.update(at=None, by_kind={})
        cfg = {**LCFG, "seats": {FJ_SEAT: {**LCFG["seats"][FJ_SEAT], "cpus": 6, "memory_gb": 14},
                                 REPO_SEAT: {**LCFG["seats"][REPO_SEAT], "cpus": 6, "memory_gb": 14}}}
        rows = {"cc:job_kind_cores": [("forgejo", [1.8] * 60), ("repo", [6.0] * 59)],
                "cc:job_kind_mem": [("forgejo", [2 * 2 ** 30] * 60)]}

        def prom_range(q, start, end, step):  # repo's 59 samples are under min_samples: no weight
            rule = next(r for r in rows if r in q)
            return [{"metric": {"kind": k}, "values": [[t, str(v)] for t, v in enumerate(vs)]} for k, vs in rows[rule]]
        with mock.patch.object(governor, "prom_range", prom_range), mock.patch("builtins.print"):
            self.assertEqual(governor.kind_weights(cfg, 1000), {"forgejo": 0.3})  # cores 1.8/6 beats mem 2/14
            rows["cc:job_kind_cores"] = [("forgejo", [0.1] * 60)]
            rows["cc:job_kind_mem"] = []
            self.assertEqual(governor.kind_weights(cfg, 1100), {"forgejo": 0.3})  # held until the refresh is due
            self.assertEqual(governor.kind_weights(cfg, 1000 + 3600), {"forgejo": 0.1})  # floor
        governor.WEIGHTS.update(at=None, by_kind={})

    def test_one_busy_runner_does_not_set_its_kinds_weight(self):
        # hc-823: one runner ran light jobs at a whole slot's 6 cores for 70 steps, nine others at 1 core
        # for 630. The p90 job uses 1.5 cores (0.25 of the slot); the busiest runner's own p90 read 1.0.
        governor.WEIGHTS.update(at=None, by_kind={})
        cfg = {**LCFG, "seats": {FJ_SEAT: {**LCFG["seats"][FJ_SEAT], "cpus": 6, "memory_gb": 14}}}
        series = [{"metric": {"runner": "busy", "kind": "forgejo"}, "values": [[t, "6"] for t in range(70)]}]
        series += [{"metric": {"runner": f"r{n}", "kind": "forgejo"}, "values": [[t, "1"] for t in range(70)]}
                   for n in range(9)]
        with mock.patch.object(governor, "prom_range", lambda q, *a: series if "cores" in q else []), \
                mock.patch("builtins.print"):
            self.assertEqual(governor.kind_weights(cfg, 1000), {"forgejo": 0.25})
        governor.WEIGHTS.update(at=None, by_kind={})

    def test_every_kind_exports_its_weight_and_an_unmeasured_one_reads_one(self):
        kinds = set(LCFG["kinds"])
        self.assertTrue({"forgejo", "light", "unity"} <= kinds)
        text = governor.render(LCFG, {"disk": {}}, {}, 0, [], True, weights={"forgejo": 0.283, "light": 0.658})
        self.assertIn('ci_governor_kind_weight{kind="forgejo",measured="true"} 0.283', text)
        self.assertIn('ci_governor_kind_weight{kind="light",measured="true"} 0.658', text)
        # A kind under min_samples (heavy, repo) or a whole-slot one (unity) is a row at 1, never a missing one.
        self.assertIn('ci_governor_kind_weight{kind="unity",measured="false"} 1', text)
        self.assertEqual(text.count("ci_governor_kind_weight{"), len(kinds))
        # No weights read (lending off, or no refresh has answered yet): no series, so a reader shows "-", not a made-up 1.
        self.assertNotIn("ci_governor_kind_weight", governor.render(LCFG, {"disk": {}}, {}, 0, [], True))

    def test_units_weigh_seats_and_waits_and_price_the_backlog_in_run_time(self):
        box = Box(s1=DRAINED | {"unity-lent"})
        runners = box.runners()
        runners[S2]["busy"] = True
        box.fj["busy"] = True
        weights, withheld = {"forgejo": 0.3}, {S1: "lent", S3: "hot"}
        waits = [("forgejo", frozenset(), 60), ("forgejo", frozenset(), 30), (REPO, REPO_JOB, 10), ("github", UNITY_JOB, 5)]
        out = governor.units(LCFG, runners, box.seat_state(), waits, [UNITY_JOB], [frozenset({"light"})],
                             weights, withheld, {"unity": 600, "forgejo": 120})
        capacity, busy, backlog, drain = out
        self.assertEqual(capacity, {"box": 2})  # S3 is hot: no capacity; lent S1 still is
        self.assertAlmostEqual(busy["box"], 1.3)  # S2 whole, S1's busy Forgejo seat at 0.3
        self.assertEqual({k: round(v, 3) for k, v in backlog.items()}, {"forgejo": 0.6, "repo": 1.0, "slot": 2.0})
        self.assertAlmostEqual(drain, (0.6 * 120 + 1 * 600 + 2 * 600) / 2)  # repo and slot priced at the pool's p50
        text = governor.render(LCFG, {"disk": {}}, runners, 0, [], True, in_units=out)
        for line in ('ci_governor_capacity_units{host="box"} 2', 'ci_governor_busy_units{host="box"} 1.300',
                     'ci_governor_backlog_units{kind="slot"} 2.000', "ci_governor_drain_seconds 936"):
            self.assertIn(line, text)
        # No run time to price it: no drain series, so a reader shows "-", never a made-up 0.
        out = governor.units(LCFG, runners, box.seat_state(), waits, [], [], weights, withheld, {})
        self.assertIsNone(out[3])
        self.assertNotIn("ci_governor_drain_seconds", governor.render(LCFG, {"disk": {}}, runners, 0, [], True, in_units=out))
        # Nothing waiting drains in 0, whatever the run times.
        self.assertEqual(governor.units(LCFG, runners, {}, [], [], [], {}, {}, {})[3], 0.0)
        # A hot slot still running its job is capacity: the hold stops the next job, so busy
        # never reads past capacity (ms-a2 read 4/2 live, 2026-11-19).
        runners[S3]["busy"] = True
        capacity, busy, _, _ = governor.units(LCFG, runners, box.seat_state(), [], [], [], weights, withheld, {})
        self.assertEqual(capacity, {"box": 3})
        self.assertAlmostEqual(busy["box"], 2.3)

    def test_an_ack_while_its_seat_is_busy_does_not_unlend(self):
        box = Box(s1=DRAINED | {"unity-lent"})
        box.lends[S1] = {"seat": FJ_SEAT, "phase": "returning", "at": 10_000}
        box.fj["busy"] = True
        box.leases.wait(FJ_SEAT, "github", 0)  # any LAN client can ask, the seat or not
        self.assertEqual(box.plan([], 10_060), [])
        box.fj["busy"] = False
        self.assertEqual(box.plan([], 10_060)[0][0], "readmit")

    def test_a_lend_whose_relabel_failed_half_way_is_never_granted(self):
        box, job = Box(), [("forgejo", frozenset({"docker"}), 60)]
        box.lends[S1] = {"seat": FJ_SEAT, "phase": "held", "at": 10_000}
        box.gh.scopes[ORG][S1]["labels"] = DRAINED | {"unity-lent", "light"}
        self.assertEqual(box.plan(job, 10_060), [])
        box.gh.scopes[ORG][S1]["labels"] = DRAINED | {"unity-lent"}
        self.assertEqual(box.plan(job, 10_060)[0][0], "grant")

    def test_a_mark_on_a_slot_no_seat_borrows_is_dropped_on_its_host(self):
        box = Box()
        box.gh.scopes[ORG][S3]["labels"] = DRAINED | {"unity-lent"}
        box.lends[S3] = {"seat": None, "phase": "returning", "at": 10_000}
        self.assertEqual(box.step([]), [("readmit", S3)])
        self.assertEqual(box.labels(S3), ADMITTED)

    def test_a_repo_seat_runs_only_while_its_slot_is_lent(self):
        box, job = Box(s1=ADMITTED, s2=DRAINED), [(REPO, REPO_JOB, 60)]
        self.assertEqual(box.step(job), [("lend", S2)])
        self.assertIn("light-withheld", box.gh.scopes[f"repos/{REPO}"][REPO_SEAT]["labels"])
        self.assertEqual(box.step(job, 10_060), [("grant", S2)])
        self.assertEqual(box.gh.scopes[f"repos/{REPO}"][REPO_SEAT]["labels"], {"self-hosted", "light"})
        self.assertEqual(box.step([], 10_120), [("return", S2)])
        self.assertEqual(box.gh.scopes[f"repos/{REPO}"][REPO_SEAT]["labels"], {"self-hosted", "light-withheld"})
        self.assertEqual(box.step([], 10_180), [("readmit", S2)])
        self.assertEqual(box.labels(S2), ADMITTED)

    def test_a_repo_seat_admitted_with_no_lend_is_withheld(self):
        box = Box(seat={"self-hosted", "light"})
        self.assertEqual(box.step([]), [("withhold", REPO_SEAT)])
        self.assertEqual(box.gh.scopes[f"repos/{REPO}"][REPO_SEAT]["labels"], {"self-hosted", "light-withheld"})

    def light_box(self, weight, n=3):
        """box with n org light seats on slot 1, all withheld, and the box that sees them (hc-667). Its slots
        take no light, as the topology renders a host whose seats serve light."""
        lights = [f"{S1}-light-{i}" for i in range(1, n + 1)]
        seats = {**LCFG["seats"], **{m: {"host": "box", "slot": S1, "forge": "github", "repo": None, "caps": ["light"]}
                                     for m in lights}}
        box = Box()
        box.weights = {"light": weight}
        for m in lights:
            box.gh.scopes[ORG][m] = {"labels": {"self-hosted", "light-withheld"}, "busy": False}
        slots = {S1, S2, S3}
        box.runners = lambda: {k: v for k, v in Box.runners(box).items() if k in slots}
        box.seat_state = lambda: {
            **Box.seat_state(box),
            **{m: {"busy": box.gh.scopes[ORG][m]["busy"], "online": True,
                   "withheld": "light-withheld" in box.gh.scopes[ORG][m]["labels"]} for m in lights}}
        return box, lights, mock.patch.dict(LCFG, {"seats": seats, "shared": {}})

    def test_light_seats_of_one_lend_are_admitted_together_as_far_as_their_weights_fit(self):
        jobs = [("light", REPO_JOB, 60)] * 3
        for weight, admitted in ((0.3, 3), (0.5, 2), (None, 1)):
            with self.subTest(weight=weight):
                box, lights, seats = self.light_box(weight)
                if weight is None:
                    box.weights = None
                with seats:
                    self.assertEqual(box.step(jobs), [("lend", S1)])
                    self.assertEqual(box.step(jobs, 10_060), [("grant", S1)] * admitted)
                    got = [box.gh.scopes[ORG][m]["labels"] for m in lights]
                    self.assertEqual(got[:admitted], [{"self-hosted", "light"}] * admitted)
                    self.assertEqual(got[admitted:], [{"self-hosted", "light-withheld"}] * (3 - admitted))
                    # An admitted seat in a granted lend is not withheld as one with no lend.
                    self.assertEqual(box.step(jobs, 10_120), [])

    def test_light_seats_go_back_together_and_the_slot_is_readmitted_once_none_runs(self):
        box, lights, seats = self.light_box(0.3)
        jobs = [("light", REPO_JOB, 60)] * 3
        with seats:
            box.step(jobs), box.step(jobs, 10_060)
            box.gh.scopes[ORG][lights[1]]["busy"] = True
            self.assertEqual(box.step([], 10_120), [])  # a busy seat keeps the lend
            box.gh.scopes[ORG][lights[1]]["busy"] = False
            self.assertEqual(box.step([], 10_180), [("return", S1)] * 3)
            self.assertTrue(all("light-withheld" in box.gh.scopes[ORG][m]["labels"] for m in lights))
            box.gh.scopes[ORG][lights[1]]["busy"] = True  # it finishes the job it took before it was withheld
            self.assertEqual(box.step([], 10_240), [])
            box.gh.scopes[ORG][lights[1]]["busy"] = False
            self.assertEqual(box.step([], 10_300), [("readmit", S1)])
            # Readmitted, the slot takes no light: its seats serve that kind.
            self.assertEqual(box.labels(), ADMITTED - {"light"})

    def test_an_older_github_job_takes_a_busy_light_lend_back_one_slot_per_job(self):
        # hc-766: light jobs kept every lent slot busy, so a Unity job older than any light one
        # never got a slot back while the lends waited to go idle.
        box, lights, seats = self.light_box(0.3)
        light = [("light", REPO_JOB, 60)] * 3
        repo = [(REPO, REPO_JOB, 60)]
        # The age rule past any jump lend (the next test).
        with seats, mock.patch.dict(LCFG, {"jump_lends": {}}):
            box.step(light + repo), box.step(light + repo, 10_060)
            self.assertEqual({s: l["phase"] for s, l in box.lends.items()}, {S1: "granted", S2: "granted"})
            box.gh.scopes[ORG][lights[0]]["busy"] = True
            box.gh.scopes[f"repos/{REPO}"][REPO_SEAT]["busy"] = True
            younger = [("github", GITHUB_JOB[0], 30), ("light", REPO_JOB, 120), (REPO, REPO_JOB, 120)]
            self.assertEqual(box.plan(younger, 10_120), [])
            # One older job takes one slot back, busy or not; the other lend keeps serving.
            older = [("github", GITHUB_JOB[0], 900), ("light", REPO_JOB, 120), (REPO, REPO_JOB, 120)]
            self.assertEqual(box.step(older, 10_120), [("return", S1)] * 3)
            # Its slot is still draining: the same job does not pull the repo lend back too.
            self.assertEqual(box.plan(older, 10_180), [])
            self.assertEqual([s[:2] for s in box.plan(older + [("github", GITHUB_JOB[0], 800)], 10_180)],
                             [("return", S2)])

    def test_a_busy_lend_whose_kind_has_no_waiter_is_not_taken_back(self):
        # Once a lend's jobs start, its kind has no waiter left; read as -1, any GitHub job beat
        # it, so every lend went back minutes after its grant and light waited 25 min for the next.
        box, lights, seats = self.light_box(0.3)
        light = [("light", REPO_JOB, 60)] * 3
        repo = [(REPO, REPO_JOB, 60)]
        with seats:
            box.step(light + repo), box.step(light + repo, 10_060)
            self.assertEqual({s: l["phase"] for s, l in box.lends.items()}, {S1: "granted", S2: "granted"})
            box.gh.scopes[ORG][lights[0]]["busy"] = True
            box.gh.scopes[f"repos/{REPO}"][REPO_SEAT]["busy"] = True
            self.assertEqual(box.plan([("github", GITHUB_JOB[0], 57)], 10_120), [])
            # Idle with nothing of its kind waiting, it goes back by the want rule.
            box.gh.scopes[ORG][lights[0]]["busy"] = False
            back = [s for s in box.plan([("github", GITHUB_JOB[0], 57)], 10_180) if s[1] == S1]
            self.assertEqual({s[0] for s in back}, {"return"})
            self.assertEqual(back[0][3], "no light job needs it")

    def test_a_jump_kind_lends_past_an_older_github_job_and_keeps_that_lend_while_busy(self):
        # ucp-2881: a gate's report job is created when its last shard ends, so it is younger than
        # every queued shard and waited out the whole Unity queue for a lend (p90 9-11 min).
        light = [("light", REPO_JOB, 60)]
        older = [("github", GITHUB_JOB[0], 600)]
        for jump, want in (({}, []), ({"light": 1}, [("lend", S1)])):
            with self.subTest(jump=jump):
                box, lights, seats = self.light_box(0.3)
                with seats, mock.patch.dict(LCFG, {"jump_lends": jump}):
                    self.assertEqual(box.step(light + older), want)
        box, lights, seats = self.light_box(0.3)
        with seats, mock.patch.dict(LCFG, {"jump_lends": {"light": 1}}):
            box.step(light + older), box.step(light + older, 10_060)
            box.gh.scopes[ORG][lights[0]]["busy"] = True
            self.assertEqual(box.plan(light + older, 10_120), [])  # busy, not taken back
            # One jump lend only: a second light lend waits on the age rule.
            self.assertEqual(box.plan(light * 4 + older, 10_120), [])
            # Past jump_max_seconds the age rule holds again, so a light stream cannot starve a Unity job.
            self.assertEqual(box.step(light + [("github", GITHUB_JOB[0], 1000)], 10_120), [("return", S1)] * 3)

    def test_a_jump_lend_never_starts_against_a_job_past_jump_max_and_a_negative_n_jumps_nothing(self):
        light = [("light", REPO_JOB, 60)]
        for jump, age in (({"light": 1}, 1000), ({"light": -1}, 900)):
            with self.subTest(jump=jump, age=age):
                box, _, seats = self.light_box(0.3)
                with seats, mock.patch.dict(LCFG, {"jump_lends": jump}):
                    self.assertEqual(box.step(light + [("github", GITHUB_JOB[0], age)]), [])

    def test_reclaim_keeps_the_busiest_jump_lend_and_takes_the_other_back(self):
        box, lights, seats = self.light_box(0.3)
        light = [("light", REPO_JOB, 60)] * 3
        repo = [(REPO, REPO_JOB, 60)]
        with seats, mock.patch.dict(LCFG, {"jump_lends": {"light": 1, REPO: 1}}):
            box.step(light + repo), box.step(light + repo, 10_060)
            self.assertEqual({s: l["phase"] for s, l in box.lends.items()}, {S1: "granted", S2: "granted"})
            box.gh.scopes[ORG][lights[0]]["busy"] = True
            box.gh.scopes[ORG][lights[1]]["busy"] = True
            older = [("github", GITHUB_JOB[0], 600), ("light", REPO_JOB, 120), (REPO, REPO_JOB, 120)]
            # Both are kept under jump_max: each is its kind's one jump lend.
            self.assertEqual(box.plan(older, 10_120), [])
        with seats, mock.patch.dict(LCFG, {"jump_lends": {"light": 1}}):
            # The repo kind no longer jumps, so its lend goes back and the busy light lend stays.
            self.assertEqual([s[:2] for s in box.plan(older, 10_120)], [("return", S2)])

    def test_a_light_seat_admitted_with_no_lend_is_withheld(self):
        box, lights, seats = self.light_box(0.3)
        box.gh.scopes[ORG][lights[0]]["labels"] = {"self-hosted", "light"}
        with seats:
            self.assertEqual(box.step([]), [("withhold", lights[0])])
        self.assertEqual(box.gh.scopes[ORG][lights[0]]["labels"], {"self-hosted", "light-withheld"})

    def test_a_job_on_a_kind_seats_serve_waits_for_a_lend_and_asks_the_host_for_no_slot(self):
        box, _, seats = self.light_box(0.3)
        light = {"labels": "self-hosted,light", "pool": "light", "repo": "platform"}
        gh_job = {"labels": "self-hosted,unity", "pool": "unity", "repo": "platform"}

        def prom(q):
            rows = {"github_actions_job_waiting": [{"metric": m, "value": [0, "60"]} for m in (light, gh_job)]}
            return next((v for k, v in rows.items() if q.startswith(k)), [])
        with seats, mock.patch.object(governor, "prom", side_effect=prom):
            _, waits = governor.observe_seats(LCFG)
            self.assertEqual([(k, a) for k, _, a in waits], [("light", 60.0), ("github", 60.0)])
            need = governor.label_set(light["labels"])
            self.assertEqual(governor.shared_demand(LCFG, "box", box.runners(), [need]), 0)
        self.assertEqual(governor.shared_demand(LCFG, "box", box.runners(), [need]), 1)

    def test_a_job_naming_no_kind_is_a_rival_github_job_and_an_unread_one_is_nothing(self):
        # pool="other": runs-on names no kind. slot_takes, on its labels, says which slot it
        # could take; a run whose jobs were unreadable has no labels and holds back no lend.
        _, _, seats = self.light_box(0.3)
        rows = [{"metric": {"labels": ls, "pool": "other", "repo": "platform"}, "value": [0, "60"]}
                for ls in ("self-hosted,linux,host-box", "")]
        with seats, mock.patch.object(governor, "prom",
                                      side_effect=lambda q: rows if q.startswith("github_actions_job_waiting") else []):
            _, waits = governor.observe_seats(LCFG)
        self.assertEqual(waits, [("github", frozenset({"self-hosted", "linux", "host-box"}), 60.0)])

    def cycle(self, box, waiting, seat_waits):
        with mock.patch.object(governor, "observe", return_value=(readings(box=COOL), box.runners(), waiting)), \
                mock.patch.object(governor, "observe_seats", return_value=(box.seat_state(), seat_waits)), \
                mock.patch.object(governor, "read_pins", return_value=({}, {})), \
                mock.patch.object(governor, "gh", box.gh), \
                mock.patch.object(governor.time, "time", return_value=10_000), mock.patch("builtins.print"):
            governor.cycle(LCFG, box.last, {}, None, (box.leases, box.lends))

    def test_a_cycle_lends_before_decide_and_decide_never_admits_the_lent_slot(self):
        box = Box()
        self.cycle(box, list(GITHUB_JOB) * 3, [("github", GITHUB_JOB[0], 100), ("forgejo", frozenset({"docker"}), 300)])
        self.assertEqual(box.labels(), DRAINED | {"unity-lent"})
        self.assertIn('ci_governor_lent{host="box",name="box-unity-pool-1",to="forgejo",seat="box-unity-pool-1-forgejo"} 1',
                      governor.State.metrics)

    def test_a_restart_keeps_a_lent_slot_lent_to_its_seat(self):
        # Its Forgejo seat runs a job: still granted, and the lease the restart reset is forgejo again.
        box = Box(s1=DRAINED | {"unity-lent"})
        box.fj["busy"] = True
        self.cycle(box, list(GITHUB_JOB) * 3, [])
        self.assertEqual(box.lends[S1], {"seat": FJ_SEAT, "phase": "granted", "at": 10_000})
        self.assertEqual(box.leases.get(S1)[0], "forgejo")
        self.assertIn("unity-lent", box.labels())
        # Idle with Forgejo jobs waiting: granted again in the same cycle.
        box = Box(s1=DRAINED | {"unity-lent"})
        self.cycle(box, [], [("forgejo", frozenset({"docker"}), 600)])
        self.assertEqual(box.lends[S1]["phase"], "granted")
        self.assertEqual(box.leases.get(S1)[0], "forgejo")
        # A repo seat that is admitted keeps its lend.
        box = Box(s2=DRAINED | {"unity-lent"}, seat={"self-hosted", "light"})
        self.cycle(box, [], [(REPO, REPO_JOB, 60)])
        self.assertEqual(box.lends[S2]["seat"], REPO_SEAT)
        self.assertEqual(box.lends[S2]["phase"], "granted")
        # Nothing needs it: back to the pool, admitted, in the same cycle.
        box = Box(s1=DRAINED | {"unity-lent"})
        self.cycle(box, list(GITHUB_JOB) * 3, [])
        self.assertEqual(box.lends, {})
        self.assertEqual(box.labels(), ADMITTED)

    def test_a_mark_the_exporter_still_shows_after_github_dropped_it_is_no_lend(self):
        # ar-2781: the exporter lags a cycle, so the one after a readmit still read the mark;
        # adopted as held for the Forgejo seat, the slot GitHub had back counted as Forgejo
        # capacity for 40 minutes while 7 jobs waited, and an idle one was granted a second job.
        box, wait = Box(), [("forgejo", frozenset({"docker"}), 600)]
        stale = Box(s1=DRAINED | {"unity-lent"}).runners()
        with mock.patch.object(box, "runners", return_value=stale):
            self.cycle(box, [], [])
        self.assertNotIn(S1, box.lends)
        self.assertEqual(box.leases.get(S1)[0], "github")
        self.assertEqual(box.labels(), DRAINED)
        # Nothing holds it out: the next Forgejo wait lends it for real.
        self.cycle(box, [], wait)
        self.assertEqual(box.lends[S1]["seat"], FJ_SEAT)
        self.assertEqual(box.labels(), DRAINED | {"unity-lent"})

    def test_a_lent_mark_the_exporter_shows_only_after_the_first_cycle_is_still_adopted(self):
        # A slot lent before the restart that the first reading missed keeps its lend once it shows.
        box = Box(s1=DRAINED | {"unity-lent"})
        box.fj["busy"] = True
        with mock.patch.object(box, "runners", return_value=Box().runners()):
            self.cycle(box, [], [])
        self.cycle(box, [], [])
        self.assertEqual(box.lends[S1], {"seat": FJ_SEAT, "phase": "granted", "at": 10_000})
        self.assertEqual(box.leases.get(S1)[0], "forgejo")

    def test_a_lent_mark_whose_live_read_fails_is_adopted_from_the_reading(self):
        box = Box(s1=DRAINED | {"unity-lent"})
        box.fj["busy"] = True
        real = governor.find_runner
        reads = []

        def first_read_fails(scope, name):
            reads.append(name)
            if len(reads) == 1:
                raise OSError("down")
            return real(scope, name)
        with mock.patch.object(governor, "find_runner", side_effect=first_read_fails):
            self.cycle(box, list(GITHUB_JOB) * 3, [])
        self.assertEqual(box.lends[S1], {"seat": FJ_SEAT, "phase": "granted", "at": 10_000})
        self.assertIn("unity-lent", box.labels())

    def test_a_lent_mark_on_a_runner_github_does_not_list_is_left_out(self):
        box = Box()
        stale = Box(s1=DRAINED | {"unity-lent"}).runners()
        del box.gh.scopes[ORG][S1]
        with mock.patch.object(box, "runners", return_value=stale):
            self.cycle(box, [], [("forgejo", frozenset({"docker"}), 600)])
        self.assertNotIn(S1, box.lends)
        self.assertEqual(box.leases.get(S1)[0], "github")

    def test_a_burst_takes_every_idle_withheld_slot_of_a_room_host_in_one_cycle(self):
        # hc-643: two pool-1 lends and nothing for pool-2 and 3 while 7 Forgejo jobs waited.
        seats = {f"box-unity-pool-{n}-forgejo": {"host": "box", "slot": f"box-unity-pool-{n}", "forge": "forgejo",
                                                 "repo": None, "caps": ["forgejo"]} for n in (1, 2, 3)}
        box = Box(s2=DRAINED)
        box.gh.scopes[ORG][S3]["labels"] = set(DRAINED)
        box.gh.scopes[ORG]["box-unity-pool-4"] = {"labels": set(ADMITTED), "busy": False}
        box.leases = governor.Leases({n: s["slot"] for n, s in seats.items()})
        state = {s: {"busy": False, "online": False, "withheld": False} for s in seats}
        burst = [("forgejo", frozenset({"docker"}), 900)] * 7
        with mock.patch.dict(LCFG, {"seats": seats}), mock.patch.object(box, "seat_state", return_value=state):
            box.last["box"] = 9_900  # in its cooldown from an earlier lend
            self.assertEqual(box.step(burst), [("lend", S1), ("lend", S2), ("lend", S3)])
            self.assertEqual([s for s, *_ in box.plan(burst, hosts=readings(box=HOT))], ["unlend"] * 3)

    def test_an_idle_slot_two_kinds_want_is_lent_once_to_the_older_wait(self):
        # hc-789: deskslim-1's idle withheld slots were lent to a light seat (3783s) and then, in the
        # same cycle, to its Forgejo seat (3359s); the second lend overwrote the first, so the light
        # jobs kept waiting while the slot served Forgejo.
        seats = {**LCFG["seats"], REPO_SEAT: {**LCFG["seats"][REPO_SEAT], "slot": S1}}
        box = Box()
        waits = [(REPO, REPO_JOB, 3783), ("forgejo", frozenset({"docker"}), 3359)]
        with mock.patch.dict(LCFG, {"seats": seats}):
            self.assertEqual(box.step(waits), [("lend", S1)])
            self.assertEqual(box.lends[S1]["seat"], REPO_SEAT)
            older_forgejo = [(REPO, REPO_JOB, 300), ("forgejo", frozenset({"docker"}), 900)]
            self.assertEqual([(s, seat) for s, _, seat, _ in Box().plan(older_forgejo)], [("lend", FJ_SEAT)])


    def test_each_withheld_slot_says_why(self):
        box = Box(s2=DRAINED)
        box.gh.scopes[ORG][S3]["labels"] = DRAINED | {"unity-lent"}
        box.lends[S3] = {"seat": None, "phase": "returning", "at": 10_000}

        def why(hosts=None, pins=None, last=None):
            return governor.withheld_reasons(LCFG, hosts or readings(box=COOL), box.runners(), box.lends, pins or {},
                                             last or {}, 10_000)
        self.assertEqual(why(), {S1: "no-work", S2: "no-work", S3: "lent"})
        self.assertEqual(why(last={"box": 9_900})[S1], "cooldown")
        self.assertEqual(why(pins={"box": 1})[S1], "pin")
        self.assertEqual(why(readings(box=HOT))[S1], "hot")
        self.assertEqual(why(readings(box=BUSY))[S1], "hot")  # short of admit on load
        self.assertEqual(why(readings(box=FULL))[S1], "disk")
        self.assertEqual(why(readings(box={**COOL, "disk": 0.22}))[S1], "disk")  # short of admit on disk
        withheld = sorted(n for n, r in box.runners().items() if r["withheld"])  # as the cycle reads them
        self.cycle(box, [], [])  # one series per withheld slot, a reason from the set
        got = re.findall(r'ci_governor_withheld\{runner="([^"]+)",reason="([^"]+)"\} 1', governor.State.metrics)
        self.assertEqual(sorted(n for n, _ in got), withheld)
        self.assertTrue(withheld and all(r in governor.WITHHELD_REASONS for _, r in got), got)


# STANDING SLICE: box gains a standing Forgejo seat and a standing seat of the personal repo, both
# in its small-job slice, beside the lend seats above.
FJ_STAND, REPO_STAND = "box-small-forgejo-1", "box-small-game-1"
STANDING = {FJ_STAND: {"host": "box", "forge": "forgejo", "repo": None, "caps": ["forgejo"], "share": 0.25},
            REPO_STAND: {"host": "box", "forge": "github", "repo": REPO, "caps": ["light"], "share": 0.8}}
FJ_JOB = frozenset({"docker"})


class StandingSliceTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(LCFG["standing"], STANDING)
        patch.start()
        self.addCleanup(patch.stop)

    def box(self, **standing):
        """Box with the standing seats' exporter rows: {seat: busy} for each one online."""
        box = Box()
        seat_state = box.seat_state
        box.standing = {s: {"busy": b, "online": True, "withheld": False} for s, b in standing.items()}
        box.seat_state = lambda: {**seat_state(), **box.standing}
        return box

    def test_an_idle_standing_seat_takes_the_wait_so_nothing_is_lent(self):
        self.assertEqual(self.box(**{FJ_STAND: False}).plan([("forgejo", FJ_JOB, 300)]), [])
        self.assertEqual(self.box(**{REPO_STAND: False}).plan([(REPO, REPO_JOB, 300)]), [])
        # One idle standing seat answers one wait, not two.
        self.assertEqual([s for s, *_ in self.box(**{FJ_STAND: False}).plan([("forgejo", FJ_JOB, 300)] * 2)], ["lend"])
        # A busy or offline standing seat answers none.
        self.assertEqual([s for s, *_ in self.box(**{FJ_STAND: True}).plan([("forgejo", FJ_JOB, 300)])], ["lend"])
        self.assertEqual([s for s, *_ in self.box().plan([("forgejo", FJ_JOB, 300)])], ["lend"])
        # A standing seat of another kind answers nothing.
        self.assertEqual([s for s, *_ in self.box(**{REPO_STAND: False}).plan([("forgejo", FJ_JOB, 300)])], ["lend"])

    def test_an_idle_standing_seat_on_a_full_slice_answers_no_wait(self):
        job = [("forgejo", FJ_JOB, 300)]
        # The busy repo seat takes 0.8 of the slice, so the idle Forgejo seat's 0.25 does not fit.
        self.assertEqual([s for s, *_ in self.box(**{FJ_STAND: False, REPO_STAND: True}).plan(job)], ["lend"])
        # Nor does a granted lend go back to it.
        box = self.box()
        self.assertEqual(box.step(job), [("lend", S1)])
        self.assertEqual(box.step(job, 10_060), [("grant", S1)])
        box.standing.update({FJ_STAND: {"busy": False, "online": True, "withheld": False},
                             REPO_STAND: {"busy": True, "online": True, "withheld": False}})
        self.assertEqual(box.plan(job, 10_120), [])
        box.standing[REPO_STAND]["busy"] = False
        self.assertEqual([s for s, *_ in box.plan(job, 10_120)], ["return"])

    def test_a_standing_seat_admitted_with_no_lend_is_never_withheld(self):
        box = self.box(**{REPO_STAND: False})
        steps = box.plan([])
        self.assertNotIn(REPO_STAND, [seat for _, _, seat, _ in steps])
        self.assertEqual(steps, [])

    def test_a_granted_lend_goes_back_once_idle_standing_seats_cover_the_waits(self):
        box = self.box()
        job = [("forgejo", FJ_JOB, 60)]
        self.assertEqual(box.step(job), [("lend", S1)])
        self.assertEqual(box.step(job, 10_060), [("grant", S1)])
        box.standing[FJ_STAND] = {"busy": False, "online": True, "withheld": False}
        self.assertEqual(box.step(job, 10_120), [("return", S1)])

    def two_waves(self, standing):
        """A fan-in of two Forgejo jobs 90 s apart, read every 30 s: ([(t, steps)], {wave: start}).
        Each cycle reads the waits, steps, then the seat takes a waiting job if it is idle and may: the
        lend seat while its lease is forgejo, the standing seat always. Each job runs 20 s."""
        box = self.box(**({FJ_STAND: False} if standing else {}))
        seat = box.standing[FJ_STAND] if standing else box.fj
        out, running, arrive = [], {}, {1: 10_000, 2: 10_090}
        for t in range(10_000, 10_330, 30):
            seat["busy"] = any(at <= t < at + 20 for at in running.values())
            waits = [("forgejo", FJ_JOB, t - at) for w, at in arrive.items() if at <= t and w not in running]
            out.append((t, box.step(waits, t)))
            lease = "forgejo" if standing else box.leases.wait(FJ_SEAT, box.leases.of(FJ_SEAT), 0)
            if waits and lease == "forgejo" and not seat["busy"]:
                running[min(w for w in arrive if arrive[w] <= t and w not in running)] = t
        return out, running

    def test_a_two_wave_fan_in_runs_on_the_standing_slice_and_lends_nothing(self):
        steps, running = self.two_waves(standing=True)
        self.assertEqual([s for _, s in steps if s], [])
        self.assertEqual(running, {1: 10_000, 2: 10_090})  # each wave starts the cycle it arrives

    def test_negative_control_lend_seats_alone_give_the_slot_back_between_the_waves(self):
        steps, running = self.two_waves(standing=False)
        moves = [(t, s) for t, ss in steps for s, _ in ss]
        # Wave 1 waits on a lend and a grant; the slot goes back once its job ends, so wave 2 waits on
        # a second lend: the churn the standing slice removes.
        self.assertEqual([s for _, s in moves][:6], ["lend", "grant", "return", "readmit", "lend", "grant"])
        self.assertEqual(running, {1: 10_030, 2: 10_150})  # wave 2 waits 60 s for its second lend

    def test_a_standing_forgejo_seat_holds_forgejo_for_good(self):
        leases = governor.Leases({FJ_SEAT: S1})
        governor.standing_leases(LCFG, leases)
        self.assertEqual(leases.slot_of[FJ_STAND], governor.standing_slot("box"))
        self.assertNotIn(REPO_STAND, leases.slot_of)
        self.assertEqual(leases.wait(FJ_STAND, "github", 0), "forgejo")
        leases.limit({S1: 0})
        leases.set(S1, "forgejo")
        leases.set(S1, "github")
        self.assertEqual(leases.wait(FJ_STAND, "forgejo", 0, task="7"), "forgejo")
        self.assertEqual(leases.tasks(), {FJ_STAND: "7"})
        self.assertEqual(leases.wait(FJ_SEAT, "github", 0), "github")

    def test_slice_units_count_each_host_slice_once_weighed_by_its_busy_seats_shares(self):
        one = {FJ_STAND: {"busy": True, "online": True}, REPO_STAND: {"busy": False, "online": True}}
        both = {FJ_STAND: {"busy": True, "online": True}, REPO_STAND: {"busy": True, "online": True}}
        self.assertEqual(governor.slice_units(LCFG, one), ({"box": 1}, {"box": 0.25}))
        self.assertEqual(governor.slice_units(LCFG, both), ({"box": 1}, {"box": 1.0}))  # 1.05: at most 1
        self.assertEqual(governor.slice_units(LCFG, {}), ({}, {}))
        out = governor.every_machine(LCFG, ({"box": 3}, {"box": 1.0}, {}, 0.0), ({}, {}), ({}, {}, 0),
                                     governor.slice_units(LCFG, one))
        self.assertEqual((out[0]["box"], round(out[1]["box"], 2)), (4, 1.25))

    def test_a_standing_seat_has_its_kind(self):
        self.assertEqual(governor.seat_kind(LCFG, FJ_STAND), "forgejo")
        self.assertEqual(governor.seat_kind(LCFG, REPO_STAND), REPO)
        self.assertEqual(governor.weight_kind(LCFG, REPO_STAND), "repo")


class LeaseTest(unittest.TestCase):
    def test_wait_answers_at_once_on_a_change_and_have_on_its_timeout(self):
        leases = governor.Leases({"s": "s"})
        self.assertEqual(leases.wait("s", "github", 0), "github")
        self.assertEqual(leases.get("s")[1], "github")
        self.assertEqual(leases.wait("s", "forgejo", 0), "github")  # asked with a lease it does not hold
        got = []
        t = __import__("threading").Thread(target=lambda: got.append(leases.wait("s", "github", 120)))
        start = time.monotonic()
        t.start()
        leases.set("s", "forgejo")
        t.join(10)  # a guard only: the set wakes it
        self.assertEqual(got, ["forgejo"])
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(leases.get("s")[1], None)  # a change clears the ack until the supervisor holds it
        leases.wait("s", "forgejo", 0)
        self.assertEqual(leases.get("s")[1], "forgejo")

    def test_the_lease_endpoint(self):
        from http.server import ThreadingHTTPServer
        import threading
        leases = governor.Leases({FJ_SEAT: S1})
        server = ThreadingHTTPServer(("127.0.0.1", 0), governor.Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}/lease"

        def ask(query):
            try:
                with urllib.request.urlopen(f"{base}?{query}", timeout=10) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                e.close()
                return e.code, ""
        self.assertEqual(ask("seat=box-unity-pool-1-forgejo&have=github&wait=0")[0], 404)  # no leases yet
        with mock.patch.object(governor.Handler, "leases", leases):
            self.assertEqual(ask("seat=box-unity-pool-9-forgejo&have=github&wait=0")[0], 404)
            self.assertEqual(ask("seat=box-unity-pool-1-forgejo&have=docker&wait=0")[0], 400)
            self.assertEqual(ask("seat=box-unity-pool-1-forgejo&have=github&wait=soon")[0], 400)
            self.assertEqual(ask("seat=box-unity-pool-1-forgejo&have=github&wait=0"), (200, "github\n"))
            got = []
            t = threading.Thread(target=lambda: got.append(ask("seat=box-unity-pool-1-forgejo&have=github&wait=120")))
            t.start()
            leases.set("box-unity-pool-1", "forgejo")
            t.join(10)
            self.assertEqual(got, [(200, "forgejo\n")])


class SeatConfigTest(unittest.TestCase):
    def test_the_live_seats_borrow_lan_unity_slots(self):
        seats = LIVE["seats"]
        self.assertTrue(any(s["forge"] == "forgejo" for s in seats.values()))
        self.assertTrue(any(s["repo"] for s in seats.values()))
        slots = {s["name"] for h, e in live_topology()["hosts"].items() if h in LIVE["hosts"]
                 for s in e["slots"] if "unity" in s["caps"]}
        self.assertTrue(all(s["slot"] in slots for s in seats.values()))
        self.assertNotIn("hp-victus", {s["host"] for s in seats.values()})

    def test_labels_and_instances_come_from_the_topology(self):
        rendered = live_topology()
        self.assertEqual(LIVE["withheld_label"], rendered["labels"]["withheld"])
        self.assertEqual(LIVE["lent_label"], rendered["labels"]["lent"])
        for host, entry in LIVE["standby"].items():
            self.assertEqual({k: entry["power"][k] for k in governor.INSTANCE_KEYS}, rendered["hosts"][host]["power"])

    def test_a_copied_label_or_instance_id_in_config_fails(self):
        topo = {"hosts": {"c": {"site": "cloud", "power": {"provider": "aws", "instance_id": "i-0c", "region": "r"},
                                "slots": [{"name": "c-unity-pool-1", "caps": ["unity"]}]}}}
        base = {**live_config(), "hosts": {}, "rates": {"c": {"usd_per_hour": 1, "basis": "on-demand"}}}
        self.assertEqual(load_files({**base, "power": {}}, topo)["standby"]["c"]["power"]["instance_id"], "i-0c")
        with self.assertRaisesRegex(ValueError, "label keys belong in the topology"):
            load_files({**base, "power": {}, "pool_label": "unity"}, topo)
        with self.assertRaisesRegex(ValueError, "instance keys belong in the topology.s power .inventory ci_power.: c.instance_id"):
            load_files({**base, "power": {"c": {"instance_id": "i-0d"}}}, topo)
        del topo["hosts"]["c"]["power"]
        with self.assertRaisesRegex(ValueError, "power blocks for no powered cloud Unity host: c"):
            load_files({**base, "power": {"c": {"max_hours_day": 1}}}, topo)

    def test_a_seat_that_borrows_no_lan_unity_slot_fails(self):
        topo = {"hosts": {"box": {"site": "lan", "slots": [
            {"name": S1, "caps": ["unity", "light"]},
            {"name": FJ_SEAT, "caps": ["forgejo"], "forge": "forgejo", "borrows": "box-unity-pool-7"}]}}}
        conf = {**live_config(), "hosts": {}, "power": {}, "rates": {}}
        with self.assertRaisesRegex(ValueError, "seats that borrow no LAN Unity slot: box-unity-pool-1-forgejo"):
            load_files(conf, topo)
        topo["hosts"]["box"]["slots"][1]["borrows"] = S1
        self.assertEqual(load_files(conf, topo)["seats"][FJ_SEAT]["slot"], S1)

    def test_the_live_standing_seats_sit_on_lan_unity_hosts_and_lend_nothing(self):
        standing = LIVE["standing"]
        self.assertEqual({s["host"] for s in standing.values()}, {"ms-a2", "deskslim-1", "deskslim-2", "tuf-b550"})
        self.assertFalse(set(standing) & set(LIVE["seats"]))
        self.assertTrue({"forgejo", "light", "heavy"} <= {governor.weight_kind(LIVE, n) for n in standing})

    def test_a_standing_seat_on_no_lan_unity_host_fails(self):
        topo = {"hosts": {"box": {"site": "lan", "slots": [{"name": S1, "caps": ["unity", "light"]}]},
                          "m": {"site": "local", "slots": [
                              {"name": "m-small-forgejo-1", "caps": ["forgejo"], "forge": "forgejo", "slice": "small"}]}}}
        conf = {**live_config(), "hosts": {}, "power": {}, "rates": {}}
        with self.assertRaisesRegex(ValueError, "standing seats on no LAN Unity host: m-small-forgejo-1"):
            load_files(conf, topo)
        topo["hosts"]["box"]["slots"].append(topo["hosts"].pop("m")["slots"][0])
        self.assertEqual(load_files(conf, topo)["standing"]["m-small-forgejo-1"]["host"], "box")


# A host with two local seats and a floor of one, so a step up and a step down both show.
LOCAL = {**LIVE, "local": {"ws": 2}, "local_min_admitted": 1, "cooldown_seconds": 600}


class LocalSeatTest(unittest.TestCase):
    def ask(self, local, seat, have, wait=0):
        return local.wait(seat, have, wait)

    def test_grants_go_to_the_oldest_waiter_up_to_admitted_and_done_hands_the_seat_on(self):
        local = governor.LocalSeats(LOCAL)
        self.assertEqual(sorted(local.host_of), ["ws-local-1", "ws-local-2"])
        self.assertEqual(self.ask(local, "ws-local-2", "github"), "local")  # floor 1: one granted
        self.assertEqual(self.ask(local, "ws-local-1", "github"), "github")  # waits
        self.assertEqual(local.state("ws"), (1, 1, 1))
        got = []
        t = __import__("threading").Thread(target=lambda: got.append(self.ask(local, "ws-local-1", "github", 120)))
        start = time.monotonic()
        t.start()
        self.assertEqual(self.ask(local, "ws-local-2", "done"), "github")  # gives it back: the waiter gets it
        t.join(10)  # a guard only: the done wakes it
        self.assertEqual(got, ["local"])
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(local.state("ws"), (1, 0, 1))
        self.assertEqual(self.ask(local, "ws-local-1", "local"), "local")  # a keepalive keeps it

    def test_an_admit_grants_a_waiter_and_a_lower_count_never_takes_a_held_seat(self):
        local = governor.LocalSeats(LOCAL)
        self.ask(local, "ws-local-1", "github")
        self.assertEqual(self.ask(local, "ws-local-2", "github"), "github")
        local.admit("ws", 2)
        self.assertEqual(self.ask(local, "ws-local-2", "github"), "local")
        local.admit("ws", 1)  # HOT: the next run waits, both running ones go on
        self.assertEqual(local.state("ws"), (2, 0, 1))
        self.assertEqual(self.ask(local, "ws-local-2", "local"), "local")
        self.ask(local, "ws-local-2", "done")
        self.assertEqual(self.ask(local, "ws-local-2", "github"), "github")  # one held, one admitted

    def test_a_run_the_governor_lost_keeps_its_seat_and_a_gone_one_loses_it(self):
        local = governor.LocalSeats(LOCAL)
        # A restart: the running run's keepalive holds the seat, past admitted.
        self.assertEqual(self.ask(local, "ws-local-1", "local"), "local")
        self.assertEqual(self.ask(local, "ws-local-2", "local"), "local")
        self.assertEqual(local.state("ws"), (2, 0, 1))
        now = time.time()
        local.tick(now + governor.LEASE_STALE_SECONDS - 1)
        self.assertEqual(local.state("ws")[0], 2)
        local.tick(now + governor.LEASE_STALE_SECONDS + 1)  # neither asked again: both gone
        self.assertEqual(local.state("ws"), (0, 0, 1))
        # A waiter that stopped asking leaves the queue before a grant strands a seat on it.
        self.assertEqual(self.ask(local, "ws-local-1", "github"), "local")
        self.assertEqual(self.ask(local, "ws-local-1", "local"), "local")  # the granted run confirms
        self.assertEqual(self.ask(local, "ws-local-2", "github"), "github")
        local.tick(time.time() + governor.LOCAL_WAIT_GRACE + 1)
        self.assertEqual(local.state("ws"), (1, 0, 1))  # the holder stays until it is stale

    def test_a_grant_no_run_confirms_goes_back_after_the_grace(self):
        # A run killed while it waited: its long-poll took the grant, and nothing confirms it.
        local = governor.LocalSeats(LOCAL)
        self.assertEqual(self.ask(local, "ws-local-1", "github"), "local")
        now = time.time()
        local.tick(now + governor.LOCAL_WAIT_GRACE - 1)
        self.assertEqual(local.state("ws"), (1, 0, 1))
        local.tick(now + governor.LOCAL_WAIT_GRACE + 1)
        self.assertEqual(local.state("ws"), (0, 0, 1))  # free for the next run
        self.assertEqual(self.ask(local, "ws-local-2", "github"), "local")
        # A re-ask with github before it confirms (a lost answer) still holds it, unconfirmed.
        self.assertEqual(self.ask(local, "ws-local-2", "github"), "local")
        self.assertIn("ws-local-2", local.fresh)

    def test_local_rule_moves_one_seat_by_the_verdict_and_shares_the_hosts_cooldown(self):
        rule = governor.local_rule
        self.assertEqual(rule(LOCAL, "ws", "ROOM", 1, 1, {}, 1000), 2)
        self.assertEqual(rule(LOCAL, "ws", "ROOM", 2, 1, {}, 1000), 2)  # every seat admitted
        self.assertEqual(rule(LOCAL, "ws", "ROOM", 1, 0, {}, 1000), 1)  # no run waits
        self.assertEqual(rule(LOCAL, "ws", "STEADY", 1, 3, {}, 1000), 1)
        self.assertEqual(rule(LOCAL, "ws", "HOT", 2, 0, {}, 1000), 1)
        self.assertEqual(rule(LOCAL, "ws", "HOT", 1, 0, {}, 1000), 1)  # the floor
        self.assertEqual(rule(LOCAL, "ws", "ROOM", 1, 1, {"ws": 900}, 1000), 1)  # cooldown
        self.assertEqual(rule(LOCAL, "ws", "HOT", 2, 0, {"ws": 900}, 1000), 2)
        self.assertEqual(rule(LOCAL, "ws", "FULL", 2, 0, {"ws": 900}, 1000), 1)  # FULL past it

    def test_local_pass_moves_the_count_and_starts_the_hosts_cooldown(self):
        local = governor.LocalSeats(LOCAL)
        self.ask(local, "ws-local-1", "github")
        self.ask(local, "ws-local-2", "github")
        last = {}
        with mock.patch("builtins.print") as out:
            governor.local_pass(LOCAL, local, {"ws": ("ROOM", "idle 0.60")}, last, time.time())
        self.assertEqual(local.state("ws"), (2, 0, 2))
        self.assertIn("local ws: 1 -> 2 seats", out.call_args.args[0])
        self.assertIn("ws", last)  # a GitHub admit on ws now waits out the cooldown, and so does the next local move
        # Negative control: a slot move this cycle (decide() set the host's last action) holds the local step.
        local, last = governor.LocalSeats(LOCAL), {"ws": time.time()}
        self.ask(local, "ws-local-1", "github")
        self.ask(local, "ws-local-2", "github")
        with mock.patch("builtins.print"):
            governor.local_pass(LOCAL, local, {"ws": ("ROOM", "idle 0.60")}, last, time.time())
        self.assertEqual(local.state("ws"), (1, 1, 1))

    def test_local_units_count_every_seat_but_on_a_hot_host_only_the_admitted_or_held(self):
        local = governor.LocalSeats(LOCAL)
        self.ask(local, "ws-local-1", "github")
        self.ask(local, "ws-local-2", "github")
        self.assertEqual(governor.local_units(LOCAL, local, {"ws": ("ROOM", "")}), ({"ws": 2}, {"ws": 1.0}, 1))
        self.assertEqual(governor.local_units(LOCAL, local, {"ws": ("HOT", "")}), ({"ws": 1}, {"ws": 1.0}, 1))
        self.assertEqual(governor.local_units(LOCAL, local, {"ws": ("STEADY", "short of admit on idle")})[0], {"ws": 1})
        self.ask(local, "ws-local-2", "local")  # held past admitted: capacity never reads under busy
        self.assertEqual(governor.local_units(LOCAL, local, {"ws": ("HOT", "")})[:2], ({"ws": 2}, {"ws": 2.0}))

    # machine-mode's unity_slots: the <host>.slots key's local field pins the host's local seats.
    PINNED_LOCAL = {**LOCAL, "local": {"ws": 3}, "local_min_admitted": 2}

    def test_a_local_pin_sets_the_count_below_the_floor_whatever_the_verdict_but_full(self):
        rule, cfg = governor.local_rule, self.PINNED_LOCAL
        self.assertEqual(rule(cfg, "ws", "ROOM", 3, 1, {}, 1000, 1), 1)  # under the floor of 2
        self.assertEqual(rule(cfg, "ws", "ROOM", 2, 0, {"ws": 900}, 1000, 1), 1)  # in the cooldown too
        self.assertEqual(rule(cfg, "ws", "HOT", 1, 0, {"ws": 900}, 1000, 2), 2)  # up, with no run waiting
        self.assertEqual(rule(cfg, "ws", "STEADY", 2, 0, {}, 1000, 9), 3)  # at most the seats
        self.assertEqual(rule(cfg, "ws", "ROOM", 2, 5, {}, 1000, 2), 2)  # never past the pin
        self.assertEqual(rule(cfg, "ws", "FULL", 3, 0, {}, 1000, 3), 2)  # FULL still steps down...
        self.assertEqual(rule(cfg, "ws", "FULL", 3, 0, {}, 1000, 2), 2)  # ...and never above the pin
        # Negative control: unpinned, the verdict and the floor rule as before.
        self.assertEqual(rule(cfg, "ws", "ROOM", 3, 1, {}, 1000), 3)
        self.assertEqual(rule(cfg, "ws", "HOT", 2, 0, {}, 1000), 2)

    def test_local_pass_obeys_the_pin_and_logs_it(self):
        cfg = self.PINNED_LOCAL
        local = governor.LocalSeats(cfg)
        for s in ("ws-local-1", "ws-local-2"):
            self.ask(local, s, "github")
        self.assertEqual(local.state("ws"), (2, 0, 2))
        with mock.patch("builtins.print") as out:
            governor.local_pass(cfg, local, {"ws": ("ROOM", "idle 0.60")}, {}, time.time(), {"ws": 1})
        self.assertEqual(local.state("ws"), (2, 0, 1))  # the running runs keep their seats
        self.assertIn("local ws: 2 -> 1 seats (2 held, 0 waiting): PINNED, pinned to 1", out.call_args.args[0])
        self.ask(local, "ws-local-3", "github")
        self.assertEqual(local.state("ws"), (2, 1, 1))  # the next run waits
        self.assertEqual(governor.local_units(cfg, local, {"ws": ("ROOM", "")}, {"ws": 1})[0], {"ws": 2})
        self.ask(local, "ws-local-1", "done")
        self.ask(local, "ws-local-2", "done")
        self.assertEqual(governor.local_units(cfg, local, {"ws": ("ROOM", "")}, {"ws": 1})[0], {"ws": 1})
        # Negative control: no pin, a ROOM host counts every seat.
        self.assertEqual(governor.local_units(cfg, local, {"ws": ("ROOM", "")})[0], {"ws": 3})

    def test_read_local_pins(self):
        import tempfile
        cfg = self.PINNED_LOCAL
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d), \
                mock.patch("builtins.print"):
            self.assertEqual(governor.read_local_pins(cfg), {})  # no ConfigMap
            path = os.path.join(d, "ws.slots")
            for body, want in (('{"slots": 0, "local": 1, "by": "m"}', {"ws": 1}),
                               ('{"slots": 0, "local": 0, "by": "m"}', {"ws": 0}),
                               ('{"slots": 0, "by": "m"}', {}),  # a runner pin alone pins no local seat
                               ('{"slots": 0, "local": -1, "by": "m"}', {}), ('{"slots": 0, "local": 1}', {})):
                with open(path, "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_local_pins(cfg), want, body)
            # A release reservation holds pool slots, not local seats.
            with open(os.path.join(d, "ws.reserve"), "w") as f:
                f.write('{"until": 99999999999}')
            with open(path, "w") as f:
                f.write('{"slots": 0, "local": 2, "by": "m"}')
            self.assertEqual(governor.read_local_pins(cfg), {"ws": 2})


class EveryMachineTest(unittest.TestCase):
    def test_the_live_topology_has_whole_slot_hosts_and_the_workstations_local_seats(self):
        self.assertEqual(sorted(LIVE["machines"]), ["mac-mini"])
        self.assertEqual(LIVE["local"], {CX: 3})
        self.assertEqual(LIVE["local_min_admitted"], 2)

    def test_every_topology_host_has_a_units_row(self):
        names = {"mac-mini-mac": {"busy": True, "online": True}, "m1-playground-mac": {"busy": False, "online": True}}
        machines = governor.machine_units(LIVE, names)
        self.assertEqual(machines, ({"mac-mini": 2}, {"mac-mini": 1.0}))
        local = governor.LocalSeats(LIVE)
        local.wait(f"{CX}-local-1", "github", 0)
        verdicts = {h: ("ROOM", "") for h in LIVE["hosts"]}
        pool = ({CX: 1, "ms-a2": 5}, {CX: 1.0, "ms-a2": 2.0}, {"slot": 2.0}, 300.0)
        capacity, busy, backlog, drain = governor.every_machine(LIVE, pool, machines,
                                                                governor.local_units(LIVE, local, verdicts))
        self.assertEqual(sorted(capacity), sorted(live_topology()["hosts"]))
        self.assertEqual(len(capacity), 6)
        self.assertEqual((capacity[CX], busy[CX]), (4, 2.0))  # one pool slot plus three local seats, one held
        self.assertEqual((capacity["mac-mini"], busy["mac-mini"]), (2, 1.0))
        self.assertEqual((capacity["deskslim-1"], busy["deskslim-1"]), (0, 0.0))  # unread: a 0 row, not none
        self.assertEqual((backlog, drain), ({"slot": 2.0}, 300.0))  # nothing waits for a local seat; drain the pool's
        text = governor.render(LIVE, {"disk": {}}, {}, 0, [], True, in_units=(capacity, busy, backlog, drain))
        self.assertIn('ci_governor_capacity_units{host="mac-mini"} 2', text)
        self.assertIn('ci_governor_busy_units{host="mac-mini"} 1.000', text)

    def test_a_waiting_local_run_is_local_backlog(self):
        out = governor.every_machine(LIVE, ({}, {}, {}, 0.0), ({}, {}), ({CX: 3}, {CX: 3.0}, 2))
        self.assertEqual(out[2], {"local": 2.0})
        self.assertEqual(out[3], 0.0)

    def test_local_seats_on_a_host_with_no_lan_unity_slot_fail(self):
        topo = {"hosts": {"m": {"site": "local", "local_seats": 2, "slots": [{"name": "m-mac", "caps": ["macos"]}]}}}
        with self.assertRaisesRegex(ValueError, "local seats on no LAN Unity host: m"):
            load_files({**live_config(), "hosts": {}}, topo)


class LeaseEndpointTest(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        import threading
        server = ThreadingHTTPServer(("127.0.0.1", 0), governor.Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(self, path):
        try:
            with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            e.close()
            return e.code, ""

    def test_a_local_seat_asks_holds_and_gives_back_over_lease(self):
        local = governor.LocalSeats(LOCAL)
        with mock.patch.object(governor.Handler, "local", local):
            self.assertEqual(self.get("/lease?seat=ws-local-9&have=github&wait=0")[0], 404)
            self.assertEqual(self.get("/lease?seat=ws-local-1&have=forgejo&wait=0")[0], 400)
            self.assertEqual(self.get("/lease?seat=ws-local-1&have=github&wait=0&task=5")[0], 400)
            self.assertEqual(self.get("/lease?seat=ws-local-1&have=github&wait=0"), (200, "local\n"))
            self.assertEqual(self.get("/lease?seat=ws-local-2&have=github&wait=0"), (200, "github\n"))
            self.assertEqual(self.get("/lease?seat=ws-local-1&have=local&wait=0"), (200, "local\n"))
            self.assertEqual(self.get("/lease?seat=ws-local-1&have=done&wait=0"), (200, "github\n"))
            self.assertEqual(self.get("/lease?seat=ws-local-2&have=github&wait=0"), (200, "local\n"))

    def test_a_forgejo_seat_names_its_task_and_tasks_serves_it_until_the_next_ask(self):
        leases = governor.Leases({FJ_SEAT: S1})
        leases.set(S1, "forgejo")
        self.assertEqual(self.get("/tasks"), (200, "{}"))  # no leases: nothing runs
        with mock.patch.object(governor.Handler, "leases", leases):
            self.assertEqual(self.get(f"/lease?seat={FJ_SEAT}&have=forgejo&wait=0&task=x1")[0], 400)
            self.assertEqual(self.get(f"/lease?seat={FJ_SEAT}&have=forgejo&wait=0&task=19311"), (200, "forgejo\n"))
            self.assertEqual(json.loads(self.get("/tasks")[1]), {FJ_SEAT: "19311"})
            self.get(f"/lease?seat={FJ_SEAT}&have=forgejo&wait=0")  # the job ended: its next ask names none
            self.assertEqual(json.loads(self.get("/tasks")[1]), {})
            # A task named while holding github is no job of its: it is not recorded.
            self.get(f"/lease?seat={FJ_SEAT}&have=github&wait=0&task=19312")
            self.assertEqual(json.loads(self.get("/tasks")[1]), {})


if __name__ == "__main__":
    unittest.main(verbosity=1)
