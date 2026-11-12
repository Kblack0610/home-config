#!/usr/bin/env python3
"""Offline tests for exporter.py: canned GitHub, Forgejo and Kubernetes responses, no network.

Covers the logic a quiet afternoon cannot show on the live API: a busy runner
gets the job it is running, a queued job and a job-less ghost run show as
waiting, a never-registered roster slot is a `missing` row, and a failed
source makes runners absent and suppresses missing rows instead of reading as
offline. Stdlib only; CI runs it in the checks workflow.
"""
import io
import json
import os
import re
import sys
import time
import unittest
import urllib.error
from unittest import mock

os.environ.update(
    RUNNER_ORGS="acme",
    RUN_REPOS="acme/web acme/game",
    RUNNER_ROSTER="box:light:2 box:unity:1 gone:unity:1",
    CLOUD_HOSTS="gone",
    RECENT_PER_REPO="2",
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exporter  # noqa: E402

T0 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
T1 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 540))
T2 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 300))
OLD = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 90000))


def runner(i, name, labels, status="online", busy=False):
    return {"id": i, "name": name, "status": status, "busy": busy, "labels": [{"name": l} for l in labels]}


FAKE = {
    "orgs/acme/actions/runners?per_page=100&page=1": {
        "total_count": 1, "runners": [runner(1, "box-unity-pool-1", ["self-hosted", "unity", "host-box"], busy=True)]},
    "repos/acme/web/actions/runners?per_page=100&page=1": {
        "total_count": 2, "runners": [runner(2, "box-light-1", ["self-hosted", "light"]),
                                      runner(3, "box-light-2", ["self-hosted", "light"], status="offline")]},
    "repos/acme/game/actions/runners?per_page=100&page=1": {"total_count": 0, "runners": []},
    "repos/acme/web/actions/runs?status=in_progress&per_page=15": {"total_count": 0, "workflow_runs": []},
    "repos/acme/web/actions/runs?status=queued&per_page=15": {
        "total_count": 1, "workflow_runs": [{"id": 20, "name": "Drift", "status": "queued", "created_at": T0,
                                             "head_branch": "develop", "html_url": "https://x/20"}]},
    "repos/acme/web/actions/runs/20/jobs?per_page=100": {"jobs": []},
    "repos/acme/game/actions/runs?status=in_progress&per_page=15": {
        "total_count": 1, "workflow_runs": [{"id": 10, "name": "Tests", "status": "in_progress", "event": "pull_request",
                                             "display_title": "feat: a title long enough to be clipped at sixty characters, really",
                                             "pull_requests": [{"number": 412}]}]},
    "repos/acme/game/actions/runs?status=queued&per_page=15": {"total_count": 0, "workflow_runs": []},
    "repos/acme/game/actions/runs/10/jobs?per_page=100": {"jobs": [
        {"name": "PlayMode 1", "status": "in_progress", "runner_name": "box-unity-pool-1", "started_at": T0,
         "head_branch": "main", "html_url": "https://x/job/1"},
        {"name": "Lint", "status": "in_progress", "runner_name": "GitHub Actions 3", "runner_group_name": "GitHub Actions",
         "labels": ["ubuntu-latest"], "started_at": T0, "head_branch": "main", "html_url": "https://x/job/3"},
        {"name": "PlayMode 2", "status": "queued", "created_at": T0, "head_branch": "main",
         "labels": ["self-hosted", "unity"], "html_url": "https://x/job/2"}]},
    # Completed listings; Fake drops the `created=` cutoff, which moves with the clock.
    # Run 7 is older than the window: GitHub would not list it, but the cached list
    # holds runs that age out between refreshes, and it must not count.
    "repos/acme/web/actions/runs?status=completed&per_page=100&page=1": {"total_count": 3, "workflow_runs": [
        {"id": 9, "name": "CI", "status": "completed", "head_branch": "main", "event": "push", "conclusion": "failure", "created_at": T0,
         "updated_at": T0, "run_started_at": T0, "html_url": "https://x/run/9"},
        {"id": 8, "name": "CI", "status": "completed", "head_branch": "main", "event": "pull_request", "conclusion": "cancelled", "created_at": T0,
         "updated_at": T0, "run_started_at": T0, "html_url": "https://x/run/8"},
        {"id": 7, "name": "CI", "status": "completed", "head_branch": "main", "event": "push", "conclusion": "success", "created_at": OLD,
         "updated_at": OLD, "run_started_at": OLD, "html_url": "https://x/run/7"}]},
    "repos/acme/web/actions/runs/9/jobs?per_page=100": {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "failure", "runner_name": "box-light-1",
         "labels": ["self-hosted", "light"], "created_at": T0, "started_at": T1, "completed_at": T2,
         "html_url": "https://x/job/9"},
        {"name": "build", "status": "completed", "conclusion": "success", "runner_name": "GitHub Actions 5",
         "labels": ["ubuntu-latest"], "created_at": T0, "started_at": T0, "completed_at": T1,
         "html_url": "https://x/job/10"},
        {"name": "deploy", "status": "completed", "conclusion": "skipped", "runner_name": None, "labels": ["ubuntu-latest"],
         "created_at": T0, "started_at": T0, "completed_at": T0, "html_url": "https://x/job/11"}]},
    # A self-hosted job whose runner is not listed (removed since): it keeps its pool.
    "repos/acme/web/actions/runs/8/jobs?per_page=100": {"jobs": [
        {"name": "e2e", "status": "completed", "conclusion": "cancelled", "runner_name": "old-light-9",
         "labels": ["self-hosted", "light"], "created_at": T0, "started_at": T1, "completed_at": T1,
         "html_url": "https://x/job/12"}]},
    "repos/acme/game/actions/runs?status=completed&per_page=100&page=1": {"total_count": 0, "workflow_runs": []},
}


class Fake:
    def __init__(self, broken=()):
        self.broken = broken
        self.calls = []

    def __call__(self, path, kind="other"):
        self.calls.append(path)
        if any(path.startswith(b) for b in self.broken):
            raise urllib.error.URLError("boom")
        return FAKE[norm(path)]


def norm(path):
    # History reads an attempt's jobs page by page; the fixtures list each run's
    # jobs once, under the plain path, as one page.
    path = re.sub(r"&created=[^&]*", "", path)
    if path in FAKE:
        return path
    return re.sub(r"/attempts/\d+/jobs\?per_page=100&page=1$", "/jobs?per_page=100", path)


def rows(text, metric):
    return [l for l in text.splitlines() if l.startswith(metric + "{")]


