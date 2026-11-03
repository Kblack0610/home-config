#!/usr/bin/env python3
"""Offline tests for governor.py: canned readings, no Prometheus, no GitHub.

Covers what a live night cannot show on demand: a hot host withholds its idle
runner first and its longest-running busy one next, a host with room re-admits
only while jobs wait, the pool floor and cap hold, a cooling host is left alone,
a missing reading is never room, a full disk empties a host past its floor and cooldown
but not the pool floor, a standby host takes only the jobs the others cannot, a powered
standby host is started and stopped by a fake EC2 (no network) and only it acts while the
rest is dry run, and a live action adds the new label before it removes the old. Stdlib
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


def actions(hosts, runners=RUNNERS, queue=5, last=None, cfg=CFG, instances=None):
    return {h: (a, r) for h, a, r, _, _ in governor.decide(cfg, hosts, runners, queue, last or {}, 10_000, instances)}


POWER = {"provider": "aws", "instance_id": "i-0abc", "region": "us-east-2", "idle_off_seconds": 1200, "live": True}
AWS = {**CFG, "standby": {"aws-burst-1": {"max_admitted": 8, "power": POWER}}}


def instance(state, idle=0):
    return {"aws-burst-1": {"state": state, "busy_at": 10_000 - idle}}


class FakeEc2:
    def __init__(self, state="stopped", fail=False):
        self.st, self.fail, self.calls = state, fail, []

    def state(self, iid):
        self.calls.append(("state", iid))
        return self.st

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

    def test_power_on_never_past_the_pool_cap(self):
        got = actions(self.STEADY, self.FULL, cfg={**AWS, "pool_max_admitted": 2}, instances=instance("stopped"))
        self.assertEqual(got["aws-burst-1"], ("hold", ""))

    def power_cycle(self, ec2, cfg=AWS, observed=None, cycles=1, tracked=None):
        hosts = readings(**{"razer-laptop": HOT, "asus-laptop": BUSY, "hp-victus": BUSY})
        observed = observed or (hosts, dict(self.FULL, **{"razer-3": RUNNERS["razer-3"]}), 9)
        last, tracked = {}, {} if tracked is None else tracked
        with mock.patch.object(governor, "observe", return_value=observed), \
                mock.patch.object(governor, "power_provider", return_value=(ec2, "")), \
                mock.patch.object(governor, "apply") as apply, mock.patch("builtins.print") as out:
            for _ in range(cycles):
                governor.cycle(cfg, last, tracked)
        return apply, [c.args[0] for c in out.call_args_list], last

    def test_a_live_power_host_acts_and_other_hosts_stay_dry_run(self):
        ec2 = FakeEc2("stopped")
        apply, log, _ = self.power_cycle(ec2)
        self.assertIn(("start", "i-0abc"), ec2.calls)
        apply.assert_not_called()  # razer-laptop is HOT, but only "would withhold"
        self.assertTrue(any(line.startswith("would withhold razer-3 on razer-laptop") for line in log))

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
        busy = (hosts, {**self.aws(on_busy=1, off=7), **self.FULL}, 0)
        with mock.patch.object(governor.time, "time", return_value=10_000):
            self.power_cycle(FakeEc2("running"), observed=busy, tracked=tracked)
        self.assertEqual(tracked["aws-burst-1"]["busy_at"], 10_000)

    def test_no_credentials_no_aws_call(self):
        hosts = readings(**{"razer-laptop": BUSY, "asus-laptop": BUSY, "hp-victus": BUSY})
        with mock.patch.object(governor, "AWS_KEY", ""), mock.patch.object(governor, "AWS_SECRET", ""), \
                mock.patch.object(governor, "observe", return_value=(hosts, self.FULL, 9)), \
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

    def test_describe_reads_the_instance_state(self):
        xml = (b'<DescribeInstancesResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"><reservationSet>'
               b'<item><instancesSet><item><instanceState><code>80</code><name>stopped</name></instanceState>'
               b'</item></instancesSet></item></reservationSet></DescribeInstancesResponse>')
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = xml
        with mock.patch.object(governor.urllib.request, "urlopen", return_value=resp) as urlopen:
            self.assertEqual(governor.Ec2("us-east-2", "k", "s").state("i-0abc"), "stopped")
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
                return [{"metric": {}, "value": [0, "1" if 'stuck!="true"' in query else "3"]}]
            return []
        with mock.patch.object(governor, "prom", side_effect=fake):
            _, _, queue = governor.observe("unity")
        self.assertEqual(queue, 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
