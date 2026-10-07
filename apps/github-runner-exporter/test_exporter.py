#!/usr/bin/env python3
"""Offline tests for exporter.py: canned GitHub responses, no network.

Covers the logic a quiet afternoon cannot show on the live API: a busy runner
gets the job it is running, a queued job and a job-less ghost run show as
waiting, a never-registered roster slot is a `missing` row, and a failed
source makes runners absent and suppresses missing rows instead of reading as
offline. Stdlib only; CI runs it in the checks workflow.
"""
import os
import sys
import time
import unittest
import urllib.error

os.environ.update(
    RUNNER_ORGS="acme",
    RUN_REPOS="acme/web acme/game",
    RUNNER_ROSTER="box:pmp-light:2 box:unity:1 gone:unity:1",
    RECENT_PER_REPO="2",
    HISTORY_PER_REPO="2",
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exporter  # noqa: E402

T0 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))


def runner(i, name, labels, status="online", busy=False):
    return {"id": i, "name": name, "status": status, "busy": busy, "labels": [{"name": l} for l in labels]}


FAKE = {
    "orgs/acme/actions/runners?per_page=100&page=1": {
        "total_count": 1, "runners": [runner(1, "box-unity-pool-1", ["self-hosted", "unity", "host-box"], busy=True)]},
    "repos/acme/web/actions/runners?per_page=100&page=1": {
        "total_count": 2, "runners": [runner(2, "box-light-1", ["self-hosted", "pmp-light"]),
                                      runner(3, "box-light-2", ["self-hosted", "pmp-light"], status="offline")]},
    "repos/acme/game/actions/runners?per_page=100&page=1": {"total_count": 0, "runners": []},
    "repos/acme/web/actions/runs?status=in_progress&per_page=15": {"total_count": 0, "workflow_runs": []},
    "repos/acme/web/actions/runs?status=queued&per_page=15": {
        "total_count": 1, "workflow_runs": [{"id": 20, "name": "Drift", "status": "queued", "created_at": T0,
                                             "head_branch": "develop", "html_url": "https://x/20"}]},
    "repos/acme/web/actions/runs/20/jobs?per_page=100": {"jobs": []},
    "repos/acme/game/actions/runs?status=in_progress&per_page=15": {
        "total_count": 1, "workflow_runs": [{"id": 10, "name": "Tests", "status": "in_progress"}]},
    "repos/acme/game/actions/runs?status=queued&per_page=15": {"total_count": 0, "workflow_runs": []},
    "repos/acme/game/actions/runs/10/jobs?per_page=100": {"jobs": [
        {"name": "PlayMode 1", "status": "in_progress", "runner_name": "box-unity-pool-1", "started_at": T0,
         "head_branch": "main", "html_url": "https://x/job/1"},
        {"name": "PlayMode 2", "status": "queued", "created_at": T0, "head_branch": "main",
         "labels": ["self-hosted", "unity"], "html_url": "https://x/job/2"}]},
    "repos/acme/web/actions/runs?status=completed&per_page=2": {"workflow_runs": [
        {"id": 9, "name": "CI", "head_branch": "main", "event": "push", "conclusion": "failure",
         "updated_at": T0, "run_started_at": T0, "html_url": "https://x/run/9"}]},
    "repos/acme/web/actions/runs/9/jobs?per_page=100": {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "failure", "runner_name": "box-light-1",
         "completed_at": T0, "html_url": "https://x/job/9"},
        {"name": "build", "status": "completed", "conclusion": "success", "runner_name": "GitHub Actions 5",
         "completed_at": T0, "html_url": "https://x/job/10"}]},
    "repos/acme/game/actions/runs?status=completed&per_page=2": {"workflow_runs": []},
}


class Fake:
    def __init__(self, broken=()):
        self.broken = broken
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if any(path.startswith(b) for b in self.broken):
            raise urllib.error.URLError("boom")
        return FAKE[path]


def rows(text, metric):
    return [l for l in text.splitlines() if l.startswith(metric + "{")]