class ExporterTest(unittest.TestCase):
    def setUp(self):
        exporter.DONE_JOBS.clear()
        exporter.FEED.clear()
        exporter.RUN_FEED.clear()
        exporter.HISTORY.clear()
        exporter.CALLS.clear()
        exporter.RATE.clear()

    def render(self, broken=()):
        exporter.get = Fake(broken)
        return exporter.render()

    def test_busy_runner_carries_its_job(self):
        (row,) = [r for r in rows(self.render(), "github_runner_state") if 'name="box-unity-pool-1"' in r]
        for want in ('state="running"', 'repo="game"', 'workflow="Tests"', 'job_name="PlayMode 1"',
                     'branch="main"', 'url="https://x/job/1"', 'host="box"', 'pool="unity"'):
            self.assertIn(want, row)
        self.assertGreaterEqual(int(row.rsplit(" ", 1)[1]), 590)

    def test_busy_runner_and_its_queued_sibling_carry_the_pr(self):
        out = self.render()
        (row,) = [r for r in rows(out, "github_runner_state") if 'name="box-unity-pool-1"' in r]
        self.assertIn('pr="412"', row)
        self.assertIn('event="pull_request"', row)
        self.assertIn('title="feat: a title long enough to be clipped at sixty characters,"', row)
        (sib,) = [r for r in rows(out, "github_actions_job_waiting") if 'job_name="PlayMode 2"' in r]
        self.assertIn('pr="412"', sib)

    def test_no_pr_is_blank_not_guessed(self):
        # The Drift run has no pull_requests (a schedule run, or a fork PR), so
        # its pr label is empty; idle runners carry the same empty labels.
        out = self.render()
        (ghost,) = [r for r in rows(out, "github_actions_job_waiting") if 'workflow="Drift"' in r]
        self.assertIn('pr=""', ghost)
        (idle,) = [r for r in rows(out, "github_runner_state") if 'name="box-light-1"' in r]
        self.assertIn('pr=""', idle)

    def test_offline_idle_and_missing(self):
        out = self.render()
        state = rows(out, "github_runner_state")
        self.assertTrue(any('name="box-light-1"' in r and 'state="idle"' in r for r in state))
        self.assertTrue(any('name="box-light-2"' in r and 'state="offline"' in r for r in state))
        # box:light has 2 registered (one offline) -> nothing missing there;
        # gone:unity has none -> one missing row.
        missing = [r for r in state if 'state="missing"' in r]
        self.assertEqual(len(missing), 1)
        self.assertIn('host="gone"', missing[0])
        self.assertIn('github_runners_by_state{state="missing",forge="github"} 1', out)

    def test_runner_rows_carry_their_labels_offline_too(self):
        # ci-runner-governor reads these to tell which waiting jobs a stopped host could take.
        state = rows(self.render(), "github_runner_state")
        (busy,) = [r for r in state if 'name="box-unity-pool-1"' in r]
        self.assertIn('labels="host-box,self-hosted,unity"', busy)
        (offline,) = [r for r in state if 'name="box-light-2"' in r]
        self.assertIn('labels="light,self-hosted"', offline)
        (missing,) = [r for r in state if 'state="missing"' in r]
        self.assertIn('labels=""', missing)

    def test_waiting_jobs_include_jobless_ghost_run(self):
        waiting = rows(self.render(), "github_actions_job_waiting")
        self.assertEqual(len(waiting), 2)
        self.assertTrue(any('job_name="PlayMode 2"' in r and 'labels="self-hosted,unity"' in r for r in waiting))
        self.assertTrue(any('job_name="(run has no jobs)"' in r and 'workflow="Drift"' in r for r in waiting))

    def test_one_unreadable_run_keeps_the_repo(self):
        # GitHub 502s a stuck queued run's jobs: that run is a waiting row of its own,
        # and the repo's running jobs and history still read.
        out = self.render(broken=("repos/acme/web/actions/runs/20/jobs",))
        self.assertIn('github_runner_exporter_api_ok{source="repos/acme/web",kind="runs",forge="github"} 1', out)
        waiting = rows(out, "github_actions_job_waiting")
        self.assertTrue(any('job_name="(jobs unreadable)"' in r and 'workflow="Drift"' in r for r in waiting))
        self.assertEqual(len(rows(out, "github_actions_run_recent")), 2)

    def test_waiting_jobs_carry_pool_and_stuck(self):
        waiting = rows(self.render(), "github_actions_job_waiting")
        job = next(r for r in waiting if 'job_name="PlayMode 2"' in r)
        self.assertIn('pool="unity"', job)
        self.assertIn('stuck="false"', job)
        # The ghost run is 10 minutes old in the fixture: jobless, but not stuck yet.
        ghost = next(r for r in waiting if 'job_name="(run has no jobs)"' in r)
        self.assertIn('stuck="false"', ghost)
        old = exporter.JOBLESS_STUCK_SECS
        try:
            exporter.JOBLESS_STUCK_SECS = 60
            ghost = next(r for r in rows(self.render(), "github_actions_job_waiting") if 'job_name="(run has no jobs)"' in r)
            self.assertIn('stuck="true"', ghost)
        finally:
            exporter.JOBLESS_STUCK_SECS = old

    def test_recent_runs(self):
        recent = rows(self.render(), "github_actions_run_recent")
        self.assertEqual(len(recent), 2)  # RECENT_PER_REPO, newest first; the aged-out run 7 is not one
        self.assertIn('conclusion="failure"', recent[0])
        self.assertIn('conclusion="cancelled"', recent[1])

    def test_failed_source_is_unknown_not_offline(self):
        out = self.render(broken=("orgs/acme",))
        self.assertIn('github_runner_exporter_api_ok{source="orgs/acme",kind="runners",forge="github"} 0', out)
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
                         [l for l in out.splitlines() if l.startswith('github_pool_workflow{pool="light",repo="web",workflow="CI",forge="github"}')])

    def test_runs_finished_counts_the_window(self):
        out = self.render()
        self.assertEqual(sorted(rows(out, "github_runs_finished")), [
            'github_runs_finished{repo="web",workflow="CI",conclusion="cancelled",scope="branch"} 1',
            'github_runs_finished{repo="web",workflow="CI",conclusion="failure",scope="main"} 1'])
        # Paging asks with the window's cutoff, URL-encoded.
        self.assertTrue(any("status=completed&created=%3E%3D" in c for c in exporter.get.calls))

    def test_run_scope_is_main_only_for_the_default_branch(self):
        scope = exporter.run_scope
        self.assertEqual(scope({"head_branch": "main", "event": "schedule"}), "main")
        self.assertEqual(scope({"head_branch": "master", "event": "workflow_dispatch"}), "main")
        self.assertEqual(scope({"head_branch": "main", "event": "pull_request"}), "branch")
        self.assertEqual(scope({"head_branch": "fix/kick-scooter-red", "event": "workflow_dispatch"}), "branch")
        self.assertEqual(scope({}), "branch")

    def test_jobs_finished_by_pool(self):
        self.assertEqual(sorted(rows(self.render(), "github_jobs_finished")), [
            'github_jobs_finished{pool="github-hosted",repo="web",workflow="CI",conclusion="skipped",forge="github"} 1',
            'github_jobs_finished{pool="github-hosted",repo="web",workflow="CI",conclusion="success",forge="github"} 1',
            'github_jobs_finished{pool="light",repo="web",workflow="CI",conclusion="cancelled",forge="github"} 1',
            'github_jobs_finished{pool="light",repo="web",workflow="CI",conclusion="failure",forge="github"} 1'])

    def test_queue_and_duration_quantiles(self):
        out = self.render()
        q = {r.rsplit(" ", 1)[0]: int(r.rsplit(" ", 1)[1]) for r in rows(out, "github_job_queue_seconds")}
        # light: lint waited 60s, e2e 60s. The GitHub-hosted build waited 0s.
        # The skipped job had no runner and gives no sample.
        self.assertEqual(q['github_job_queue_seconds{pool="light",quantile="0.5"}'], 60)
        self.assertEqual(q['github_job_queue_seconds{pool="github-hosted",quantile="0.9"}'], 0)
        self.assertEqual(len(q), 4)
        d = {r.rsplit(" ", 1)[0]: int(r.rsplit(" ", 1)[1]) for r in rows(out, "github_job_duration_seconds")}
        # light ran lint 240s and e2e 0s: median (nearest rank) 0, p90 240.
        self.assertEqual(d['github_job_duration_seconds{pool="light",workflow="CI",quantile="0.5",forge="github"}'], 0)
        self.assertEqual(d['github_job_duration_seconds{pool="light",workflow="CI",quantile="0.9",forge="github"}'], 240)
        self.assertEqual(d['github_job_duration_seconds{pool="github-hosted",workflow="CI",quantile="0.5",forge="github"}'], 60)

    def test_quantile_nearest_rank(self):
        xs = list(range(1, 11))
        self.assertEqual([exporter.quantile(xs, 0.5), exporter.quantile(xs, 0.9)], [5, 9])
        self.assertEqual(exporter.quantile([7], 0.9), 7)

    def test_run_list_is_reused_between_refreshes(self):
        self.render()
        exporter.get = Fake()
        exporter.render()
        self.assertFalse(any("status=completed" in c for c in exporter.get.calls))
        # Once the cadence has passed, it is read again.
        for repo, (at, runs) in list(exporter.HISTORY.items()):
            exporter.HISTORY[repo] = (at - exporter.HISTORY_REFRESH_SECS, runs)
        exporter.get = Fake()
        exporter.render()
        self.assertEqual(sum("status=completed" in c for c in exporter.get.calls), 2)

    def test_job_fetch_is_capped_per_scrape_newest_first(self):
        old = exporter.HISTORY_CALL_BUDGET
        try:
            exporter.HISTORY_CALL_BUDGET = 1
            out = self.render()
            self.assertEqual(set(exporter.DONE_JOBS), {9})
            # Run 8 counts as a run already; its job is not counted until cached.
            self.assertIn('github_runs_finished{repo="web",workflow="CI",conclusion="cancelled",scope="branch"} 1', out)
            self.assertFalse(any('conclusion="cancelled"' in r for r in rows(out, "github_jobs_finished")))
            exporter.render()
            self.assertEqual(set(exporter.DONE_JOBS), {9, 8})
        finally:
            exporter.HISTORY_CALL_BUDGET = old

    def test_failed_scrape_keeps_the_job_cache(self):
        self.render()
        self.assertIn(9, exporter.DONE_JOBS)
        for repo, (at, runs) in list(exporter.HISTORY.items()):
            exporter.HISTORY[repo] = (at - exporter.HISTORY_REFRESH_SECS, runs)
        out = self.render(broken=("repos/",))
        self.assertEqual(rows(out, "github_runs_finished"), [])
        self.assertIn(9, exporter.DONE_JOBS)

    def test_no_history_work_below_the_floor(self):
        exporter.rate("repos")["remaining"] = exporter.HISTORY_FLOOR - 1
        out = self.render()
        self.assertFalse(any("status=completed" in c or "/runs/9/" in c for c in exporter.get.calls))
        self.assertEqual(rows(out, "github_runs_finished"), [])
        # Live state is still read.
        self.assertTrue(any('name="box-unity-pool-1"' in r for r in rows(out, "github_runner_state")))

    def test_site_says_local_or_cloud_from_cloud_hosts(self):
        state = rows(self.render(), "github_runner_state")
        self.assertTrue(all('site="local"' in r for r in state if 'host="box"' in r))
        (gone,) = [r for r in state if 'host="gone"' in r]
        self.assertIn('site="cloud"', gone)

    def test_a_feed_repo_feeds_the_feed_and_is_no_runner_source_and_no_metric(self):
        # acme/game read only for the feed: never asked for runners, which needs admin on it (a
        # runner source that cannot answer would blank the pool counts), and none of its jobs
        # count in the pool's metrics.
        with mock.patch.object(exporter, "RUN_REPOS", ["acme/web"]), \
                mock.patch.object(exporter, "FEED_REPOS", ["acme/game"]):
            out = self.render(broken=("repos/acme/game/actions/runners",))
            page = exporter.feed_page(0, 100)
        self.assertFalse(any(c.startswith("repos/acme/game/actions/runners") for c in exporter.get.calls))
        self.assertNotIn('source="repos/acme/game",kind="runners"', out)
        self.assertIn('github_runner_exporter_api_ok{source="repos/acme/game",kind="runs",forge="github"} 1', out)
        self.assertTrue(rows(out, "github_runner_pool_online"))
        self.assertFalse([r for r in out.splitlines() if not r.startswith(("#", "github_runner_exporter_api_ok")) and 'repo="game"' in r])
        self.assertEqual(page["repos"], ["acme/web", "acme/game"])
        self.assertIn("acme/game", {r["repo"] for r in page["runs"]})
        # Negative control: as a pool repo, the same broken listing blanks the pool counts and its jobs count.
        out = self.render(broken=("repos/acme/game/actions/runners",))
        self.assertIn('github_runner_exporter_api_ok{source="repos/acme/game",kind="runners",forge="github"} 0', out)
        self.assertFalse(rows(out, "github_runner_pool_online"))
        self.assertIn('github_hosted_jobs_running{repo="game"} 1', out)

    def test_github_hosted_jobs_running_counts_only_githubs_runners(self):
        out = self.render()
        self.assertIn('github_hosted_jobs_running{repo="game"} 1', out)
        self.assertIn('github_hosted_jobs_running{repo="web"} 0', out)
        # A repo whose runs failed is unknown, not zero.
        out = self.render(broken=("repos/acme/web/actions/runs",))
        self.assertNotIn('github_hosted_jobs_running{repo="web"}', out)

    def test_github_hosted_needs_the_group_or_labels(self):
        self.assertTrue(exporter.github_hosted({"labels": ["ubuntu-latest"]}))
        self.assertFalse(exporter.github_hosted({"runner_group_name": "Default", "labels": ["self-hosted"]}))
        self.assertFalse(exporter.github_hosted({"labels": ["self-hosted", "unity"]}))
        self.assertFalse(exporter.github_hosted({}))

    def test_history_stops_at_the_deadline_and_live_state_does_not(self):
        old = exporter.HISTORY_DEADLINE_SECS
        try:
            exporter.HISTORY_DEADLINE_SECS = 0
            out = self.render()
            # No finished run's jobs are fetched once the deadline has passed...
            self.assertEqual(exporter.DONE_JOBS, {})
            self.assertFalse(any("/runs/9/" in c or "/runs/8/" in c for c in exporter.get.calls))
            # ...but a cold run list is still read, and live runners and active jobs always are.
            self.assertTrue(any("status=completed" in c for c in exporter.get.calls))
            self.assertIn("repos/acme/game/actions/runs/10/jobs?per_page=100", exporter.get.calls)
            self.assertTrue(any('name="box-unity-pool-1"' in r and 'state="running"' in r
                                for r in rows(out, "github_runner_state")))
            # A due refresh of a cached run list waits for a scrape with time left.
            for repo, (at, runs) in list(exporter.HISTORY.items()):
                exporter.HISTORY[repo] = (at - exporter.HISTORY_REFRESH_SECS, runs)
            self.render()
            self.assertFalse(any("status=completed" in c for c in exporter.get.calls))
        finally:
            exporter.HISTORY_DEADLINE_SECS = old
        self.render()
        self.assertIn(9, exporter.DONE_JOBS)

    def test_health_answers_while_a_scrape_renders(self):
        import threading
        import urllib.request
        entered, release = threading.Event(), threading.Event()
        real = exporter.render

        def slow_render():
            entered.set()
            release.wait(5)
            return ""

        exporter.render = slow_render
        srv = exporter.server(("127.0.0.1", 0))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        scrape = threading.Thread(target=lambda: urllib.request.urlopen(base + "/metrics", timeout=5).read())
        try:
            scrape.start()
            self.assertTrue(entered.wait(2))
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                self.assertEqual(r.read(), b"ok")
        finally:
            release.set()
            scrape.join(5)
            exporter.render = real
            srv.shutdown()
            srv.server_close()

    def test_a_repos_active_jobs_are_fetched_in_parallel(self):
        import threading
        both = threading.Barrier(2, timeout=2)
        two_runs = {"total_count": 2, "workflow_runs": [{"id": 10, "name": "Tests", "status": "in_progress"},
                                                         {"id": 11, "name": "Tests", "status": "in_progress"}]}
        fake = Fake()

        def get(path, kind="other"):
            if kind == "active_jobs" and "/acme/game/" in path:
                both.wait()  # breaks, and the render raises, if the two are fetched one by one
            return fake(path, kind)

        with mock.patch.dict(FAKE, {"repos/acme/game/actions/runs?status=in_progress&per_page=15": two_runs,
                                    "repos/acme/game/actions/runs/11/jobs?per_page=100": {"jobs": []}}):
            exporter.get = get
            exporter.render()
        self.assertIn("repos/acme/game/actions/runs/11/jobs?per_page=100", fake.calls)

    def test_finished_jobs_are_fetched_once(self):
        self.render()
        first = exporter.get
        exporter.get = Fake()
        exporter.render()
        self.assertIn("repos/acme/web/actions/runs/9/attempts/1/jobs?per_page=100&page=1", first.calls)
        self.assertNotIn("repos/acme/web/actions/runs/9/attempts/1/jobs?per_page=100&page=1", exporter.get.calls)
        self.assertIn("repos/acme/game/actions/runs/10/jobs?per_page=100", exporter.get.calls)  # active: always refetched

    def test_pool_of_pinned_runners(self):
        self.assertEqual(exporter.pool_of(["self-hosted", "macOS", "arm64"]), "macos")
        self.assertEqual(exporter.pool_of(["self-hosted", "Windows", "X64", "host-windows-media"]), "windows")
        self.assertEqual(exporter.pool_of(["self-hosted", "Linux", "X64"]), "other")
        self.assertEqual(exporter.pool_of(["self-hosted", "linux", "unity-full"]), "unity")
        self.assertEqual(exporter.pool_of(["self-hosted", "linux", "unity-withheld", "host-box"]), "unity")
        self.assertEqual(exporter.pool_of(["self-hosted", "linux", "unity-build", "host-aws-build-1"]), "unity-build")
        self.assertEqual(exporter.pool_of(["self-hosted", "linux", "unity-build-withheld", "host-aws-build-1"]), "unity-build")

    def test_host_label_beats_the_name_so_one_machine_is_one_host(self):
        self.assertEqual(exporter.host_of("m1-playground-mac", ["self-hosted", "macOS", "host-mac-mini"]), "mac-mini")
        self.assertEqual(exporter.host_of("m1-playground-mac", ["self-hosted", "macOS"]), "m1")

    def test_withheld_runner_stays_in_the_pool(self):
        org = "orgs/acme/actions/runners?per_page=100&page=1"
        saved = FAKE[org]
        FAKE[org] = {"total_count": 1, "runners": [
            runner(1, "box-unity-pool-1", ["self-hosted", "unity-withheld", "host-box"], busy=True)]}
        try:
            out = self.render()
        finally:
            FAKE[org] = saved
        self.assertEqual(rows(out, "github_runner_withheld"), ['github_runner_withheld{name="box-unity-pool-1",host="box"} 1'])
        self.assertTrue(any('pool="unity"' in r and 'state="running"' in r for r in rows(out, "github_runner_state")))
        self.assertFalse(any('state="missing"' in r and 'host="box"' in r and 'pool="unity"' in r
                             for r in rows(out, "github_runner_state")))

    def test_withheld_build_runner_stays_in_the_build_pool(self):
        org = "orgs/acme/actions/runners?per_page=100&page=1"
        saved = FAKE[org]
        FAKE[org] = {"total_count": 1, "runners": [
            runner(1, "bld-unity-pool-1", ["self-hosted", "unity-build-withheld", "host-bld"], busy=False)]}
        try:
            out = self.render()
        finally:
            FAKE[org] = saved
        self.assertEqual(rows(out, "github_runner_withheld"), ['github_runner_withheld{name="bld-unity-pool-1",host="bld"} 1'])
        self.assertTrue(any('pool="unity-build"' in r and 'host="bld"' in r for r in rows(out, "github_runner_state")))
        self.assertFalse(any('pool="unity"' in r and 'host="bld"' in r for r in rows(out, "github_runner_state")))

    def test_a_paired_host_runs_two_pools_by_each_runners_labels(self):
        # aws-build-1: build slots in unity-build, test slots in unity, withheld or not.
        base = ["self-hosted", "linux", "host-aws-build-1"]
        for labels, pool in ((["unity-build", "unity-release"], "unity-build"), (["unity-build-withheld"], "unity-build"),
                             (["unity", "unity-full", "unity-gate"], "unity"), (["unity-withheld", "unity-gate"], "unity")):
            self.assertEqual(exporter.pool_of(base + labels), pool, labels)

    def test_no_token_everything_failed(self):
        out = self.render(broken=("orgs/", "repos/"))
        self.assertEqual(rows(out, "github_runner_state"), [])
        self.assertEqual(rows(out, "github_actions_job_waiting"), [])


