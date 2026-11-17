"""ci-slot-effect's classification, contention and host readings, on fixtures (no gh, no Prometheus)."""

import argparse
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("effect", Path(__file__).with_name("ci-slot-effect.py"))
effect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(effect)

T0 = effect.ts("2026-11-15T00:00:00Z")


def iso(offset):
    return effect.dt.datetime.fromtimestamp(T0 + offset, effect.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def step(name, start, end, conclusion="success"):
    return {"name": name, "started_at": iso(start), "completed_at": iso(end), "conclusion": conclusion}


def job(name, runner, start, end, steps, conclusion="success"):
    return {"name": name, "runner_name": runner, "conclusion": conclusion, "created_at": iso(start - 60),
            "started_at": iso(start), "completed_at": iso(end), "steps": steps}


class WindowTests(unittest.TestCase):
    def test_parses_a_forward_window(self):
        a, b = effect.window("2026-11-15T00:00Z..2026-11-16T00:00Z")
        self.assertEqual(b - a, 86400)

    def test_rejects_backward_long_and_unsplit_windows(self):
        for text in ("2026-11-16T00:00Z..2026-11-15T00:00Z", "2026-11-01T00:00Z..2026-11-15T00:00Z", "2026-11-15T00:00Z"):
            with self.assertRaises(argparse.ArgumentTypeError, msg=text):
                effect.window(text)


class ClassifyTests(unittest.TestCase):
    def test_build_scope_follows_the_event(self):
        j = job("\U0001f528 Build", "r", 0, 300, [step("Build all packages", 60, 150)])
        self.assertEqual(effect.classify("push", j), ("Build", "full", 90))
        self.assertEqual(effect.classify("pull_request", j), ("Build", "affected", 90))

    def test_typecheck_sums_the_shared_build_and_the_check(self):
        j = job("\U0001f50d Type Check", "r", 0, 300, [step("Build shared packages (x)", 10, 40), step("Type check", 40, 100),
                                                       step("Install dependencies", 0, 10)])
        self.assertEqual(effect.classify("push", j)[2], 90)

    def test_test_scope_is_the_coverage_step_and_skipped_steps_do_not_count(self):
        full = job("\U0001f9ea Test (Shard 1/2)", "r", 0, 300, [step("Run tests with coverage (shard 1/2)", 0, 50),
                                                                step("Run affected tests (shard 1/2)", 50, 50, "skipped")])
        scoped = job("\U0001f9ea Test (Shard 2/2)", "r", 0, 300, [step("Run tests with coverage (shard 2/2)", 0, 0, "skipped"),
                                                                  step("Run affected tests (shard 2/2)", 0, 20)])
        self.assertEqual(effect.classify("push", full), ("Test", "full", 50))
        self.assertEqual(effect.classify("push", scoped), ("Test", "affected", 20))

    def test_other_jobs_and_failures_are_not_timed(self):
        self.assertIsNone(effect.classify("push", job("\U0001f512 Security Audit", "r", 0, 60, [])))
        self.assertIsNone(effect.classify("push", job("\U0001f528 Build", "r", 0, 60, [], conclusion="failure")))

    def test_a_job_whose_main_step_did_not_run_is_not_timed_as_zero(self):
        skipped = job("\U0001f528 Build", "r", 0, 60, [step("Build all packages", 0, 0, "skipped")])
        self.assertIsNone(effect.classify("push", skipped))


class HelperTests(unittest.TestCase):
    def test_a_time_with_no_offset_is_utc(self):
        self.assertEqual(effect.ts("2026-11-15T00:00:00"), T0)

    def test_pct_interpolates_and_its_p50_is_the_median(self):
        self.assertEqual(effect.pct([1, 2, 3, 4], .5), 2.5)
        self.assertEqual(effect.pct([5], .9), 5)
        self.assertIsNone(effect.pct([], .5))


class ContentionTests(unittest.TestCase):
    def test_counts_other_slots_only(self):
        busy = {"h-light-1": {T0, T0 + 60, T0 + 120}, "h-light-2": {T0 + 60}, "h-heavy-1": set()}
        self.assertAlmostEqual(effect.others_busy(busy, "h-light-1", T0, T0 + 120), 1 / 3)
        self.assertAlmostEqual(effect.others_busy(busy, "h-light-2", T0, T0 + 120), 1)

    def test_job_rows_match_the_host_and_window(self):
        windows = {"before": (T0 - 3600, T0), "after": (T0, T0 + 3600)}
        busy = {"razer-laptop": {"razer-laptop-light-1": set(), "razer-laptop-light-2": set()}}
        jobs = [("push", job("\U0001f528 Build", "razer-laptop-light-1", 600, 900, [step("Build all packages", 660, 840)])),
                ("push", job("\U0001f528 Build", "razer-laptop2-light-1", 600, 900, [])),
                ("push", job("\U0001f528 Build", "razer-laptop-light-2", 7200, 7300, []))]
        rows = effect.job_rows(jobs, ["razer-laptop"], windows, busy)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["win"], rows[0]["step"], rows[0]["queue"], rows[0]["others"]), ("after", 180, 60, 0))


class HostSummaryTests(unittest.TestCase):
    def test_ci_readings_cover_only_busy_samples(self):
        times = [T0 + 60 * k for k in range(4)]
        load = dict(zip(times, (0.5, 1.2, 0.8, 0.4)))
        slice_busy = dict(zip(times, (0.0, 4.0, 2.0, 6.0)))
        psi = dict(zip(times, (0.0, 0.4, 0.2, 9.0)))
        busy = {"h-light-1": {times[1], times[2]}, "h-light-2": {times[1]}}
        s = effect.host_summary(load, slice_busy, psi, {times[1]: 1.0}, busy, T0, T0 + 240)
        self.assertEqual(s["slice_mean"], 3.0)
        self.assertAlmostEqual(s["psi_mean"], 0.3)
        self.assertEqual(s["slots_busy"], 1.5)
        self.assertEqual(s["load_max"], 1.2)
        self.assertEqual(s["alert_min"], 1)
        self.assertEqual(s["over1_day"], 360)  # one minute over 1.0 in four minutes is 360 a day
        self.assertEqual(s["coverage"], 1.0)

    def test_no_samples_is_none(self):
        self.assertIsNone(effect.host_summary({}, {}, {}, {}, {}, T0, T0 + 60))


if __name__ == "__main__":
    unittest.main()
