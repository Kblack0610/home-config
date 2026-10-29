#!/usr/bin/env python3
"""Offline tests for exporter.py: canned GitHub responses, no network.

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

os.environ.update(
    RUNNER_ORGS="acme",
    RUN_REPOS="acme/web acme/game",
    RUNNER_ROSTER="box:pmp-light:2 box:unity:1 gone:unity:1",
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
        "total_count": 2, "runners": [runner(2, "box-light-1", ["self-hosted", "pmp-light"]),
                                      runner(3, "box-light-2", ["self-hosted", "pmp-light"], status="offline")]},
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
        {"name": "PlayMode 2", "status": "queued", "created_at": T0, "head_branch": "main",
         "labels": ["self-hosted", "unity"], "html_url": "https://x/job/2"}]},
    # Completed listings; Fake drops the `created=` cutoff, which moves with the clock.
    # Run 7 is older than the window: GitHub would not list it, but the cached list
    # holds runs that age out between refreshes, and it must not count.
    "repos/acme/web/actions/runs?status=completed&per_page=100&page=1": {"total_count": 3, "workflow_runs": [
        {"id": 9, "name": "CI", "head_branch": "main", "event": "push", "conclusion": "failure", "created_at": T0,
         "updated_at": T0, "run_started_at": T0, "html_url": "https://x/run/9"},
        {"id": 8, "name": "CI", "head_branch": "main", "event": "pull_request", "conclusion": "cancelled", "created_at": T0,
         "updated_at": T0, "run_started_at": T0, "html_url": "https://x/run/8"},
        {"id": 7, "name": "CI", "head_branch": "main", "event": "push", "conclusion": "success", "created_at": OLD,
         "updated_at": OLD, "run_started_at": OLD, "html_url": "https://x/run/7"}]},
    "repos/acme/web/actions/runs/9/jobs?per_page=100": {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "failure", "runner_name": "box-light-1",
         "labels": ["self-hosted", "pmp-light"], "created_at": T0, "started_at": T1, "completed_at": T2,
         "html_url": "https://x/job/9"},
        {"name": "build", "status": "completed", "conclusion": "success", "runner_name": "GitHub Actions 5",
         "labels": ["ubuntu-latest"], "created_at": T0, "started_at": T0, "completed_at": T1,
         "html_url": "https://x/job/10"},
        {"name": "deploy", "status": "completed", "conclusion": "skipped", "runner_name": None, "labels": ["ubuntu-latest"],
         "created_at": T0, "started_at": T0, "completed_at": T0, "html_url": "https://x/job/11"}]},
    # A self-hosted job whose runner is not listed (removed since): it keeps its pool.
    "repos/acme/web/actions/runs/8/jobs?per_page=100": {"jobs": [
        {"name": "e2e", "status": "completed", "conclusion": "cancelled", "runner_name": "old-light-9",
         "labels": ["self-hosted", "pmp-light"], "created_at": T0, "started_at": T1, "completed_at": T1,
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
        return FAKE[re.sub(r"&created=[^&]*", "", path)]


def rows(text, metric):
    return [l for l in text.splitlines() if l.startswith(metric + "{")]


class ExporterTest(unittest.TestCase):
    def setUp(self):
        exporter.DONE_JOBS.clear()
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

    def test_runs_finished_counts_the_window(self):
        out = self.render()
        self.assertEqual(sorted(rows(out, "github_runs_finished")), [
            'github_runs_finished{repo="web",workflow="CI",conclusion="cancelled"} 1',
            'github_runs_finished{repo="web",workflow="CI",conclusion="failure"} 1'])
        # Paging asks with the window's cutoff, URL-encoded.
        self.assertTrue(any("status=completed&created=%3E%3D" in c for c in exporter.get.calls))

    def test_jobs_finished_by_pool(self):
        self.assertEqual(sorted(rows(self.render(), "github_jobs_finished")), [
            'github_jobs_finished{pool="github-hosted",repo="web",workflow="CI",conclusion="skipped"} 1',
            'github_jobs_finished{pool="github-hosted",repo="web",workflow="CI",conclusion="success"} 1',
            'github_jobs_finished{pool="pmp-light",repo="web",workflow="CI",conclusion="cancelled"} 1',
            'github_jobs_finished{pool="pmp-light",repo="web",workflow="CI",conclusion="failure"} 1'])

    def test_queue_and_duration_quantiles(self):
        out = self.render()
        q = {r.rsplit(" ", 1)[0]: int(r.rsplit(" ", 1)[1]) for r in rows(out, "github_job_queue_seconds")}
        # pmp-light: lint waited 60s, e2e 60s. The GitHub-hosted build waited 0s.
        # The skipped job had no runner and gives no sample.
        self.assertEqual(q['github_job_queue_seconds{pool="pmp-light",quantile="0.5"}'], 60)
        self.assertEqual(q['github_job_queue_seconds{pool="github-hosted",quantile="0.9"}'], 0)
        self.assertEqual(len(q), 4)
        d = {r.rsplit(" ", 1)[0]: int(r.rsplit(" ", 1)[1]) for r in rows(out, "github_job_duration_seconds")}
        # pmp-light ran lint 240s and e2e 0s: median (nearest rank) 0, p90 240.
        self.assertEqual(d['github_job_duration_seconds{pool="pmp-light",workflow="CI",quantile="0.5"}'], 0)
        self.assertEqual(d['github_job_duration_seconds{pool="pmp-light",workflow="CI",quantile="0.9"}'], 240)
        self.assertEqual(d['github_job_duration_seconds{pool="github-hosted",workflow="CI",quantile="0.5"}'], 60)

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
            self.assertIn('github_runs_finished{repo="web",workflow="CI",conclusion="cancelled"} 1', out)
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

    def test_finished_jobs_are_fetched_once(self):
        self.render()
        exporter.get = Fake()
        exporter.render()
        self.assertNotIn("repos/acme/web/actions/runs/9/jobs?per_page=100", exporter.get.calls)
        self.assertIn("repos/acme/game/actions/runs/10/jobs?per_page=100", exporter.get.calls)  # active: always refetched

    def test_pool_of_pinned_runners(self):
        self.assertEqual(exporter.pool_of(["self-hosted", "macOS", "arm64"]), "macos")
        self.assertEqual(exporter.pool_of(["self-hosted", "Windows", "X64", "host-windows-media"]), "windows")
        self.assertEqual(exporter.pool_of(["self-hosted", "Linux", "X64"]), "other")
        self.assertEqual(exporter.pool_of(["self-hosted", "linux", "unity-full"]), "unity")
        self.assertEqual(exporter.pool_of(["self-hosted", "linux", "unity-withheld", "host-box"]), "unity")

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

    def test_no_token_everything_failed(self):
        out = self.render(broken=("orgs/", "repos/"))
        self.assertEqual(rows(out, "github_runner_state"), [])
        self.assertEqual(rows(out, "github_actions_job_waiting"), [])


class ColdStartBudgetTest(unittest.TestCase):
    """A day of runs at the playground's volume (2026-10-29: ~600 runs, ~3 jobs
    each): the cold start must spend at most HISTORY_CALL_BUDGET history job
    calls per scrape, and stop when GitHub says the budget is low."""

    RUNS = 600

    def setUp(self):
        exporter.DONE_JOBS.clear()
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
        self.assertIn(f'github_runs_finished{{repo="game",workflow="Tests",conclusion="success"}} {self.RUNS}', out)


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
        self.assertIn('github_runner_exporter_api_ok{source="orgs/acme",kind="runners"} 0', out)
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


ORIGINAL_GET = exporter.get

if __name__ == "__main__":
    unittest.main(verbosity=1)