class ExporterTest(unittest.TestCase):
    def setUp(self):
        exporter.DONE_JOBS.clear()

    def render(self, broken=()):
        exporter.get = Fake(broken)
        return exporter.render()

    def test_busy_runner_carries_its_job(self):
        (row,) = [r for r in rows(self.render(), "github_runner_state") if 'name="box-unity-pool-1"' in r]
        for want in ('state="running"', 'repo="game"', 'workflow="Tests"', 'job_name="PlayMode 1"',
                     'branch="main"', 'url="https://x/job/1"', 'host="box"', 'pool="unity"'):
            self.assertIn(want, row)
        self.assertGreaterEqual(int(row.rsplit(" ", 1)[1]), 590)

    def test_offline_idle_and_missing(self):
        out = self.render()
        state = rows(out, "github_runner_state")
        self.assertTrue(any('name="box-light-1"' in r and 'state="idle"' in r for r in state))
        self.assertTrue(any('name="box-light-2"' in r and 'state="offline"' in r for r in state))
        # box:pmp-light has 2 registered (one offline) -> nothing missing there;
        # gone:unity has none -> one missing row.
        missing = [r for r in state if 'state="missing"' in r]
        self.assertEqual(len(missing), 1)
        self.assertIn('host="gone"', missing[0])
        self.assertIn('github_runners_by_state{state="missing"} 1', out)

    def test_waiting_jobs_include_jobless_ghost_run(self):
        waiting = rows(self.render(), "github_actions_job_waiting")
        self.assertEqual(len(waiting), 2)
        self.assertTrue(any('job_name="PlayMode 2"' in r and 'labels="self-hosted,unity"' in r for r in waiting))
        self.assertTrue(any('job_name="(run has no jobs)"' in r and 'workflow="Drift"' in r for r in waiting))

    def test_recent_runs(self):
        out = self.render()
        self.assertEqual(len(rows(out, "github_actions_run_recent")), 1)
        self.assertIn('conclusion="failure"', rows(out, "github_actions_run_recent")[0])

    def test_failed_source_is_unknown_not_offline(self):
        out = self.render(broken=("orgs/acme",))
        self.assertIn('github_runner_exporter_api_ok{source="orgs/acme",kind="runners"} 0', out)
        state = rows(out, "github_runner_state")
        self.assertFalse(any('name="box-unity-pool-1"' in r for r in state))
        self.assertFalse(any('state="missing"' in r for r in state))
        self.assertNotIn('state="missing"} ', "\n".join(l for l in out.splitlines() if l.startswith("github_runners_by_state")))
        self.assertEqual(rows(out, "github_runner_pool_online"), [])

    def test_no_reserved_prometheus_labels(self):
        # Prometheus renames a scraped `job`/`instance` label to exported_*, so
        # emitting one silently moves the value out of the column that shows it.
        for line in self.render().splitlines():
            if not line.startswith("#"):
                self.assertNotRegex(line, r'[{,](job|instance)="', line)

    def test_last_job_per_runner_and_pool_workflows(self):
        out = self.render()
        (row,) = rows(out, "github_runner_last_job")
        for want in ('name="box-light-1"', 'last_repo="web"', 'last_workflow="CI"', 'last_job="lint"',
                     'last_conclusion="failure"', 'last_url="https://x/job/9"'):
            self.assertIn(want, row)
        # The GitHub-hosted job is not a runner of ours and must not appear.
        self.assertNotIn("GitHub Actions", out)
        self.assertEqual(rows(out, "github_pool_workflow"),
                         [l for l in out.splitlines() if l.startswith('github_pool_workflow{pool="pmp-light",repo="web",workflow="CI"}')])

    def test_finished_jobs_are_fetched_once(self):
        self.render()
        exporter.get = Fake()
        exporter.render()
        self.assertNotIn("repos/acme/web/actions/runs/9/jobs?per_page=100", exporter.get.calls)
        self.assertIn("repos/acme/game/actions/runs/10/jobs?per_page=100", exporter.get.calls)  # active: always refetched

    def test_no_token_everything_failed(self):
        out = self.render(broken=("orgs/", "repos/"))
        self.assertEqual(rows(out, "github_runner_state"), [])
        self.assertEqual(rows(out, "github_actions_job_waiting"), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
