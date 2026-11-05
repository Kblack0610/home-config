#!/usr/bin/env python3
"""Offline tests for governor.py: canned readings, no Prometheus, no GitHub.

Covers what a live night cannot show on demand: a hot host withholds its idle
runner first and its longest-running busy one next, a host with room re-admits
only while jobs wait, the pool floor and cap hold, a cooling host is left alone,
a missing reading is never room, a full disk empties a host past its floor and cooldown
but not the pool floor, a standby host takes only the jobs the others cannot, a powered
standby host is started and stopped by a fake EC2 (no network) and only it acts while the
rest is dry run, it is started only for a waiting job whose labels its runners carry, and a live action adds the new label before it removes the old. Stdlib
only; CI runs it in the checks workflow.
"""
import json
import os
import sys
import time
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import governor  # noqa: E402

# The live thresholds with a fixed host list, so a change to the real roster
# (config.json "hosts" and "standby") does not change what these tests mean.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")) as f:
    LIVE = json.load(f)
CFG = governor.with_defaults({**LIVE, "standby": {"mac-studio": {"max_admitted": 6}}, "hosts": {
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


def decide(hosts, runners=RUNNERS, queue=5, last=None, cfg=CFG, instances=None, waiting=None, pins=None):
    waiting = [UNITY_JOB] * queue if waiting is None else waiting
    return governor.decide(cfg, hosts, runners, waiting, last or {}, 10_000, instances, pins)


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
    def __init__(self, state="stopped", fail=False, launched=0.0):
        self.st, self.fail, self.launched, self.calls = state, fail, launched, []

    def state(self, iid):
        self.calls.append(("state", iid))
        return self.st, self.launched

    def start(self, iid):
        self.calls.append(("start", iid))
        if self.fail:
            raise urllib.error.URLError("InsufficientInstanceCapacity")

    def stop(self, iid):
        self.calls.append(("stop", iid))


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
                          "max_hours_day": 12})

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

    def test_an_unpinned_host_is_unchanged(self):
        hosts = readings(**{"razer-laptop": HOT, "asus-laptop": COOL, "hp-victus": COOL})
        self.assertEqual(actions(hosts, self.PINNED, pins={"hp-victus": 2})["razer-laptop"], ("withhold", "razer-1"))

    def test_read_pins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(governor, "OVERRIDE_DIR", d), \
                mock.patch("builtins.print") as out:
            self.assertEqual(governor.read_pins(CFG), {})  # no ConfigMap
            path = os.path.join(d, "hp-victus")
            for body, want in (('{"slots": 1}', {"hp-victus": 1}), ('{"slots": 0}', {"hp-victus": 0}),
                               ('{"slots": -1}', {}), ('{"slots": "2"}', {}), ('{"slots": true}', {}),
                               ('{"slots": 1.5}', {}), ("{}", {}), ("", {}), ("not json", {}), ("[1]", {})):
                with open(path, "w") as f:
                    f.write(body)
                self.assertEqual(governor.read_pins(CFG), want, body)
        lines = [c.args[0] for c in out.call_args_list]
        self.assertEqual(len([l for l in lines if "pin unreadable" in l]), 4)
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

    def test_power_metrics_after_a_live_stop(self):
        ec2 = FakeEc2("running")
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
        tracked = {"aws-burst-1": {"busy_at": 0, "day": time.strftime("%Y-%m-%d"), "run_seconds": 7200.0}}
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
        apply.assert_called_once_with(CFG, "withhold", "hpv-2")
        log = [c.args[0] for c in out.call_args_list]
        self.assertTrue(any(line.startswith("withhold hpv-2 on hp-victus: PINNED") for line in log), log)
        self.assertTrue(any(line.startswith("would withhold razer-1 on razer-laptop") for line in log), log)

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
        cfg = {**AWS, "standby": {"aws-burst-1": {"max_admitted": 8, "power": {**POWER, "usd_per_hour": 1.855}}}}
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
            self.assertEqual(governor.Ec2("us-east-2", "k", "s").state("i-0abc"), ("stopped", 1793805342.0))
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
        governor.apply(CFG, "admit", "hpv-2")
        self.assertEqual([c[0] for c in calls], ["GET", "POST", "DELETE"])
        self.assertEqual(calls[1][2], {"labels": ["unity", "unity-full"]})
        self.assertTrue(calls[2][1].endswith("/7/labels/unity-withheld"))

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


if __name__ == "__main__":
    unittest.main(verbosity=1)