class ByHourTest(unittest.TestCase):
    """When runs finished, which a trailing-window total cannot say."""

    # Far enough apart that the two can never share an hour, both inside the window.
    CREATED = time.time() - 20 * 3600
    FINISHED = time.time() - 3 * 3600

    @staticmethod
    def iso(t):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    def setUp(self):
        for d in (exporter.DONE_JOBS, exporter.FEED, exporter.RUN_FEED, exporter.HISTORY, exporter.CALLS, exporter.RATE):
            d.clear()

    def render(self, runs):
        listing = {"total_count": len(runs), "workflow_runs": runs}
        jobs = {f"repos/acme/game/actions/runs/{r['id']}/jobs?per_page=100": {"jobs": []} for r in runs}
        with mock.patch.dict(FAKE, {"repos/acme/game/actions/runs?status=completed&per_page=100&page=1": listing, **jobs}):
            exporter.get = Fake()
            return exporter.render()

    def run_row(self, i, conclusion, **kw):
        return {"id": i, "name": "Tests", "head_branch": "feat/x", "event": "pull_request", "conclusion": conclusion,
                "created_at": self.iso(self.CREATED), "updated_at": self.iso(self.FINISHED), "run_started_at": self.iso(self.CREATED),
                "html_url": f"https://x/run/{i}", **kw}

    def game_rows(self, out, metric):
        return [r for r in rows(out, metric) if 'repo="game"' in r]

    def test_a_run_counts_in_the_hour_it_finished_not_the_hour_it_started(self):
        (row,) = self.game_rows(self.render([self.run_row(30, "success")]), "github_runs_by_hour")
        self.assertIn(f'hour="{int(self.FINISHED) // 3600 * 3600}"', row)
        self.assertIn('conclusion="success"', row)
        self.assertIn('scope="branch"', row)
        self.assertTrue(row.endswith(" 1"))

    def test_runs_in_one_hour_add_up(self):
        out = self.render([self.run_row(30, "failure"), self.run_row(31, "failure"), self.run_row(32, "success")])
        counts = {("failure" if 'conclusion="failure"' in r else "success"): r.rsplit(" ", 1)[1] for r in self.game_rows(out, "github_runs_by_hour")}
        self.assertEqual(counts, {"failure": "2", "success": "1"})

    def test_a_failure_is_listed_with_when_and_where(self):
        run = self.run_row(30, "failure", display_title="fix: a thing", pull_requests=[{"number": 77}])
        (row,) = self.game_rows(self.render([run]), "github_run_failed")
        for want in ('workflow="Tests"', 'branch="feat/x"', 'scope="branch"', 'conclusion="failure"', 'pr="77"',
                     'title="fix: a thing"', 'url="https://x/run/30"'):
            self.assertIn(want, row)
        self.assertTrue(row.endswith(f" {int(self.FINISHED)}"))

    def test_a_timeout_is_listed_but_cancelled_and_success_only_count(self):
        out = self.render([self.run_row(30, "timed_out"), self.run_row(31, "cancelled"), self.run_row(32, "success")])
        (row,) = self.game_rows(out, "github_run_failed")
        self.assertIn('conclusion="timed_out"', row)
        self.assertEqual(len(self.game_rows(out, "github_runs_by_hour")), 3)

    def test_a_failure_on_main_says_so(self):
        (row,) = self.game_rows(self.render([self.run_row(30, "failure", head_branch="main", event="push")]), "github_run_failed")
        self.assertIn('scope="main"', row)

    def test_a_run_with_no_finish_time_has_no_hour(self):
        out = self.render([self.run_row(30, "failure", updated_at=None)])
        self.assertEqual(self.game_rows(out, "github_runs_by_hour"), [])
        self.assertEqual(self.game_rows(out, "github_run_failed"), [])

    def test_the_hours_come_from_the_cached_list_with_no_api_call(self):
        runs = [self.run_row(i, "failure") for i in range(30, 35)]
        self.render(runs)
        out = self.render(runs)
        history_calls = [c for c in exporter.get.calls if "status=completed" in c or "/runs/3" in c]
        self.assertEqual(history_calls, [])
        self.assertEqual(len(self.game_rows(out, "github_run_failed")), 5)


