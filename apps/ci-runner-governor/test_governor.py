#!/usr/bin/env python3
"""Offline tests for governor.py: canned readings, no Prometheus, no GitHub.

Covers what a live night cannot show on demand: a hot host withholds its idle
runner first and its longest-running busy one next, a host with room re-admits
only while jobs wait, the pool floor and cap hold, a cooling host is left alone,
a missing reading is never room, a full disk empties a host past its floor and cooldown
but not the pool floor, a standby host takes only the jobs the others cannot, a powered
standby host is started and stopped by a fake EC2 (no network) and only it acts while the
rest is dry run, it is started only for a waiting job whose labels its runners carry, the
standby list starts Spot first and falls back to the next host on no capacity or a
reclaim but not on a budget deny, and a live action adds the new label before it removes the old. Stdlib
only; CI runs it in the checks workflow.
"""
import io
import json
import os
import sys
import time
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import governor  # noqa: E402

# The live thresholds with a fixed host list and the pool floor that roster was written
# for, so a change to the real roster (config.json "hosts", "standby", "pool_min_admitted")
# does not change what these tests mean.
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
LIVE = governor.load_config(CONFIG)
CFG = governor.with_defaults({**LIVE, "pool_min_admitted": 2, "standby": {"mac-studio": {"max_admitted": 6}}, "hosts": {
    "asus-laptop": {"min_admitted": 0, "admit": {"tctl": 80}, "withhold": {"idle": 0.25, "tctl": 90}},
    "hp-victus": {},
    "razer-laptop": {},
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


RUNNERS = pool(("razer-1", "razer-laptop", True, 900, False), ("razer-2", "razer-laptop", True, 100, False),
               ("razer-3", "razer-laptop", False, 0, False), ("asus-1", "asus-laptop", True, 300, False),
               ("hpv-1", "hp-victus", True, 50, False), ("hpv-2", "hp-victus", False, 0, True))


UNITY_JOB = frozenset({"self-hosted", "unity"})
LAN_JOB = frozenset({"self-hosted", "unity", "lan"})


def decide(hosts, runners=RUNNERS, queue=5, last=None, cfg=CFG, instances=None, waiting=None, pins=None, restore=()):
    waiting = [UNITY_JOB] * queue if waiting is None else waiting
    return governor.decide(cfg, hosts, runners, waiting, last or {}, 10_000, instances, pins, restore)


def actions(*args, **kw):
    return {h: (a, r) for h, a, r, _, _ in decide(*args, **kw)}


# Starts on the first job past the others, at once: what these tests check is which jobs
# and slots count, not the cost gate. HEAVY below is the live gate (config.json).
POWER = {"provider": "aws", "instance_id": "i-0abc", "region": "us-east-2", "idle_off_seconds": 1200, "live": True,
         "start_min_excess": 1, "start_after_seconds": 0}
AWS = {**CFG, "standby": {"aws-burst-1": {"max_admitted": 8, "power": POWER}}}
LIVE_POWER = {**governor.POWER_DEFAULTS, **LIVE["standby"]["aws-burst-1"]["power"]}
HEAVY = {**CFG, "standby": {"aws-burst-1": {"max_admitted": 6, "power": {**LIVE_POWER, "instance_id": "i-0abc"}}}}


# What host_labels() reads for aws-burst-1: its runners' labels (off the LAN, so no `lan`)
# plus the pool labels.
AWS_LABELS = frozenset({"self-hosted", "linux", "x64", "host-aws-burst-1", "unity-withheld", "unity", "unity-full"})


def instance(state, idle=0, labels=AWS_LABELS, **more):
    return {"aws-burst-1": {"state": state, "busy_at": 10_000 - idle, "labels": labels, **more}}


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


RESTORED = governor.restored


def setUpModule():
    # No test reads Prometheus: a first sight restores nothing unless a test says so.
    p = mock.patch.object(governor, "restored", return_value=(0.0, "", False))
    p.start()
    unittest.addModuleCleanup(p.stop)


class GovernorTest(unittest.TestCase):
    def test_hot_host_withholds_its_idle_runner_first(self):
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": BUSY}))
        self.assertEqual(got["razer-laptop"], ("withhold", "razer-3"))

    def test_then_the_busy_runner_nearest_its_end(self):
        runners = {n: r for n, r in RUNNERS.items() if n != "razer-3"}
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": BUSY}), runners)
        self.assertEqual(got["razer-laptop"], ("withhold", "razer-1"))

    def test_room_readmits_only_while_jobs_wait(self):
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": COOL})
        self.assertEqual(actions(hosts)["hp-victus"], ("admit", "hpv-2"))
        self.assertEqual(actions(hosts, queue=0)["hp-victus"], ("hold", ""))

    def test_host_floor(self):
        runners = pool(("razer-1", "razer-laptop", True, 9, False), ("asus-1", "asus-laptop", True, 9, False),
                       ("hpv-1", "hp-victus", True, 9, False))
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": HOT, "hp-victus": BUSY}), runners)
        self.assertEqual(got["razer-laptop"], ("hold", ""))   # razer's floor is 1
        self.assertEqual(got["asus-laptop"], ("withhold", "asus-1"))  # asus's is 0

    def test_pool_floor_keeps_the_watch_green(self):
        runners = pool(("asus-1", "asus-laptop", True, 9, False), ("razer-1", "razer-laptop", True, 9, False),
                       ("razer-2", "razer-laptop", False, 0, True), ("hpv-1", "hp-victus", True, 9, True))
        got = actions(readings(**{"razer-laptop": BUSY, "asus-laptop": HOT, "hp-victus": BUSY}), runners)
        self.assertEqual(got["asus-laptop"], ("hold", ""))

    def test_pool_cap(self):
        cfg = {**CFG, "pool_max_admitted": 5}
        got = actions(readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": COOL}), cfg=cfg)
        self.assertEqual(got["hp-victus"], ("hold", ""))

    def test_a_withhold_makes_room_under_the_cap(self):
        cfg = {**CFG, "pool_max_admitted": 5}
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": COOL}), cfg=cfg)
        self.assertEqual(got["razer-laptop"], ("withhold", "razer-3"))
        self.assertEqual(got["hp-victus"], ("admit", "hpv-2"))

    def test_cooling_host_is_left_alone(self):
        got = actions(readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": BUSY}),
                      last={"razer-laptop": 10_000 - 60})
        self.assertEqual(got["razer-laptop"], ("hold", ""))

    def test_a_missing_reading_is_never_room(self):
        got = actions(readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": {"tctl": 50}}))
        self.assertEqual(got["hp-victus"], ("hold", ""))

    def test_full_disk_takes_the_hosts_last_slot(self):
        got = actions(readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": FULL}),
                      pool(("hpv-1", "hp-victus", True, 9, False), ("razer-1", "razer-laptop", True, 9, False),
                           ("razer-2", "razer-laptop", True, 9, False), ("asus-1", "asus-laptop", True, 9, False)))
        self.assertEqual(got["hp-victus"], ("withhold", "hpv-1"))  # hp-victus's floor is 1

    def test_full_disk_does_not_wait_for_the_cooldown(self):
        got = actions(readings(**{"razer-laptop": FULL, "asus-laptop": BUSY, "hp-victus": BUSY}),
                      last={"razer-laptop": 10_000 - 60})
        self.assertEqual(got["razer-laptop"], ("withhold", "razer-3"))

    def test_full_disk_stops_at_the_pool_floor(self):
        runners = pool(("razer-1", "razer-laptop", True, 9, False), ("hpv-1", "hp-victus", True, 9, False))
        got = actions(readings(**{"razer-laptop": FULL, "asus-laptop": BUSY, "hp-victus": FULL}), runners)
        self.assertEqual(got["razer-laptop"], ("hold", ""))
        self.assertEqual(got["hp-victus"], ("hold", ""))

    def test_full_disk_counts_with_cpu_readings_missing(self):
        self.assertEqual(governor.verdict({"disk": 0.19}, CFG["hosts"]["razer-laptop"])[0], "FULL")

    def test_disk_between_the_lines_withholds_nothing_and_admits_nothing(self):
        between = {**COOL, "disk": 0.22}
        self.assertEqual(governor.verdict(between, CFG["hosts"]["hp-victus"])[0], "STEADY")
        got = actions(readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": between}))
        self.assertEqual(got["hp-victus"], ("hold", ""))

    def test_a_missing_disk_reading_is_never_room(self):
        self.assertEqual(governor.verdict({k: v for k, v in COOL.items() if k != "disk"},
                                          CFG["hosts"]["hp-victus"])[0], "STEADY")

    def test_asus_runs_hot_sooner(self):
        warm = {**COOL, "tctl": 91}
        self.assertEqual(governor.verdict(warm, CFG["hosts"]["asus-laptop"])[0], "HOT")
        self.assertEqual(governor.verdict(warm, CFG["hosts"]["razer-laptop"])[0], "STEADY")

    def mac(self, on_busy=0, on_idle=0, off=2):
        specs = [(f"mac-{i}", "mac-studio", True, 100 - i, False) for i in range(on_busy)]
        specs += [(f"mac-idle-{i}", "mac-studio", False, 0, False) for i in range(on_idle)]
        specs += [(f"mac-off-{i}", "mac-studio", False, 0, True) for i in range(off)]
        return pool(*specs)

    FULL = pool(("razer-1", "razer-laptop", True, 900, False), ("hpv-1", "hp-victus", True, 50, False))
    STEADY = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
    CAPPED = {**CFG, "standby": {"mac-studio": {"max_admitted": 6}}}

    def test_standby_stays_out_while_idle_slots_cover_the_queue(self):
        runners = {**self.mac(), **RUNNERS}  # razer-3 is admitted and idle
        self.assertEqual(actions(self.STEADY, runners, queue=1)["mac-studio"], ("hold", ""))

    def test_standby_takes_one_when_more_jobs_wait_than_idle_slots(self):
        runners = {**self.mac(), **RUNNERS}  # razer-3 is admitted and idle
        self.assertEqual(actions(self.STEADY, runners, queue=9)["mac-studio"], ("admit", "mac-off-0"))

    def test_standby_takes_one_when_the_other_hosts_are_full(self):
        runners = {**self.mac(), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners)["mac-studio"], ("admit", "mac-off-0"))

    def test_standby_waits_for_a_host_with_room_to_admit_first(self):
        runners = {**self.mac(), **self.FULL, "hpv-2": RUNNERS["hpv-2"]}
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": COOL})
        got = actions(hosts, runners, queue=1)
        self.assertEqual(got["hp-victus"], ("admit", "hpv-2"))
        self.assertEqual(got["mac-studio"], ("hold", ""))

    def test_standby_counts_an_idle_slot_and_an_admit_together(self):
        runners = {**self.mac(), **RUNNERS}  # razer-3 is idle, hpv-2 comes in this cycle
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": COOL})
        self.assertEqual(actions(hosts, runners, queue=2)["mac-studio"], ("hold", ""))
        got = actions(hosts, runners, queue=3)
        self.assertEqual(got["hp-victus"], ("admit", "hpv-2"))
        self.assertEqual(got["mac-studio"], ("admit", "mac-off-0"))

    def test_standby_never_passes_its_own_cap(self):
        runners = {**self.mac(on_busy=6), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, cfg=self.CAPPED)["mac-studio"], ("hold", ""))

    def test_standby_over_its_cap_gives_one_back(self):
        runners = {**self.mac(on_busy=8, off=0), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, cfg=self.CAPPED)["mac-studio"], ("withhold", "mac-0"))

    def test_standby_respects_the_pool_cap(self):
        runners = {**self.mac(on_busy=1), **self.FULL}
        got = actions(self.STEADY, runners, cfg={**CFG, "pool_max_admitted": 3})
        self.assertEqual(got["mac-studio"], ("hold", ""))

    def test_standby_goes_back_when_nothing_waits(self):
        runners = {**self.mac(on_busy=1, on_idle=1), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, queue=0)["mac-studio"], ("withhold", "mac-idle-0"))

    def test_standby_leaves_a_busy_slot_when_nothing_waits(self):
        runners = {**self.mac(on_busy=1), **self.FULL}
        self.assertEqual(actions(self.STEADY, runners, queue=0)["mac-studio"], ("hold", ""))

    def test_standby_slots_do_not_hold_the_pool_floor(self):
        runners = {**self.mac(on_busy=4, off=0), "asus-1": RUNNERS["asus-1"], "razer-1": RUNNERS["razer-1"]}
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": HOT, "hp-victus": BUSY})
        self.assertEqual(actions(hosts, runners)["asus-laptop"], ("hold", ""))

    def aws(self, on_busy=0, on_idle=0, off=0):
        specs = [(f"aws-{i}", "aws-burst-1", True, 100 - i, False) for i in range(on_busy)]
        specs += [(f"aws-idle-{i}", "aws-burst-1", False, 0, False) for i in range(on_idle)]
        specs += [(f"aws-off-{i}", "aws-burst-1", False, 0, True) for i in range(off)]
        return pool(*specs)

    def test_power_on_when_jobs_wait_and_the_instance_is_stopped(self):
        got = actions(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped"))
        self.assertEqual(got["aws-burst-1"], ("start", ""))

    def test_no_power_on_while_idle_slots_cover_the_queue(self):
        got = actions(self.STEADY, RUNNERS, queue=1, cfg=AWS, instances=instance("stopped"))  # razer-3 is idle
        self.assertEqual(got["aws-burst-1"], ("hold", ""))

    def test_a_start_is_not_room(self):
        # mac-studio sorts after aws-burst-1: it still admits, since a start covers no job.
        cfg = {**CFG, "standby": {**AWS["standby"], "mac-studio": {"max_admitted": 6}}}
        got = actions(self.STEADY, {**self.mac(), **self.FULL}, queue=1, cfg=cfg, instances=instance("stopped"))
        self.assertEqual(got["aws-burst-1"], ("start", ""))
        self.assertEqual(got["mac-studio"], ("admit", "mac-off-0"))

    def test_online_slots_come_in_one_per_cycle(self):
        got = actions(self.STEADY, {**self.aws(off=8), **self.FULL}, cfg=AWS, instances=instance("running"))
        self.assertEqual(got["aws-burst-1"], ("admit", "aws-off-0"))

    def test_stop_after_idle_off_seconds(self):
        withheld = {**self.aws(off=8), **RUNNERS}  # razer-3 covers the one job
        for idle, want in ((1199, ("hold", "")), (1200, ("stop", ""))):
            got = actions(self.STEADY, withheld, queue=1, cfg=AWS, instances=instance("running", idle))
            self.assertEqual(got["aws-burst-1"], want)
        # Its slots go first, even with jobs waiting that it has not taken in that time.
        got = actions(self.STEADY, {**self.aws(on_idle=1, off=7), **self.FULL}, queue=9, cfg=AWS,
                      instances=instance("running", 1200))
        self.assertEqual(got["aws-burst-1"], ("withhold", "aws-idle-0"))

    def test_a_lan_job_alone_does_not_start_an_off_lan_instance(self):
        # It would start, sit idle for idle_off_seconds, stop and start again, forever.
        (got,) = [d for d in decide(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped"),
                                    waiting=[LAN_JOB] * 3) if d[0] == "aws-burst-1"]
        self.assertEqual(got[1], "hold")
        self.assertEqual(got[4], "3 jobs wait, none it can take")

    def test_a_job_it_can_take_starts_it_beside_lan_jobs(self):
        got = actions(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped"),
                      waiting=[LAN_JOB, frozenset({"self-hosted", "unity-full", "linux"})])
        self.assertEqual(got["aws-burst-1"], ("start", ""))

    def test_a_withheld_only_host_still_takes_a_unity_job(self):
        # Stopped, so every runner is offline, and the governor withheld all of them.
        rows = [{"metric": {"name": f"aws-{i}", "host": "aws-burst-1", "pool": "unity", "state": "offline",
                            "labels": "host-aws-burst-1,Linux,self-hosted,unity-withheld,X64"}, "value": [0, "0"]}
                for i in range(2)]
        with mock.patch.object(governor, "prom", return_value=rows) as prom:
            labels = governor.host_labels(AWS, "aws-burst-1")
        self.assertIn('host="aws-burst-1"', prom.call_args.args[0])
        self.assertNotIn("state=", prom.call_args.args[0])
        got = actions(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped", labels=labels), queue=1)
        self.assertEqual(got["aws-burst-1"], ("start", ""))
        self.assertEqual(actions(self.STEADY, self.FULL, cfg=AWS, instances=instance("stopped", labels=labels),
                                 waiting=[LAN_JOB])["aws-burst-1"], ("hold", ""))

    def test_no_runner_labels_read_never_starts(self):
        with mock.patch.object(governor, "prom", return_value=[]):
            self.assertEqual(governor.host_labels(AWS, "aws-burst-1"), frozenset())
        ec2 = FakeEc2("stopped")
        _, log, _ = self.power_cycle(ec2, cycles=2, labels=frozenset())
        self.assertNotIn(("start", "i-0abc"), ec2.calls)
        self.assertEqual(len([line for line in log if "no runner labels" in line]), 1)
        self.assertIn('ci_governor_decision{host="aws-burst-1",verdict="STANDBY",action="hold"} 1',
                      governor.State.metrics)

    def test_power_on_never_past_the_pool_cap(self):
        got = actions(self.STEADY, self.FULL, cfg={**AWS, "pool_max_admitted": 2}, instances=instance("stopped"))
        self.assertEqual(got["aws-burst-1"], ("hold", ""))

    # The cost gate (config.json's live values): a start costs money, so only a deep queue,
    # held, starts it; a day's running time is capped; the Infra page's override beats both.
    def heavy(self, inst, queue=5, runners=None, waiting=None):
        got = {h: (a, r, why) for h, a, r, _, why in decide(self.STEADY, self.FULL if runners is None else runners,
                                                         queue=queue, cfg=HEAVY, instances=inst, waiting=waiting)}
        return got["aws-burst-1"]

    def test_live_gate_values(self):
        self.assertEqual({k: LIVE_POWER[k] for k in governor.POWER_DEFAULTS},
                         {"idle_off_seconds": 900, "start_min_excess": 4, "start_after_seconds": 300,
                          "max_hours_day": 12, "fallback_seconds": 3600})

    def test_no_start_below_the_excess(self):
        inst = instance("stopped", want_since=1)
        action, _, why = self.heavy(inst, queue=3)
        self.assertEqual(action, "hold")
        self.assertIn("starts at 4", why)
        self.assertIsNone(inst["aws-burst-1"]["want_since"])

    def test_the_excess_counts_only_jobs_it_can_take(self):
        waiting = [LAN_JOB] * 6 + [UNITY_JOB] * 3
        self.assertEqual(self.heavy(instance("stopped", want_since=1), waiting=waiting)[0], "hold")

    def test_no_start_before_the_hold_time_then_a_start(self):
        inst = instance("stopped")
        action, _, why = self.heavy(inst)
        self.assertEqual((action, inst["aws-burst-1"]["want_since"]), ("hold", 10_000))
        self.assertIn("held 0s of 300s", why)
        inst["aws-burst-1"]["want_since"] = 10_000 - 299
        self.assertEqual(self.heavy(inst)[0], "hold")
        inst["aws-burst-1"]["want_since"] = 10_000 - 300
        self.assertEqual(self.heavy(inst)[0], "start")
        self.assertEqual(inst["aws-burst-1"]["next_cause"], "queue")

    def test_a_dip_below_the_excess_restarts_the_hold(self):
        inst = instance("stopped", want_since=1)
        self.heavy(inst, queue=2)
        self.assertEqual(self.heavy(inst)[0], "hold")
        self.assertEqual(inst["aws-burst-1"]["want_since"], 10_000)

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
        self.assertEqual(inst["aws-burst-1"]["next_cause"], "cap")
        self.assertTrue(inst["aws-burst-1"]["draining"])

    def test_idle_off_is_900s_live(self):
        runners = {**self.aws(off=6), **RUNNERS}
        self.assertEqual(self.heavy(instance("running", 899), runners=runners, queue=1)[0], "hold")
        self.assertEqual(self.heavy(instance("running", 900), runners=runners, queue=1)[0], "stop")

    def test_override_on_starts_with_nothing_waiting(self):
        inst = instance("stopped", override="on", hours_today=13.0)
        self.assertEqual(self.heavy(inst, queue=0)[:2], ("start", ""))
        self.assertEqual(inst["aws-burst-1"]["next_cause"], "override")

    def test_override_on_survives_idle_and_the_cap(self):
        runners = {**self.aws(on_idle=1, off=5), **self.FULL}
        inst = instance("running", 5000, override="on", hours_today=13.0)
        self.assertEqual(self.heavy(inst, runners=runners, queue=0)[:2], ("admit", "aws-off-0"))
        full = {**self.aws(on_idle=6), **self.FULL}
        self.assertEqual(self.heavy(inst, runners=full, queue=0)[0], "hold")

    def test_override_off_drains_then_stops(self):
        inst = instance("running", override="off")
        busy = {**self.aws(on_busy=2, off=4), **self.FULL}
        self.assertEqual(self.heavy(inst, runners=busy, queue=9)[:2], ("withhold", "aws-0"))
        self.assertEqual(self.heavy(inst, runners={**self.aws(off=6), **self.FULL}, queue=9)[0], "stop")
        self.assertEqual(inst["aws-burst-1"]["next_cause"], "override")

    def test_override_off_blocks_a_start(self):
        action, _, why = self.heavy(instance("stopped", want_since=1, override="off"), queue=20)
        self.assertEqual((action, why), ("hold", "override off"))

    def test_read_override(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d), \
                mock.patch("builtins.print") as out:
            self.assertEqual(governor.read_override("aws-burst-1", 100), ("", 0))  # no ConfigMap
            path = os.path.join(d, "aws-burst-1")
            for body, want in (('{"action": "on", "until": 200}', ("on", 200)),
                               ('{"action": "off", "until": 200}', ("off", 200)),
                               ('{"action": "on", "until": 50}', ("", 0)),     # ran out
                               ('{"action": "start", "until": 200}', ("", 0)),  # not an action
                               ("", ("", 0)), ("{}", ("", 0)), ("not json", ("", 0)), ("[1]", ("", 0))):
                with open(path, "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_override("aws-burst-1", 100), want, body)
        self.assertEqual(len([c for c in out.call_args_list if "unreadable" in c.args[0]]), 2)

    # The human's machine-mode: a pinned host goes to its slots whatever the readings say.
    PINNED = pool(("hpv-1", "hp-victus", True, 50, False), ("hpv-2", "hp-victus", False, 0, False),
                  ("razer-1", "razer-laptop", False, 0, False), ("razer-2", "razer-laptop", False, 0, False),
                  ("asus-1", "asus-laptop", False, 0, False))
    PIN_HOSTS = readings(**{"razer-laptop": COOL, "asus-laptop": COOL, "hp-victus": COOL})

    def test_pin_withholds_the_idle_slot_first_on_a_cool_host(self):
        self.assertEqual(actions(self.PIN_HOSTS, self.PINNED, pins={"hp-victus": 1})["hp-victus"],
                         ("withhold", "hpv-2"))

    def test_pin_withholds_a_busy_slot_past_the_cooldown(self):
        runners = {**self.PINNED, "hpv-2": {**self.PINNED["hpv-2"], "withheld": True}}
        self.assertEqual(actions(self.PIN_HOSTS, runners, pins={"hp-victus": 0}, last={"hp-victus": 9_999})["hp-victus"],
                         ("withhold", "hpv-1"))

    def test_pin_admits_with_no_queue_on_a_hot_host(self):
        runners = {**self.PINNED, "hpv-2": {**self.PINNED["hpv-2"], "withheld": True}}
        hosts = readings(**{"razer-laptop": COOL, "asus-laptop": COOL, "hp-victus": HOT})
        self.assertEqual(actions(hosts, runners, queue=0, pins={"hp-victus": 2})["hp-victus"], ("admit", "hpv-2"))

    def test_pin_met_holds_even_when_hot(self):
        hosts = readings(**{"razer-laptop": COOL, "asus-laptop": COOL, "hp-victus": HOT})
        self.assertEqual(actions(hosts, self.PINNED, pins={"hp-victus": 2})["hp-victus"], ("hold", ""))

    def test_pin_stops_at_the_pool_floor(self):
        runners = pool(("hpv-1", "hp-victus", False, 0, False), ("hpv-2", "hp-victus", False, 0, False))
        out = {h: (a, w) for h, a, _, _, w in decide(self.PIN_HOSTS, runners, pins={"hp-victus": 0})}
        self.assertEqual(out["hp-victus"][0], "hold")
        self.assertIn("at the pool floor", out["hp-victus"][1])

    def test_pin_stops_at_the_pool_cap(self):
        runners = {**self.PINNED, "hpv-2": {**self.PINNED["hpv-2"], "withheld": True}}
        cfg = {**CFG, "pool_max_admitted": 4}
        self.assertEqual(actions(self.PIN_HOSTS, runners, cfg=cfg, pins={"hp-victus": 2})["hp-victus"], ("hold", ""))

    def test_a_full_host_keeps_its_verdict_over_a_pin(self):
        hosts = readings(**{"razer-laptop": COOL, "asus-laptop": COOL, "hp-victus": FULL})
        out = decide(hosts, self.PINNED, pins={"hp-victus": 2})
        self.assertEqual([(a, v) for h, a, _, v, _ in out if h == "hp-victus"], [("withhold", "FULL")])

    def test_a_pin_withholds_on_a_full_host(self):
        # A release reservation (pin 0) on a full razer: the withhold is the pin's, so a dry
        # run applies it; a FULL withhold there would only log.
        hosts = readings(**{"razer-laptop": COOL, "asus-laptop": COOL, "hp-victus": FULL})
        out = decide(hosts, self.PINNED, pins={"hp-victus": 0})
        self.assertEqual([(a, r, v) for h, a, r, v, _ in out if h == "hp-victus"], [("withhold", "hpv-2", "PINNED")])

    def test_an_unpinned_host_is_unchanged(self):
        hosts = readings(**{"razer-laptop": HOT, "asus-laptop": COOL, "hp-victus": COOL})
        self.assertEqual(actions(hosts, self.PINNED, pins={"hp-victus": 2})["razer-laptop"], ("withhold", "razer-1"))

    # A dry run applies nothing but pins, so once no pin holds a host it gets its slots back.
    WITHHELD = {**PINNED, "razer-1": {**PINNED["razer-1"], "withheld": True},
                "razer-2": {**PINNED["razer-2"], "withheld": True}}

    def test_a_dry_run_restores_an_unpinned_hosts_withheld_slot_on_any_verdict_but_full(self):
        for reading in (COOL, HOT):
            hosts = readings(**{"razer-laptop": reading, "asus-laptop": COOL, "hp-victus": COOL})
            out = decide(hosts, self.WITHHELD, queue=0, last={"razer-laptop": 9_999}, restore={"razer-laptop"})
            self.assertEqual([(a, r, v) for h, a, r, v, _ in out if h == "razer-laptop"],
                             [("admit", "razer-1", "RESTORE")], reading)
        hosts = readings(**{"razer-laptop": FULL, "asus-laptop": COOL, "hp-victus": COOL})
        self.assertEqual(actions(hosts, self.WITHHELD, queue=0, restore={"razer-laptop"})["razer-laptop"], ("hold", ""))

    def test_a_pin_holds_over_a_restore(self):
        self.assertEqual(actions(self.PIN_HOSTS, self.WITHHELD, queue=0, pins={"razer-laptop": 0},
                                 restore={"razer-laptop"})["razer-laptop"], ("hold", ""))

    def test_a_live_host_is_not_restored(self):
        hosts = readings(**{"razer-laptop": HOT, "asus-laptop": COOL, "hp-victus": COOL})
        self.assertEqual(actions(hosts, self.WITHHELD, queue=0)["razer-laptop"], ("hold", ""))

    def test_read_pins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d), \
                mock.patch("builtins.print") as out:
            self.assertEqual(governor.read_pins(CFG, 1000), {})  # no ConfigMap
            path = os.path.join(d, "hp-victus")
            for body, want in (('{"slots": 1}', {"hp-victus": 1}), ('{"slots": 0}', {"hp-victus": 0}),
                               ('{"slots": -1}', {}), ('{"slots": "2"}', {}), ('{"slots": true}', {}),
                               ('{"slots": 1.5}', {}), ("{}", {}), ("", {}), ("not json", {}), ("[1]", {})):
                with open(path, "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_pins(CFG, 1000), want, body)
            # A release reservation: its own key, a pin to 0 until its end, over the host's own pin.
            with open(path, "w") as f:
                f.write('{"slots": 2, "action": "off", "until": 5}')
            for body, want in (('{"until": 2000}', 0), ('{"until": 1000.5}', 0), ('{"until": 1000}', 2),
                               ('{"until": 500}', 2), ('{"until": "2000"}', 2), ('{"until": true}', 2),
                               ("{}", 2), ("", 2)):
                with open(path + ".reserve", "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_pins(CFG, 1000), {"hp-victus": want}, body)
        lines = [c.args[0] for c in out.call_args_list]
        self.assertEqual(len([l for l in lines if "pin unreadable" in l]), 4)
        self.assertEqual(len([l for l in lines if "reservation unreadable" in l]), 2)
        self.assertEqual(len([l for l in lines if "override unreadable" in l]), 2)

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
        last = [{"metric": {"host": "aws-burst-1", "state": "running", "reason": "queue"}, "value": [0, "1"]}]
        with mock.patch.object(governor, "prom", side_effect=[pods, last]) as prom:
            seconds, cause, up = RESTORED("aws-burst-1", day + 10 * 3600)
        self.assertAlmostEqual(seconds, 7.9 * 3600)
        self.assertEqual((cause, up), ("queue", True))
        self.assertIn(f"[{10 * 3600 - 2 * governor.CYCLE_SECONDS}s]", prom.call_args_list[0].args[0])
        with mock.patch.object(governor, "prom", side_effect=[[], []]):  # no cause on record
            self.assertEqual(RESTORED("aws-burst-1", day + 3600), (0.0, "", False))
        with mock.patch.object(governor, "prom") as prom:  # just past midnight, not up: ask again
            self.assertIsNone(RESTORED("aws-burst-1", day + 60))
        prom.assert_not_called()
        with mock.patch.object(governor, "prom", side_effect=urllib.error.URLError("down")), \
                mock.patch("builtins.print"):
            self.assertIsNone(RESTORED("aws-burst-1", day + 3600))

    def test_a_restart_finds_the_start_cause_since_the_launch_past_none_and_midnight(self):
        # hc-223: started by an override before midnight, then pods that exported "none".
        day = 1_762_214_400
        last = [{"metric": {"host": "aws-burst-1", "state": "running", "reason": "override"}, "value": [0, "1"]}]
        with mock.patch.object(governor, "prom", side_effect=[last]) as prom:
            back = RESTORED("aws-burst-1", day + 60, launched=day - 8 * 3600)
        self.assertEqual(back, (0.0, "override", True))
        query = prom.call_args.args[0]  # the day's hours are not read this close to midnight
        self.assertIn('reason!="none"', query)
        self.assertIn(f"[{8 * 3600 + 60 + 2 * governor.CYCLE_SECONDS}s:]", query)
        with mock.patch.object(governor, "prom", side_effect=[[], last]) as prom:  # restart 10s after a start
            self.assertEqual(RESTORED("aws-burst-1", day + 3600, launched=day + 3590), (0.0, "override", True))
        self.assertIn(f"[{10 + 2 * governor.CYCLE_SECONDS}s:]", prom.call_args.args[0])

    def test_a_stopped_host_reads_todays_stop_reason_not_back_to_its_old_launch(self):
        now = time.time()
        with mock.patch.object(governor, "restored", return_value=(0.0, "idle", False)) as back, \
                mock.patch.object(governor, "power_provider",
                                  return_value=(FakeEc2("stopped", launched=now - 21 * 86_400), "")), \
                mock.patch.object(governor, "host_labels", return_value=AWS_LABELS):
            t = {}
            governor.power_readings(AWS, {}, {"aws-burst-1": t}, now)
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
            governor.power_readings(AWS, {}, {"aws-burst-1": t}, now)
        self.assertEqual(t["cause"], "queue")
        with mock.patch.object(governor, "restored", return_value=(0.0, "idle", False)), \
                mock.patch.object(governor, "power_provider",
                                  return_value=(FakeEc2("running", launched=now - 30), "")), \
                mock.patch.object(governor, "host_labels", return_value=AWS_LABELS):
            t = {}
            governor.power_readings(AWS, {}, {"aws-burst-1": t}, now)
        self.assertNotIn("cause", t)  # the stop before this launch is not why it started

    def test_a_first_sight_keeps_the_larger_day_and_a_cause_that_matches_the_state(self):
        def first_sight(state, back, launched=0.0, now=time.time()):
            t = {}
            with mock.patch.object(governor, "restored", return_value=back), \
                    mock.patch.object(governor, "power_provider", return_value=(FakeEc2(state, launched=launched), "")), \
                    mock.patch.object(governor, "host_labels", return_value=AWS_LABELS):
                governor.power_readings(AWS, {}, {"aws-burst-1": t}, now)
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
                governor.power_readings(AWS, {}, {"aws-burst-1": t}, time.time())
        self.assertEqual(back.call_count, 2)  # unread, then read once and never again
        self.assertEqual((t["cause"], round(t["hours_today"])), ("queue", 3))

    def test_power_metrics_after_a_live_stop(self):
        ec2 = FakeEc2("running")
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
        tracked = {"aws-burst-1": {"busy_at": 0, "day": time.strftime("%Y-%m-%d", time.gmtime()), "run_seconds": 7200.0}}
        self.power_cycle(ec2, observed=(hosts, {**self.aws(off=8), **self.FULL}, []), tracked=tracked)
        self.assertIn(("stop", "i-0abc"), ec2.calls)
        m = governor.State.metrics
        self.assertIn('ci_governor_power_state{host="aws-burst-1",state="draining",reason="idle"} 1', m)
        self.assertIn('ci_governor_power_hours_today{host="aws-burst-1"} 2.0', m)
        self.assertIn('ci_governor_power_max_hours_day{host="aws-burst-1"} 12', m)
        self.assertIn('ci_governor_power_override{host="aws-burst-1",action="none"} 0', m)

    def test_a_failed_start_keeps_the_old_reason(self):
        tracked = {}
        self.power_cycle(FakeEc2("stopped", fail=True), tracked=tracked)
        self.assertIn('ci_governor_power_state{host="aws-burst-1",state="stopped",reason="none"} 1',
                      governor.State.metrics)
        self.power_cycle(FakeEc2("stopped"), tracked=tracked)
        self.assertEqual(tracked["aws-burst-1"]["cause"], "queue")

    def test_a_dry_run_power_host_reports_its_state(self):
        cfg = {**AWS, "standby": {"aws-burst-1": {"max_admitted": 8, "power": {**POWER, "live": False}}}}
        self.power_cycle(FakeEc2("pending"), cfg)
        self.assertIn('ci_governor_power_state{host="aws-burst-1",state="starting",reason="none"} 1',
                      governor.State.metrics)

    def power_cycle(self, ec2, cfg=AWS, observed=None, cycles=1, tracked=None, labels=AWS_LABELS, budget=None):
        hosts = readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": BUSY})
        observed = observed or (hosts, dict(self.FULL, **{"razer-3": RUNNERS["razer-3"]}), [UNITY_JOB] * 9)
        last, tracked = {}, {} if tracked is None else tracked
        with mock.patch.object(governor, "observe", return_value=observed), \
                mock.patch.object(governor, "power_provider", return_value=(ec2, "")), \
                mock.patch.object(governor, "host_labels", return_value=labels), \
                mock.patch.object(governor, "apply") as apply, mock.patch("builtins.print") as out:
            for _ in range(cycles):
                governor.cycle(cfg, last, tracked, budget)
        return apply, [c.args[0] for c in out.call_args_list], last

    def test_a_live_power_host_acts_and_other_hosts_stay_dry_run(self):
        ec2 = FakeEc2("stopped")
        apply, log, _ = self.power_cycle(ec2)
        self.assertIn(("start", "i-0abc"), ec2.calls)
        apply.assert_not_called()  # razer-laptop is HOT, but only "would withhold"
        self.assertTrue(any(line.startswith("would withhold razer-3 on razer-laptop") for line in log))

    def test_a_pin_acts_in_a_dry_run_and_verdicts_stay_dry(self):
        hosts = readings(**{"razer-laptop": HOT, "asus-laptop": COOL, "hp-victus": COOL})
        observed = (hosts, self.PINNED, [UNITY_JOB] * 2)
        with mock.patch.object(governor, "observe", return_value=observed), \
                mock.patch.object(governor, "read_pins", return_value={"hp-victus": 1}), \
                mock.patch.object(governor, "DRY_RUN", True), \
                mock.patch.object(governor, "apply") as apply, mock.patch("builtins.print") as out:
            governor.cycle(CFG, {})
        apply.assert_called_once_with(CFG, "withhold", "hpv-2", "hp-victus")
        log = [c.args[0] for c in out.call_args_list]
        self.assertTrue(any(line.startswith("withhold hpv-2 on hp-victus: PINNED") for line in log), log)
        self.assertTrue(any(line.startswith("would withhold razer-1 on razer-laptop") for line in log), log)

    def test_an_ended_reservation_is_restored_in_a_dry_run(self):
        hosts = readings(**{"razer-laptop": HOT, "asus-laptop": COOL, "hp-victus": HOT})
        observed = (hosts, self.WITHHELD, [])
        with mock.patch.object(governor, "observe", return_value=observed), \
                mock.patch.object(governor, "read_pins", return_value={}), \
                mock.patch.object(governor, "DRY_RUN", True), \
                mock.patch.object(governor, "apply") as apply, mock.patch("builtins.print") as out:
            governor.cycle(CFG, {})
        apply.assert_called_once_with(CFG, "admit", "razer-1", "razer-laptop")
        log = [c.args[0] for c in out.call_args_list]
        self.assertTrue(any(line.startswith("admit razer-1 on razer-laptop: RESTORE") for line in log), log)
        self.assertTrue(any(line.startswith("would withhold hpv-2 on hp-victus") for line in log), log)

    def test_a_power_block_not_live_stays_dry_run(self):
        ec2 = FakeEc2("stopped")
        cfg = {**AWS, "standby": {"aws-burst-1": {"max_admitted": 8, "power": {**POWER, "live": False}}}}
        _, log, _ = self.power_cycle(ec2, cfg)
        self.assertNotIn(("start", "i-0abc"), ec2.calls)
        self.assertTrue(any(line.startswith("would start i-0abc on aws-burst-1") for line in log))

    def test_a_failed_start_is_logged_and_retried_never_counted(self):
        ec2 = FakeEc2("stopped", fail=True)
        _, log, last = self.power_cycle(ec2, cycles=2)
        self.assertEqual([c for c in ec2.calls if c[0] == "start"], [("start", "i-0abc")] * 2)
        self.assertNotIn("aws-burst-1", last)
        self.assertTrue(any(line.startswith("start i-0abc failed") for line in log))
        self.assertIn('ci_governor_admitted{host="aws-burst-1"} 0', governor.State.metrics)

    def test_a_busy_slot_restarts_the_idle_clock(self):
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
        tracked = {"aws-burst-1": {"busy_at": 0}}  # idle for hours by the clock
        busy = (hosts, {**self.aws(on_busy=1, off=7), **self.FULL}, [])
        with mock.patch.object(governor.time, "time", return_value=10_000):
            self.power_cycle(FakeEc2("running"), observed=busy, tracked=tracked)
        self.assertEqual(tracked["aws-burst-1"]["busy_at"], 10_000)

    def test_no_credentials_no_aws_call(self):
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
        with mock.patch.object(governor, "AWS_KEY", ""), mock.patch.object(governor, "AWS_SECRET", ""), \
                mock.patch.object(governor, "observe", return_value=(hosts, self.FULL, [UNITY_JOB] * 9)), \
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
        body = "Action=DescribeInstances&Version=2016-11-15&InstanceId.1=i-049219a501808a9cf"
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
        self.assertTrue(any(line.startswith("start i-0abc on aws-burst-1") for line in log))

    def test_no_budget_block_no_budget_series(self):
        cfg = {k: v for k, v in AWS.items() if k != "budget"}
        self.power_cycle(FakeEc2("stopped"), cfg, budget={})
        self.assertNotIn("ci_governor_budget", governor.State.metrics)

    def test_rule_progress_while_idle(self):
        inst = instance("running", idle=600)
        decide(readings(), queue=0, cfg=HEAVY, instances=inst)
        self.assertEqual((inst["aws-burst-1"]["idle_seconds"], inst["aws-burst-1"]["want"]), (600, 0))

    def test_rule_progress_below_the_excess_and_while_held(self):
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
        inst = instance("stopped")
        decide(hosts, runners=self.FULL, queue=2, cfg=HEAVY, instances=inst)
        self.assertEqual((inst["aws-burst-1"]["want"], inst["aws-burst-1"]["held_seconds"]), (2, 0))
        inst["aws-burst-1"]["want_since"] = 10_000 - 120
        decide(hosts, runners=self.FULL, queue=9, cfg=HEAVY, instances=inst)
        self.assertEqual((inst["aws-burst-1"]["want"], inst["aws-burst-1"]["held_seconds"]), (9, 120))

    def test_rule_metrics_live_flags_and_last_change(self):
        cfg = {**AWS, "rates": {"aws-burst-1": {"usd_per_hour": 1.855, "basis": "on-demand"}}}
        tracked = {}
        with mock.patch.object(governor.time, "time", return_value=10_000):
            self.power_cycle(FakeEc2("stopped"), cfg, tracked=tracked)
        m = governor.State.metrics
        for line in ('ci_governor_power_idle_off_seconds{host="aws-burst-1"} 1200',
                     'ci_governor_power_start_min_excess{host="aws-burst-1"} 1',
                     'ci_governor_power_usd_per_hour{host="aws-burst-1"} 1.855',
                     'ci_governor_power_last_change{host="aws-burst-1",action="start"} 10000',
                     'ci_governor_live{host="aws-burst-1"} 1', 'ci_governor_live{host="razer-laptop"} 0'):
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
                return {"runners": [{"id": 7, "name": "hpv-2",
                                     "labels": [{"name": "self-hosted"}, {"name": "unity-withheld"}]}]}
            return None
        governor.gh = gh
        governor.apply(CFG, "admit", "hpv-2", "hp-victus")
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
                "aws-burst-1": {"state": burst, "busy_at": 10_000 - idle, "labels": AWS_LABELS}}

    def build(self, on_busy=0, on_idle=0, off=0):
        specs = [(f"bld-{i}", "aws-build-1", True, 100 - i, False) for i in range(on_busy)]
        specs += [(f"bld-idle-{i}", "aws-build-1", False, 0, False) for i in range(on_idle)]
        specs += [(f"bld-off-{i}", "aws-build-1", False, 0, True) for i in range(off)]
        return pool(*specs)

    def test_a_waiting_build_starts_the_build_host_only(self):
        # razer-3 is idle, which would cover one pool job: it covers no build.
        got = actions(self.STEADY, RUNNERS, cfg=self.BUILD, instances=self.both(), waiting=[self.BUILD_JOB])
        self.assertEqual(got["aws-build-1"], ("start", ""))
        self.assertEqual(got["aws-burst-1"], ("hold", ""))

    def test_gate_jobs_never_start_the_build_host(self):
        # Negative control: a full pool queue starts aws-burst-1, and aws-build-1, first in the
        # standby order, neither starts nor stands ahead of it.
        got = actions(self.STEADY, self.FULL, cfg=self.BUILD, instances=self.both(), queue=5)
        self.assertEqual(got["aws-build-1"], ("hold", ""))
        self.assertEqual(got["aws-burst-1"], ("start", ""))

    def test_a_build_does_not_admit_a_pool_slot(self):
        runners = {**self.aws(off=2), **self.FULL}
        got = actions(self.STEADY, runners, cfg=self.BUILD, instances=self.both(burst="running"),
                      waiting=[self.BUILD_JOB])
        self.assertEqual(got["aws-burst-1"], ("hold", ""))

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
        self.assertEqual(got["aws-burst-1"], ("admit", "aws-off-0"))

    def test_the_build_host_goes_back_when_no_build_waits(self):
        # Gate jobs wait, but none is the build host's.
        got = actions(self.STEADY, {**self.build(on_idle=1), **self.FULL}, cfg=self.BUILD,
                      instances=self.both(build="running"), queue=5)
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
        self.assertEqual(governor.pools(self.BUILD), ["unity", "unity-build"])
        with mock.patch.object(governor, "prom", return_value=[]) as prom:
            governor.host_labels(self.BUILD, "aws-build-1")
        self.assertIn('pool=~"unity|unity-build"', prom.call_args.args[0])

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

    def test_the_live_config_keeps_full_runs_off_the_workstation(self):
        cfg = governor.load_config(CONFIG)
        self.assertEqual(governor.admit_labels(cfg, "cachyos-x8664-main"), ["unity"])
        self.assertEqual(governor.admit_labels(cfg, "razer-laptop"), ["unity", "unity-full"])

    def test_disk_held_only_with_withheld_slots_under_the_admit_line(self):
        def gauge(disk, withheld):
            hosts = readings(**{"razer-laptop": {**COOL, "disk": disk}})
            runners = pool(("r-1", "razer-laptop", False, 0, withheld))
            return 'ci_governor_disk_held{host="razer-laptop"} 1' in governor.render(CFG, hosts, runners, 3, [], True)
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
                return [{"metric": {"scope": "org", "name": "r-1", "host": "razer-laptop", "state": "idle"},
                         "value": [0, "0"]}]
            if "github_actions_job_waiting" in query:
                job = {"metric": {"labels": "self-hosted,Unity"}, "value": [0, "60"]}
                return [job] if 'stuck!="true"' in query else [job] * 3
            return []
        with mock.patch.object(governor, "prom", side_effect=fake):
            _, _, waiting = governor.observe("unity")
        self.assertEqual(waiting, [UNITY_JOB])



# Two powered standby hosts in config order, Spot first, as config.json lists them.
SPOT = {**POWER, "instance_id": "i-0spot"}
TWO = {**CFG, "standby": {"aws-burst-2": {"max_admitted": 6, "power": SPOT},
                          "aws-burst-1": {"max_admitted": 6, "power": POWER}}}
SPOT_LABELS = frozenset(AWS_LABELS - {"host-aws-burst-1"} | {"host-aws-burst-2"})
STEADY = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
HOME_BUSY = pool(("razer-1", "razer-laptop", True, 900, False), ("hpv-1", "hp-victus", True, 50, False))


def two(spot="stopped", od="stopped", **spot_more):
    return {"aws-burst-2": {"state": spot, "busy_at": 10_000, "labels": SPOT_LABELS, **spot_more},
            "aws-burst-1": {"state": od, "busy_at": 10_000, "labels": AWS_LABELS}}


class StandbyOrderTest(unittest.TestCase):
    def got(self, queue, inst, runners=HOME_BUSY):
        return {h: (a, why) for h, a, _, _, why in decide(STEADY, runners, queue=queue, cfg=TWO, instances=inst)}

    def test_the_list_keeps_its_order(self):
        with mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(
                {**{k: v for k, v in LIVE.items() if k != "standby"}, "hosts": {},
                 "standby": [{"host": "b", "max_admitted": 1}, {"host": "a", "max_admitted": 2}]}))):
            cfg = governor.load_config("config.json")
        self.assertEqual(list(cfg["standby"]), ["b", "a"])
        self.assertEqual(cfg["standby"]["a"], {"max_admitted": 2})

    def test_spot_starts_first_and_on_demand_waits(self):
        got = self.got(6, two())
        self.assertEqual(got["aws-burst-2"][0], "start")
        self.assertEqual(got["aws-burst-1"][0], "hold")
        self.assertIn("aws-burst-2 ahead of it take 6", got["aws-burst-1"][1])

    def test_a_queue_past_the_first_host_starts_both(self):
        got = self.got(7, two())
        self.assertEqual((got["aws-burst-2"][0], got["aws-burst-1"][0]), ("start", "start"))

    def test_a_booting_spot_holds_the_next_back(self):
        # Running, runners not online yet: all six slots are still to come.
        self.assertEqual(self.got(6, two("running"))["aws-burst-1"][0], "hold")
        self.assertEqual(self.got(6, two("pending"))["aws-burst-1"][0], "hold")

    def test_a_full_spot_lets_the_next_start(self):
        spot_busy = pool(*[(f"spot-{i}", "aws-burst-2", True, 100, False) for i in range(6)])
        got = self.got(3, two("running"), {**HOME_BUSY, **spot_busy})
        self.assertEqual(got["aws-burst-1"][0], "start")

    def test_a_passed_over_spot_lets_the_next_start_at_once(self):
        got = self.got(2, two(fallback_until=10_001, fallback_why="capacity"))
        self.assertEqual(got["aws-burst-2"], ("hold", "passed over after a capacity for 1s more"))
        self.assertEqual(got["aws-burst-1"][0], "start")

    def test_the_pass_over_runs_out(self):
        got = self.got(2, two(fallback_until=10_000, fallback_why="capacity"))
        self.assertEqual((got["aws-burst-2"][0], got["aws-burst-1"][0]), ("start", "hold"))

    def test_override_on_beats_a_pass_over(self):
        got = self.got(0, two(fallback_until=20_000, fallback_why="reclaim", override="on"))
        self.assertEqual(got["aws-burst-2"][0], "start")

    def test_a_spot_told_off_or_past_its_day_is_not_ahead(self):
        self.assertEqual(self.got(2, two(override="off"))["aws-burst-1"][0], "start")
        self.assertEqual(self.got(2, two(hours_today=12))["aws-burst-1"][0], "start")

    def cycles(self, ec2, n=1, tracked=None, waiting=None):
        tracked = {} if tracked is None else tracked
        labels = {"aws-burst-2": SPOT_LABELS, "aws-burst-1": AWS_LABELS}
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
        self.assertEqual(tracked["aws-burst-2"]["fallback_why"], "capacity")
        self.assertIn("aws-burst-2: start capacity (InsufficientInstanceCapacity), passed over for the next standby host", log)
        self.cycles(ec2, tracked=tracked)
        self.assertEqual(self.starts(ec2), ["i-0spot", "i-0abc"])
        m = governor.State.metrics
        self.assertIn('ci_governor_fallback_total{host="aws-burst-2",reason="capacity"} 1', m)
        self.assertIn('ci_governor_fallback_total{host="aws-burst-1",reason="reclaim"} 0', m)
        self.assertIn(f'ci_governor_power_fallback_until{{host="aws-burst-2"}} '
                      f'{int(tracked["aws-burst-2"]["fallback_until"])}', m)

    def test_a_budget_deny_starts_nothing(self):
        # Negative control: the deny policy covers every host, so it is no fallback.
        ec2 = FakeEc2("stopped", fail={"i-0spot": "UnauthorizedOperation"})
        tracked, _ = self.cycles(ec2, n=3)
        self.assertEqual(self.starts(ec2), ["i-0spot"] * 3)
        self.assertNotIn("fallback_until", tracked["aws-burst-2"])
        self.assertIn('ci_governor_fallback_total{host="aws-burst-2",reason="capacity"} 0', governor.State.metrics)

    def test_a_refused_spot_start_falls_back_too(self):
        # A Spot-only refusal (price, request state, vCPU limit) must not starve the next host.
        ec2 = FakeEc2("stopped", fail={"i-0spot": "SpotMaxPriceTooLow"})
        tracked, _ = self.cycles(ec2, n=2)
        self.assertEqual(self.starts(ec2), ["i-0spot", "i-0abc"])
        self.assertEqual(tracked["aws-burst-2"]["fallbacks"], {"refused": 1})

    def test_a_start_that_aws_stops_again_falls_back(self):
        # A Spot start can succeed, then stop with no capacity after all.
        tracked, _ = self.cycles(FakeEc2({"i-0spot": "pending", "i-0abc": "stopped"}), waiting=[])
        ec2 = FakeEc2("stopped", reason={"i-0spot": "Server.InsufficientInstanceCapacity"})
        tracked, _ = self.cycles(ec2, tracked=tracked)
        self.assertEqual(self.starts(ec2), ["i-0abc"])
        self.assertEqual(tracked["aws-burst-2"]["fallback_why"], "reclaim")

    def test_a_spot_aws_restarted_in_its_pass_over_is_ahead(self):
        # A persistent Spot instance comes back by itself when capacity returns.
        got = self.got(6, two("running", fallback_until=20_000, fallback_why="reclaim"))
        self.assertEqual(got["aws-burst-1"][0], "hold")

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
        self.assertEqual(tracked["aws-burst-2"]["fallback_why"], "reclaim")
        m = governor.State.metrics
        self.assertIn('ci_governor_power_state{host="aws-burst-2",state="stopped",reason="reclaim"} 1', m)
        self.assertIn('ci_governor_fallback_total{host="aws-burst-2",reason="reclaim"} 1', m)
        # Still stopped by the reclaim next cycle: counted once.
        self.cycles(ec2, tracked=tracked)
        self.assertEqual(tracked["aws-burst-2"]["fallbacks"], {"reclaim": 1})

    def test_an_old_reclaim_on_first_sight_is_not_counted(self):
        ec2 = FakeEc2("stopped", reason={"i-0spot": "Server.SpotInstanceShutdown"})
        tracked, _ = self.cycles(ec2)
        self.assertEqual(self.starts(ec2), ["i-0spot"])
        self.assertNotIn("fallback_until", tracked["aws-burst-2"])

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

# A cloud agent box: no runners, powered on fleet-dispatch's demand key.
WORKER_POWER = {"provider": "aws", "instance_id": "i-0wrk", "region": "us-east-2", "idle_off_seconds": 1800,
                "start_min_excess": 1, "live": True}
WRK = {**CFG, "workers": {"fleet-worker-1": {"power": WORKER_POWER}}}


class WorkerTest(unittest.TestCase):
    def cycles(self, ec2, demand=None, tracked=None, n=1, override=None, cfg=WRK):
        """demand: N for a key that runs out in 10 minutes, or the raw key text."""
        import tempfile
        tracked = {} if tracked is None else tracked
        with tempfile.TemporaryDirectory() as d:
            if demand is not None:
                with open(os.path.join(d, "fleet-worker-1.demand"), "w") as f:
                    f.write(demand if isinstance(demand, str) else
                            json.dumps({"demand": demand, "until": time.time() + 600}))
            if override:
                with open(os.path.join(d, "fleet-worker-1"), "w") as f:
                    f.write(json.dumps({"action": override, "until": time.time() + 600}))
            with mock.patch.object(governor, "OVERRIDE_DIR", d), \
                    mock.patch.object(governor, "observe", return_value=(STEADY, HOME_BUSY, [])), \
                    mock.patch.object(governor, "power_provider", return_value=(ec2, "")), \
                    mock.patch.object(governor, "host_labels") as labels, \
                    mock.patch.object(governor, "apply"), mock.patch("builtins.print") as out:
                for _ in range(n):
                    governor.cycle(cfg, {}, tracked)
        labels.assert_not_called()  # a worker has no runners to read
        return tracked, [c.args[0] for c in out.call_args_list]

    def ops(self, ec2):
        return [(op, iid) for op, iid in ec2.calls if op != "state"]

    def test_load_config_reads_workers_and_defaults_to_none(self):
        body = {**{k: v for k, v in LIVE.items() if k not in ("standby", "workers")}, "hosts": {}, "standby": []}
        for extra, want in (({}, {}), ({"workers": [{"host": "w", "power": {"live": True}}]},
                                       {"w": {"power": {"live": True}}})):
            with mock.patch("builtins.open", mock.mock_open(read_data=json.dumps({**body, **extra}))):
                self.assertEqual(governor.load_config("config.json")["workers"], want)
        # One host in both lists would be started and stopped by two rules in one cycle.
        both = {**body, "standby": [{"host": "w"}], "workers": [{"host": "w"}]}
        with mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(both))), \
                self.assertRaisesRegex(ValueError, "in both standby and workers: w"):
            governor.load_config("config.json")

    def test_read_demand(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d), \
                mock.patch("builtins.print") as out:
            self.assertEqual(governor.read_demand("w", 100), 0)  # no key
            for body, want in (('{"demand": 2, "until": 200}', 2), ('{"demand": 0, "until": 200}', 0),
                               ('{"demand": 2, "until": 50}', 0),       # ran out
                               ('{"demand": 2.5, "until": 200}', 0),    # not a whole number
                               ('{"demand": -1, "until": 200}', 0), ('{"demand": true, "until": 200}', 0),
                               ('{"demand": 2}', 0), ("", 0), ("not json", 0)):
                with open(os.path.join(d, "w.demand"), "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_demand("w", 100), want, body)
        said = [c.args[0] for c in out.call_args_list]
        self.assertEqual(len([s for s in said if "demand unreadable" in s]), 4)
        self.assertEqual(len([s for s in said if "override unreadable" in s]), 1)  # "not json", by override_key

    def test_demand_starts_a_stopped_worker(self):
        ec2 = FakeEc2("stopped")
        tracked, log = self.cycles(ec2, demand=1)
        self.assertEqual(self.ops(ec2), [("start", "i-0wrk")])
        self.assertEqual(tracked["fleet-worker-1"]["cause"], "demand")
        self.assertIn("start i-0wrk on fleet-worker-1: WORKER, demand 1; queue 0", log)
        m = governor.State.metrics
        self.assertIn('ci_governor_power_demand{host="fleet-worker-1"} 1', m)
        self.assertNotIn('ci_governor_power_start_after_seconds{host="fleet-worker-1"}', m)
        self.assertIn('ci_governor_live{host="fleet-worker-1"} 1', m)
        self.assertIn('ci_governor_decision{host="fleet-worker-1",verdict="WORKER",action="start"} 1', m)
        self.assertNotIn('ci_governor_registered{host="fleet-worker-1"}', m)

    def test_no_demand_or_a_stale_key_starts_nothing(self):
        for demand in (None, 0, json.dumps({"demand": 3, "until": time.time() - 1})):
            ec2 = FakeEc2("stopped")
            self.cycles(ec2, demand=demand)
            self.assertEqual(self.ops(ec2), [], demand)

    def test_override_beats_demand(self):
        ec2 = FakeEc2("stopped")
        self.cycles(ec2, demand=2, override="off")
        self.assertEqual(self.ops(ec2), [])
        ec2 = FakeEc2("stopped")
        self.cycles(ec2, override="on")
        self.assertEqual(self.ops(ec2), [("start", "i-0wrk")])
        ec2 = FakeEc2("running")
        self.cycles(ec2, demand=2, override="off")
        self.assertEqual(self.ops(ec2), [("stop", "i-0wrk")])

    def test_idle_worker_stops_and_demand_keeps_it_up(self):
        old = lambda: {"fleet-worker-1": {"state": "running", "busy_at": time.time() - 1801}}
        ec2 = FakeEc2("running")
        tracked, _ = self.cycles(ec2, demand=1, tracked=old())
        self.assertEqual(self.ops(ec2), [])  # demand this cycle: busy now
        ec2 = FakeEc2("running")
        tracked, log = self.cycles(ec2, tracked=old())
        self.assertEqual(self.ops(ec2), [("stop", "i-0wrk")])
        self.assertEqual(tracked["fleet-worker-1"]["cause"], "idle")
        ec2 = FakeEc2("running")
        self.cycles(ec2, tracked={"fleet-worker-1": {"state": "running", "busy_at": time.time() - 600}})
        self.assertEqual(self.ops(ec2), [])  # idle, not long enough

    def test_the_day_cap_stops_it_and_holds_a_start(self):
        capped = lambda st: {"fleet-worker-1": {"state": st, "busy_at": time.time(), "day":
                                                time.strftime("%Y-%m-%d", time.gmtime()), "run_seconds": 12 * 3600}}
        ec2 = FakeEc2("running")
        tracked, _ = self.cycles(ec2, demand=1, tracked=capped("running"))
        self.assertEqual((self.ops(ec2), tracked["fleet-worker-1"]["cause"]), ([("stop", "i-0wrk")], "cap"))
        ec2 = FakeEc2("stopped")
        self.cycles(ec2, demand=1, tracked=capped("stopped"))
        self.assertEqual(self.ops(ec2), [])

    def test_a_refused_start_is_passed_over_not_retried(self):
        ec2 = FakeEc2("stopped", fail="InsufficientInstanceCapacity")
        tracked, _ = self.cycles(ec2, demand=1, n=2)
        self.assertEqual(self.ops(ec2), [("start", "i-0wrk")])
        self.assertEqual(tracked["fleet-worker-1"]["fallback_why"], "capacity")

    def test_dry_run_only_logs(self):
        ec2 = FakeEc2("stopped")
        cfg = {**WRK, "workers": {"fleet-worker-1": {"power": {**WORKER_POWER, "live": False}}}}
        with mock.patch.object(governor, "DRY_RUN", True):
            _, log = self.cycles(ec2, demand=1, cfg=cfg)
        self.assertEqual(self.ops(ec2), [])
        self.assertIn("would start i-0wrk on fleet-worker-1: WORKER, demand 1; queue 0", log)

    def test_the_ci_hosts_never_see_a_worker(self):
        inst = {"fleet-worker-1": {"state": "stopped", "busy_at": 10_000, "labels": set(), "demand": 5}}
        got = decide(STEADY, HOME_BUSY, queue=0, cfg=WRK, instances=inst)
        self.assertNotIn("fleet-worker-1", [h for h, *_ in got])



class RatesTest(unittest.TestCase):
    """config.json "rates": every host's $/h with its basis, in one place."""

    def test_every_rate_is_exported_with_its_basis(self):
        out = governor.render(LIVE, {"disk": {}}, {}, 0, [], True)
        for host, r in LIVE["rates"].items():
            self.assertIn(f'ci_governor_rate_usd_per_hour{{host="{host}",basis="{r["basis"]}"}} {r["usd_per_hour"]}', out)

    def test_a_rate_in_a_power_block_or_without_a_basis_is_refused(self):
        bad_power = {**LIVE, "standby": {"aws-burst-1": {"max_admitted": 5, "power": {**POWER, "usd_per_hour": 1}}}}
        with self.assertRaisesRegex(ValueError, "belongs in rates"):
            governor.check_rates(bad_power)
        with self.assertRaisesRegex(ValueError, "basis"):
            governor.check_rates({**LIVE, "rates": {"x": {"usd_per_hour": 1}}})

if __name__ == "__main__":
    unittest.main(verbosity=1)