class ColdStartBudgetTest(unittest.TestCase):
    """A day of runs at the playground's volume (2026-10-29: ~600 runs, ~3 jobs
    each): the cold start must spend at most HISTORY_CALL_BUDGET history job
    calls per scrape, and stop when GitHub says the budget is low."""

    RUNS = 600

    def setUp(self):
        exporter.DONE_JOBS.clear()
        exporter.FEED.clear()
        exporter.RUN_FEED.clear()
        exporter.HISTORY.clear()
        exporter.CALLS.clear()
        exporter.RATE.clear()
        self.saved = exporter.get

    def tearDown(self):
        exporter.get = self.saved

    def fake(self):
        runs = [{"id": 10000 - i, "name": "Tests", "conclusion": "success", "created_at": T0} for i in range(self.RUNS)]
        calls = {}

        def get(path, kind="other"):
            calls[kind] = calls.get(kind, 0) + 1
            if "/actions/runners" in path:
                return {"total_count": 0, "runners": []}
            m = re.search(r"status=completed.*&page=(\d+)", path)
            if m and path.startswith("repos/acme/web/"):
                return {"total_count": 0, "workflow_runs": []}
            if m:
                p = int(m.group(1))
                return {"total_count": len(runs), "workflow_runs": runs[(p - 1) * 100:p * 100]}
            if "status=" in path:
                return {"total_count": 0, "workflow_runs": []}
            return {"jobs": [{"name": f"j{k}", "conclusion": "success", "runner_name": "x",
                              "created_at": T0, "started_at": T1, "completed_at": T2} for k in range(3)]}
        exporter.get = get
        return calls

    def test_cold_start_stays_inside_the_budget(self):
        calls = self.fake()
        exporter.render()
        # 3 runner sources, 2 active listings per repo, 1 + 6 list pages (600 runs
        # in game, none in web), and the history budget for jobs.
        self.assertEqual(calls, {"runners": 3, "active_runs": 4, "history_runs": 7,
                                 "history_jobs": exporter.HISTORY_CALL_BUDGET})
        self.assertEqual(len(exporter.DONE_JOBS), exporter.HISTORY_CALL_BUDGET)
        scrapes = 1
        while len(exporter.DONE_JOBS) < self.RUNS:
            before = dict(calls)
            exporter.render()
            scrapes += 1
            spent = {k: calls[k] - before.get(k, 0) for k in calls}
            self.assertLessEqual(spent.get("history_jobs", 0), exporter.HISTORY_CALL_BUDGET)
            self.assertEqual(spent.get("history_runs", 0), 0)  # the list is cached between refreshes
            self.assertLess(scrapes, 20)
        self.assertEqual(scrapes, -(-self.RUNS // exporter.HISTORY_CALL_BUDGET))
        # Filled: a scrape now spends nothing on history.
        before = dict(calls)
        exporter.render()
        self.assertEqual(calls["history_jobs"], before["history_jobs"])

    def test_runs_still_count_while_jobs_fill(self):
        self.fake()
        out = exporter.render()
        self.assertIn(f'github_runs_finished{{repo="game",workflow="Tests",conclusion="success",scope="branch"}} {self.RUNS}', out)


class RateLimitTest(unittest.TestCase):
    """A 403 at zero remaining stops every call until GitHub's reset, instead of
    re-asking on each scrape."""

    def setUp(self):
        exporter.RATE.clear()
        exporter.CALLS.clear()
        exporter.NOT_MODIFIED.clear()
        exporter.ETAGS.clear()
        self.saved = (exporter.urllib.request.urlopen, exporter.get)
        exporter.get = ORIGINAL_GET
        self.opened = 0

    def tearDown(self):
        exporter.urllib.request.urlopen, exporter.get = self.saved
        exporter.RATE.clear()

    def refuse(self, headers):
        def urlopen(req, timeout=None):
            self.opened += 1
            raise urllib.error.HTTPError(req.full_url, headers.pop("_code", 403), "rate limited", headers, io.BytesIO(b""))
        exporter.urllib.request.urlopen = urlopen

    def test_backs_off_until_reset(self):
        reset = int(time.time()) + 900
        self.refuse({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
        with self.assertRaises(urllib.error.HTTPError):
            exporter.get("orgs/acme/actions/runners", "runners")
        self.assertEqual(exporter.rate("orgs")["remaining"], 0)
        self.assertEqual(exporter.rate("orgs")["until"], reset)
        for _ in range(3):
            with self.assertRaises(exporter.RateLimited):
                exporter.get("orgs/acme/actions/runners", "runners")
        self.assertEqual(self.opened, 1)
        # A whole scrape while backing off makes no call and reads as api_ok=0.
        out = exporter.render()
        self.assertIn('github_runner_exporter_api_ok{source="orgs/acme",kind="runners",forge="github"} 0', out)
        self.assertIn('github_runner_exporter_rate_remaining{scope="orgs"} 0', out)
        # Only the empty bucket is held off: repos/ calls still go out.
        self.assertGreater(self.opened, 1)

    def test_one_bucket_empty_does_not_block_the_other(self):
        reset = int(time.time()) + 900
        self.refuse({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
        with self.assertRaises(urllib.error.HTTPError):
            exporter.get("repos/acme/web/actions/runners", "runners")
        with self.assertRaises(exporter.RateLimited):
            exporter.get("repos/acme/game/actions/runners", "runners")
        opened = self.opened
        with self.assertRaises(urllib.error.HTTPError):
            exporter.get("orgs/acme/actions/runners", "runners")
        self.assertEqual(self.opened, opened + 1)

    def test_secondary_limit_honours_retry_after(self):
        self.refuse({"_code": 429, "Retry-After": "30"})
        with self.assertRaises(urllib.error.HTTPError):
            exporter.get("orgs/acme/actions/runners", "runners")
        self.assertAlmostEqual(exporter.rate("orgs")["until"], time.time() + 30, delta=2)

    def test_other_errors_do_not_back_off(self):
        self.refuse({"X-RateLimit-Remaining": "4000", "_code": 403})
        with self.assertRaises(urllib.error.HTTPError):
            exporter.get("repos/acme/web/actions/runners", "runners")
        self.assertEqual(exporter.rate("repos")["until"], 0.0)

    def serve(self, body, etag):
        """GitHub as seen by one path: 200 with the ETag, 304 when it is sent back."""
        self.sent = []

        def urlopen(req, timeout=None):
            self.opened += 1
            self.sent.append(req.get_header("If-none-match"))
            headers = {"X-RateLimit-Remaining": "4000", "ETag": etag}
            if req.get_header("If-none-match") == etag:
                raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", headers, io.BytesIO(b""))
            r = io.BytesIO(json.dumps(body).encode())
            r.headers = headers
            return r
        exporter.urllib.request.urlopen = urlopen

    def test_an_unchanged_answer_is_a_free_304_from_the_etag_cache(self):
        self.serve({"runners": [1]}, '"v1"')
        first = exporter.get("orgs/acme/actions/runners", "runners")
        first["runners"].append(2)  # a caller's edit must not reach the cache
        self.assertEqual(exporter.get("orgs/acme/actions/runners", "runners"), {"runners": [1]})
        self.assertEqual(self.sent, [None, '"v1"'])
        self.assertEqual(exporter.CALLS, {"runners": 1})
        self.assertEqual(exporter.NOT_MODIFIED, {"runners": 1})
        exporter.get = Fake()  # render the counter over the fixture repos
        out = exporter.render()
        self.assertIn('github_runner_exporter_api_not_modified_total{kind="runners"} 1', out)

    def test_a_changed_answer_replaces_the_cache_and_is_charged(self):
        self.serve({"runners": [1]}, '"v1"')
        exporter.get("orgs/acme/actions/runners", "runners")
        self.serve({"runners": [1, 2]}, '"v2"')
        self.assertEqual(exporter.get("orgs/acme/actions/runners", "runners"), {"runners": [1, 2]})
        self.assertEqual(exporter.get("orgs/acme/actions/runners", "runners"), {"runners": [1, 2]})
        self.assertEqual(self.sent, ['"v1"', '"v2"'])
        self.assertEqual(exporter.CALLS, {"runners": 2})

    def test_the_etag_cache_is_bounded(self):
        saved, exporter.ETAG_MAX = exporter.ETAG_MAX, 2
        self.addCleanup(setattr, exporter, "ETAG_MAX", saved)
        self.serve({}, '"v"')
        for i in range(3):
            exporter.get(f"repos/acme/web/actions/runs/{i}/jobs", "active_jobs")
        self.assertEqual(list(exporter.ETAGS), ["repos/acme/web/actions/runs/1/jobs", "repos/acme/web/actions/runs/2/jobs"])

    def test_one_shot_paths_are_not_etag_cached(self):
        # History paths are never asked for again (moving created>= bound, or a
        # completed run read once into DONE_JOBS); caching them OOMKilled the pod.
        self.serve({"jobs": []}, '"v"')
        exporter.get("repos/acme/web/actions/runs/9/jobs", "history_jobs")
        exporter.get("repos/acme/web/actions/runs?status=completed&created=%3E%3D2026", "history_runs")
        exporter.get("repos/acme/web/actions/runs/9/jobs", "history_jobs")
        self.assertEqual(list(exporter.ETAGS), [])
        self.assertEqual(self.sent, [None, None, None])


FJ_CONFIG = """log:
  level: info
runner:
  capacity: 2
  labels:
    - "docker:docker://docker:27"
    - "linux-amd64:host"
"""


def pod(name, node, phase="Running", ready=True):
    return {"metadata": {"name": name}, "spec": {"nodeName": node} if node else {},
            "status": {"phase": phase, "containerStatuses": [{"name": "dind", "ready": True},
                                                             {"name": "runner", "ready": ready}]}}


def task(i, status, created, started=None, updated=None, wf="build.yaml", name="build"):
    return {"id": i, "name": name, "head_branch": "master", "event": "push", "display_title": f"commit {i}\nbody",
            "status": status, "workflow_id": wf, "url": f"https://forge/kb/ci/actions/runs/{i}",
            "created_at": created, "updated_at": updated or created, "run_started_at": started or created}


# Forgejo renders its own time zone; this one is T2 written at -05:00.
T2_EST = time.strftime("%Y-%m-%dT%H:%M:%S-05:00", time.gmtime(time.time() - 300 - 5 * 3600))
FJ = {
    "repos/kb/ci/actions/runners/jobs": [
        {"id": 1, "name": "build", "status": "running", "task_id": 101, "runs_on": ["docker"]},
        {"id": 2, "name": "test", "status": "waiting", "task_id": 0, "runs_on": ["docker"]},
        {"id": 3, "name": "arm", "status": "waiting", "task_id": 0, "runs_on": ["linux-arm64"]}],
    "repos/kb/ci/actions/tasks?limit=50&page=1": {"total_count": 4, "workflow_runs": [
        task(101, "running", T2_EST),
        task(90, "success", T0, T0, T1),
        task(89, "failure", T0, T0, T2, wf="lint.yaml", name="lint"),
        task(70, "success", OLD)]},
    # Forgejo answers null, not [], when nothing is waiting or running.
    "repos/kb/site/actions/runners/jobs": None,
    "repos/kb/site/actions/tasks?limit=50&page=1": {"total_count": 0, "workflow_runs": []},
}
FJ_LOG = ("time=x level=info msg=\"task 90 repo is kb/ci https://data.forgejo.org https://forge\"\n"
          "time=x level=info msg=\"task 101 repo is kb/ci https://data.forgejo.org https://forge\"\n")


class FakeForge:
    def __init__(self, broken=(), jobs=None):
        self.broken, self.calls, self.jobs = broken, [], jobs

    def fget(self, path):
        self.calls.append(path)
        if any(path.startswith(b) for b in self.broken):
            raise urllib.error.URLError("boom")
        if self.jobs is not None and path == "repos/kb/ci/actions/runners/jobs":
            return self.jobs
        return FJ[path]

    def kget(self, path, text=False):
        self.calls.append(path)
        if "k8s" in self.broken:
            raise urllib.error.URLError("boom")
        if path.endswith("/configmaps/forgejo-runner-config"):
            return {"data": {"config.yaml": FJ_CONFIG}}
        if "/pods?labelSelector=app.kubernetes.io%2Fname%3Dforgejo-runner" in path:
            return {"items": [pod("runner-b", "razer-laptop", ready=False), pod("runner-a", "hp-victus"),
                              pod("runner-c", None, phase="Pending"),
                              pod("runner-old", "asus-laptop", phase="Succeeded", ready=False)]}
        if path.startswith("/api/v1/namespaces/forgejo/pods/runner-a/log?container=runner&"):
            return FJ_LOG
        raise AssertionError(f"unexpected k8s read {path}")


class ForgejoTest(unittest.TestCase):
    def setUp(self):
        for cache in (exporter.DONE_JOBS, exporter.HISTORY, exporter.CALLS, exporter.RATE,
                      exporter.FJ_TASKS, exporter.FJ_REFRESHED, exporter.FJ_SEEN):
            cache.clear()
        self.repos = mock.patch.object(exporter, "FORGEJO_REPOS", ["kb/ci", "kb/site"])
        self.repos.start()
        self.addCleanup(self.repos.stop)

    def render(self, **kw):
        self.forge = FakeForge(**kw)
        exporter.get = Fake()
        with mock.patch.object(exporter, "fget", self.forge.fget), mock.patch.object(exporter, "kget", self.forge.kget):
            return exporter.render()

    def state(self, out):
        return {re.search(r'name="([^"]+)"', r).group(1): r for r in rows(out, "github_runner_state")
                if 'forge="forgejo"' in r}

    def test_a_runner_is_capacity_rows_and_carries_its_task(self):
        st = self.state(self.render())
        self.assertEqual(sorted(st), ["runner-a/1", "runner-a/2", "runner-b/1", "runner-b/2", "runner-c/1", "runner-c/2"])
        run = st["runner-a/1"]
        for want in ('host="hp-victus"', 'pool="forgejo"', 'site="local"', 'labels="docker,linux-amd64"',
                     'state="running"', 'repo="ci"', 'workflow="build.yaml"', 'job_name="build"', 'branch="master"',
                     'url="https://forge/kb/ci/actions/runs/101"', 'event="push"', 'title="commit 101"'):
            self.assertIn(want, run)
        # The -05:00 start is read as the same instant as T2, 300s ago.
        self.assertGreaterEqual(int(run.rsplit(" ", 1)[1]), 299)
        self.assertLess(int(run.rsplit(" ", 1)[1]), 400)
        self.assertIn('state="idle"', st["runner-a/2"])
        self.assertIn('state="offline"', st["runner-b/1"])
        self.assertIn('state="missing"', st["runner-c/1"])
        self.assertIn('host="unscheduled"', st["runner-c/1"])

    def test_only_up_runners_have_their_log_read(self):
        self.render()
        logs = [c for c in self.forge.calls if "/log?" in c]
        self.assertEqual(len(logs), 1)
        self.assertIn("/pods/runner-a/log?container=runner&sinceSeconds=86400", logs[0])

    def test_forge_label_keeps_the_counts_apart(self):
        out = self.render()
        for want in ('github_runners_by_state{state="running",forge="forgejo"} 1',
                     'github_runners_by_state{state="idle",forge="forgejo"} 1',
                     'github_runners_by_state{state="offline",forge="forgejo"} 2',
                     'github_runners_by_state{state="missing",forge="forgejo"} 2',
                     'github_runners_by_state{state="running",forge="github"} 1'):
            self.assertIn(want, out)
        gh = [r for r in rows(out, "github_runner_state") if 'name="box-unity-pool-1"' in r]
        self.assertIn('forge="github"', gh[0])

    def test_waiting_jobs_age_from_first_seen_and_unrunnable_is_stuck(self):
        out = self.render()
        wait = {re.search(r'job_name="([^"]+)"', r).group(1): r for r in rows(out, "github_actions_job_waiting")
                if 'forge="forgejo"' in r}
        self.assertEqual(sorted(wait), ["arm", "test"])
        for want in ('repo="ci"', 'pool="forgejo"', 'status="waiting"', 'labels="docker"', 'stuck="false"',
                     'url="https://git.kblab.me/kb/ci/actions"'):
            self.assertIn(want, wait["test"])
        self.assertIn('stuck="true"', wait["arm"])
        self.assertEqual(int(wait["test"].rsplit(" ", 1)[1]), 0)
        exporter.FJ_SEEN[2] -= 120
        (again,) = [r for r in rows(self.render(), "github_actions_job_waiting") if 'job_name="test"' in r]
        self.assertGreaterEqual(int(again.rsplit(" ", 1)[1]), 120)
        # Gone from the list (picked up): forgotten, so a later wait starts at 0.
        self.render(jobs=[])
        self.assertEqual(list(exporter.FJ_SEEN), [])

    def test_a_failed_repo_keeps_the_first_seen_times(self):
        self.render()
        self.render(broken=("repos/kb/ci/",))
        self.assertEqual(sorted(exporter.FJ_SEEN), [2, 3])

    def test_finished_work_by_status_in_the_window(self):
        out = self.render()
        fj = sorted(r for r in rows(out, "github_jobs_finished") if 'forge="forgejo"' in r)
        self.assertEqual(fj, [
            'github_jobs_finished{pool="forgejo",repo="ci",workflow="build.yaml",conclusion="success",forge="forgejo"} 1',
            'github_jobs_finished{pool="forgejo",repo="ci",workflow="lint.yaml",conclusion="failure",forge="forgejo"} 1'])
        d = {r.rsplit(" ", 1)[0]: int(r.rsplit(" ", 1)[1]) for r in rows(out, "github_job_duration_seconds")}
        self.assertEqual(d['github_job_duration_seconds{pool="forgejo",workflow="build.yaml",quantile="0.5",forge="forgejo"}'], 60)
        self.assertEqual(d['github_job_duration_seconds{pool="forgejo",workflow="lint.yaml",quantile="0.9",forge="forgejo"}'], 300)
        self.assertEqual(len([r for r in rows(out, "github_pool_workflow") if 'pool="forgejo"' in r]), 2)

    def test_last_job_is_on_every_slot_of_the_runner_that_ran_it(self):
        out = self.render()
        last = sorted(r for r in rows(out, "github_runner_last_job") if 'forge="forgejo"' in r)
        self.assertEqual([re.search(r'name="([^"]+)"', r).group(1) for r in last], ["runner-a/1", "runner-a/2"])
        self.assertIn('last_job="build"', last[0])
        self.assertIn('last_conclusion="success"', last[0])

    def test_api_ok_per_source(self):
        out = self.render()
        for want in ('github_runner_exporter_api_ok{source="forgejo:namespaces/forgejo",kind="runners",forge="forgejo"} 1',
                     'github_runner_exporter_api_ok{source="forgejo:repos/kb/ci",kind="runs",forge="forgejo"} 1',
                     'github_runner_exporter_api_ok{source="forgejo:repos/kb/site",kind="runs",forge="forgejo"} 1'):
            self.assertIn(want, out)
        out = self.render(broken=("repos/kb/site/",))
        self.assertIn('github_runner_exporter_api_ok{source="forgejo:repos/kb/site",kind="runs",forge="forgejo"} 0', out)

    def test_unreadable_runners_are_absent_not_offline(self):
        out = self.render(broken=("k8s",))
        self.assertEqual(self.state(out), {})
        self.assertFalse([r for r in rows(out, "github_runners_by_state") if 'forge="forgejo"' in r])
        self.assertIn('github_runner_exporter_api_ok{source="forgejo:namespaces/forgejo",kind="runners",forge="forgejo"} 0', out)
        # Waiting jobs still show; with no label list to judge them by, none is called stuck.
        wait = [r for r in rows(out, "github_actions_job_waiting") if 'forge="forgejo"' in r]
        self.assertEqual(len(wait), 2)
        self.assertTrue(all('stuck="false"' in r for r in wait))

    def test_off_without_repos(self):
        with mock.patch.object(exporter, "FORGEJO_REPOS", []):
            out = self.render()
        self.assertNotIn('forge="forgejo"', out)
        self.assertEqual(self.forge.calls, [])


class ForgejoPagingTest(unittest.TestCase):
    """forgejo_repo reads back to the window's start on a refresh, else only until it knows every running task."""

    def setUp(self):
        exporter.FJ_TASKS.clear()
        exporter.FJ_REFRESHED.clear()
        exporter.BUDGET["until"] = time.time() + 60
        recent = [task(i, "success", T1) for i in (5, 4, 3, 2, 1)]
        self.pages = {f"repos/kb/ci/actions/tasks?limit=2&page={n}": {"workflow_runs": recent[2 * n - 2:2 * n]}
                      for n in (1, 2, 3)}
        self.pages["repos/kb/ci/actions/runners/jobs"] = [{"id": 9, "status": "running", "task_id": 5}]
        self.calls = []

    def read(self):
        def fget(path):
            self.calls.append(path)
            return self.pages[path]
        self.calls.clear()
        with mock.patch.object(exporter, "fget", fget), mock.patch.object(exporter, "FORGEJO_PAGE", 2):
            return exporter.forgejo_repo("kb/ci", time.time())

    def test_refresh_reads_every_page_then_one(self):
        self.assertTrue(self.read()["ok"])
        self.assertEqual(len([c for c in self.calls if "/tasks?" in c]), 3)
        self.assertEqual(sorted(exporter.FJ_TASKS["kb/ci"]), [1, 2, 3, 4, 5])
        self.read()
        self.assertEqual(len([c for c in self.calls if "/tasks?" in c]), 1)

    def test_an_unknown_running_task_reads_on(self):
        self.read()
        self.pages["repos/kb/ci/actions/runners/jobs"] = [{"id": 9, "status": "running", "task_id": 77}]
        self.read()
        self.assertEqual(len([c for c in self.calls if "/tasks?" in c]), 3)

    def test_deadline_stops_the_read_and_leaves_the_refresh_due(self):
        exporter.BUDGET["until"] = 0
        self.read()
        self.assertEqual(len([c for c in self.calls if "/tasks?" in c]), 1)
        self.assertNotIn("kb/ci", exporter.FJ_REFRESHED)

    def test_offset_timestamps(self):
        self.assertEqual(exporter.ts("2026-11-03T22:15:08-05:00"), exporter.ts("2026-11-04T03:15:08Z"))
        self.assertIsNone(exporter.ts("2026-11-04T03:15:08"))
        self.assertIsNone(exporter.ts("garbage"))


ORIGINAL_GET = exporter.get


class DetailFeedTest(unittest.TestCase):
    """/api/ci/details: the window's job detail per exact attempt, as a changes feed."""

    def setUp(self):
        for d in (exporter.DONE_JOBS, exporter.FEED, exporter.RUN_FEED, exporter.HISTORY, exporter.CALLS, exporter.RATE):
            d.clear()
        self.saved = exporter.get

    def tearDown(self):
        exporter.get = self.saved

    def render(self, extra=None):
        with mock.patch.dict(FAKE, extra or {}):
            exporter.get = Fake()
            exporter.render()
        return exporter.get

    def test_feed_serves_each_finished_run_with_its_jobs_and_steps(self):
        steps = [{"number": 1, "name": "checkout", "conclusion": "success", "started_at": T0, "completed_at": T1,
                  "status": "completed", "log": "x" * 50}]
        jobs9 = {"jobs": [dict(j, steps=steps) for j in FAKE["repos/acme/web/actions/runs/9/jobs?per_page=100"]["jobs"]]}
        self.render({"repos/acme/web/actions/runs/9/jobs?per_page=100": jobs9})
        page = exporter.feed_page(0, 100)
        self.assertEqual(page["repos"], exporter.RUN_REPOS)
        by_id = {r["run_id"]: r for r in page["records"]}
        self.assertEqual(set(by_id), {9, 8})  # 7 is outside the window
        rec = by_id[9]
        self.assertEqual((rec["forge"], rec["repo"], rec["attempt"], rec["complete"]), ("github", "acme/web", 1, True))
        self.assertEqual(rec["jobs"][0]["steps"][0], {k: steps[0][k] for k in exporter.STEP_KEYS})
        # The cursor: nothing new after the last record, and an unchanged run is not served twice.
        self.assertEqual(exporter.feed_page(page["next"], 100)["records"], [])
        self.render()
        self.assertEqual(exporter.feed_page(page["next"], 100)["records"], [])

    def test_feed_pages_by_limit(self):
        self.render()
        # Two job records and four run records (20, 10, 9, 8), one cursor for both.
        cursor, seen = 0, []
        while True:
            page = exporter.feed_page(cursor, 1)
            got = [("job", r["run_id"]) for r in page["records"]] + [("run", r["run"]["id"]) for r in page["runs"]]
            self.assertEqual(len(got), 1)
            seen += got
            cursor = page["next"]
            if not page["more"]:
                break
        self.assertEqual(sorted(seen), [("job", 8), ("job", 9), ("run", 8), ("run", 9), ("run", 10), ("run", 20)])
        self.assertEqual(exporter.feed_page(cursor, 1)["runs"], [])

    def expire_history(self):
        for repo in list(exporter.HISTORY):
            at, runs = exporter.HISTORY[repo]
            exporter.HISTORY[repo] = (at - exporter.HISTORY_REFRESH_SECS, runs)

    def test_runs_are_served_once_per_state_change(self):
        self.render()
        runs = {r["run"]["id"]: r for r in exporter.feed_page(0, 100)["runs"]}
        self.assertEqual(set(runs), {20, 10, 9, 8})  # 7 is outside the window
        self.assertEqual({k: runs[20][k] for k in ("forge", "repo")}, {"forge": "github", "repo": "acme/web"})
        self.assertEqual(runs[20]["run"]["status"], "queued")
        self.assertEqual(runs[10]["run"]["display_title"], FAKE["repos/acme/game/actions/runs?status=in_progress&per_page=15"]
                         ["workflow_runs"][0]["display_title"])
        self.assertEqual(runs[9]["run"]["conclusion"], "failure")
        cursor = exporter.feed_page(0, 100)["next"]
        self.render()
        self.assertEqual(exporter.feed_page(cursor, 100)["runs"], [])
        started = {"total_count": 1, "workflow_runs": [dict(FAKE["repos/acme/web/actions/runs?status=queued&per_page=15"]
                                                             ["workflow_runs"][0], status="in_progress", run_started_at=T1)]}
        self.render({"repos/acme/web/actions/runs?status=queued&per_page=15": {"total_count": 0, "workflow_runs": []},
                     "repos/acme/web/actions/runs?status=in_progress&per_page=15": started})
        (rec,) = exporter.feed_page(cursor, 100)["runs"]
        self.assertEqual((rec["run"]["id"], rec["run"]["status"], rec["run"]["run_started_at"]), (20, "in_progress", T1))

    def test_a_cached_completed_list_does_not_move_a_rerun_back(self):
        self.render()
        rerun = dict(FAKE["repos/acme/web/actions/runs?status=completed&per_page=100&page=1"]["workflow_runs"][0],
                     status="in_progress", conclusion=None, run_attempt=2)
        cursor = exporter.feed_page(0, 100)["next"]
        self.render({"repos/acme/web/actions/runs?status=in_progress&per_page=15": {"total_count": 1, "workflow_runs": [rerun]},
                     "repos/acme/web/actions/runs/9/jobs?per_page=100": {"jobs": []}})
        (rec,) = exporter.feed_page(cursor, 100)["runs"]
        self.assertEqual((rec["run"]["id"], rec["run"]["run_attempt"], rec["run"]["status"]), (9, 2, "in_progress"))
        self.assertEqual(exporter.RUN_FEED[9]["run"]["run_attempt"], 2)

    def test_a_lost_run_is_read_by_id_with_the_history(self):
        self.render()
        lost = {"id": 5, "name": "Nightly", "status": "in_progress", "created_at": OLD, "run_attempt": 2}
        exporter.run_feed_store("acme/web", lost)
        cursor = exporter.feed_page(0, 100)["next"]
        by_id = "repos/acme/web/actions/runs/5"
        # Not yet due: the lost read rides the history refresh.
        get = self.render({by_id: dict(lost, status="completed", conclusion="success")})
        self.assertNotIn(by_id, get.calls)
        self.expire_history()
        get = self.render({by_id: dict(lost, status="completed", conclusion="success")})
        self.assertIn(by_id, get.calls)
        (rec,) = exporter.feed_page(cursor, 100)["runs"]
        self.assertEqual((rec["run"]["id"], rec["run"]["conclusion"]), (5, "success"))
        # Finished and out of the cached lists, it stays a window for the reader.
        self.assertIn(5, exporter.RUN_FEED)

    def test_lost_reads_take_turns_and_a_run_github_lost_is_dropped(self):
        self.render()
        for rid in (5, 6):
            exporter.run_feed_store("acme/web", {"id": rid, "name": "Nightly", "status": "in_progress", "created_at": OLD})
        gone = urllib.error.HTTPError("x", 404, "Not Found", {}, None)

        class Getter(Fake):
            def __call__(self, path, kind="other"):
                if path == "repos/acme/web/actions/runs/6":
                    self.calls.append(path)
                    raise gone
                return super().__call__(path, kind)
        lost = lambda g: [c for c in g.calls if c in ("repos/acme/web/actions/runs/5", "repos/acme/web/actions/runs/6")]
        with mock.patch.object(exporter, "HISTORY_CALL_BUDGET", 1):
            self.expire_history()
            exporter.get = first = Getter()
            exporter.render()  # 5 is read (no answer: it stays open)
            self.expire_history()
            exporter.get = second = Getter()
            exporter.render()  # then 6, not 5 again: GitHub says 404, so it goes
        self.assertEqual((lost(first), lost(second)), (["repos/acme/web/actions/runs/5"], ["repos/acme/web/actions/runs/6"]))
        self.assertIn(5, exporter.RUN_FEED)
        self.assertNotIn(6, exporter.RUN_FEED)

    def test_an_open_record_unread_for_a_week_is_dropped(self):
        exporter.run_feed_store("acme/web", {"id": 5, "name": "Nightly", "status": "in_progress", "created_at": OLD})
        exporter.run_feed_store("acme/web", {"id": 6, "name": "Nightly", "status": "in_progress", "created_at": OLD})
        exporter.RUN_FEED[5]["stored_at"] -= exporter.RUN_FEED_OPEN_SECS + 1
        self.render()
        self.assertNotIn(5, exporter.RUN_FEED)
        self.assertIn(6, exporter.RUN_FEED)

    def test_an_older_read_of_the_same_state_does_not_overwrite_a_newer_one(self):
        run = {"id": 5, "name": "N", "status": "in_progress", "created_at": T0, "updated_at": T2}
        exporter.run_feed_store("acme/web", run)
        seq = exporter.RUN_FEED[5]["seq"]
        exporter.run_feed_store("acme/web", dict(run, updated_at=T1, run_started_at=T0))
        self.assertEqual(exporter.RUN_FEED[5]["seq"], seq)

    def test_a_rerun_is_fetched_for_its_new_attempt(self):
        self.render()
        listing = dict(FAKE["repos/acme/web/actions/runs?status=completed&per_page=100&page=1"])
        listing["workflow_runs"] = [dict(r, run_attempt=2) if r["id"] == 9 else r for r in listing["workflow_runs"]]
        for repo in list(exporter.HISTORY):
            at, _ = exporter.HISTORY[repo]
            exporter.HISTORY[repo] = (at - exporter.HISTORY_REFRESH_SECS, [])
        cursor = exporter.feed_page(0, 100)["next"]
        get = self.render({"repos/acme/web/actions/runs?status=completed&per_page=100&page=1": listing,
                           "repos/acme/web/actions/runs/9/attempts/2/jobs?per_page=100&page=1": {"total_count": 0, "jobs": []}})
        self.assertIn("repos/acme/web/actions/runs/9/attempts/2/jobs?per_page=100&page=1", get.calls)
        (rec,) = exporter.feed_page(cursor, 100)["records"]
        self.assertEqual((rec["run_id"], rec["attempt"]), (9, 2))

    def test_a_finished_run_whose_pr_is_retitled_is_served_again(self):
        # A pull_request run's display_title is the PR's title now: a retitle changes it
        # and leaves updated_at alone, and the run ledger must take the new title.
        self.render()
        listing = dict(FAKE["repos/acme/web/actions/runs?status=completed&per_page=100&page=1"])
        listing["workflow_runs"] = [dict(r, display_title="fix: the new title") if r["id"] == 9 else r
                                    for r in listing["workflow_runs"]]
        self.expire_history()
        cursor = exporter.feed_page(0, 100)["next"]
        self.render({"repos/acme/web/actions/runs?status=completed&per_page=100&page=1": listing})
        runs = exporter.feed_page(cursor, 100)["runs"]
        self.assertEqual([(r["run"]["id"], r["run"]["display_title"]) for r in runs], [(9, "fix: the new title")])
        # read again unchanged: nothing new to serve
        cursor = exporter.feed_page(0, 100)["next"]
        self.expire_history()
        self.render({"repos/acme/web/actions/runs?status=completed&per_page=100&page=1": listing})
        self.assertEqual(exporter.feed_page(cursor, 100)["runs"], [])

    def test_a_run_with_more_than_100_jobs_pages_and_a_failed_page_stays_partial(self):
        page1 = {"total_count": 150, "jobs": [{"id": i, "name": f"j{i}"} for i in range(100)]}
        base = "repos/acme/web/actions/runs/9/attempts/1/jobs?per_page=100"
        broken = Fake(broken=(base + "&page=2",))
        with mock.patch.dict(FAKE, {base + "&page=1": page1}):
            exporter.get = broken
            exporter.render()
        rec = {r["run_id"]: r for r in exporter.feed_page(0, 100)["records"]}[9]
        self.assertFalse(rec["complete"])
        self.assertEqual(len(rec["jobs"]), 100)
        page2 = {"total_count": 150, "jobs": [{"id": i, "name": f"j{i}"} for i in range(100, 150)]}
        cursor = exporter.feed_page(0, 100)["next"]
        with mock.patch.dict(FAKE, {base + "&page=1": page1, base + "&page=2": page2}):
            exporter.get = Fake()
            exporter.render()
        (rec,) = exporter.feed_page(cursor, 100)["records"]
        self.assertTrue(rec["complete"])
        self.assertEqual(len(rec["jobs"]), 150)

    def test_routes_ignore_a_trailing_slash_and_a_query(self):
        import threading
        import urllib.request
        srv = exporter.server(("127.0.0.1", 0))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        try:
            for path in ("/health", "/health/", "/health?x=1"):
                with urllib.request.urlopen(base + path, timeout=2) as r:
                    self.assertEqual(r.read(), b"ok", path)
            with urllib.request.urlopen(base + "/api/ci/details/?cursor=0&limit=5", timeout=2) as r:
                body = json.loads(r.read())
            self.assertEqual(body["epoch"], exporter.EPOCH)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/api/ci/details?cursor=x", timeout=2)
            self.assertEqual(e.exception.code, 400)
        finally:
            srv.shutdown()
            srv.server_close()

if __name__ == "__main__":
    unittest.main(verbosity=1)

