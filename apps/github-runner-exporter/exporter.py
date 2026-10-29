#!/usr/bin/env python3
"""github-runner-exporter - what every self-hosted GitHub Actions runner is doing.

WHY THIS EXISTS:

CI for BlackNBrownStudios runs on home hardware: the platform pmp-light /
pmp-heavy slots and the mac-mini (repo-scoped on platform), and the org Unity
pool, which serves every game repo. A self-hosted job
WAITS for a runner instead of failing, so a dead slot shows up as a PR sitting
queued, not as a red check. Before this, the only view was `gh api` one repo at
a time, and it could not say what a busy runner was busy WITH.

The question the dashboard answers is "what is running where, and what is
waiting", so the main series is github_runner_state: one row per runner with
the repo / workflow / job_name / branch / pr / event / title / job URL it is
running and for how long. (`job_name`, never `job`: Prometheus owns the `job` label for the scrape
target and renames ours to exported_job, which put "github-runner-exporter" in
every job column of the first deploy.)
GitHub's runner object does not carry its current job, so that comes from the
other side: every in-progress run in $RUN_REPOS, its jobs, matched on
job.runner_name.

ROSTER-FIRST for slot counts, same reason as apps/fleet-exporter: GitHub only
lists runners that registered, so a slot whose ansible play never ran would
otherwise be invisible. $RUNNER_ROSTER mirrors ansible/inventory.yml and
scripts/check-runner-roster.py keeps them equal. Each missing slot is a
state="missing" row. Runners outside the roster (m1-playground-mac is not
ansible-managed) are listed but not counted against anything.

Fetched on every scrape, no background loop: Prometheus's scrape interval is the
cadence (GitHub has no push API for runner state). Calls run on a small thread
pool so a scrape stays well inside the ServiceMonitor timeout.

A failed source never reads as "offline": its runners are simply absent and
github_runner_exporter_api_ok{source} says why. Missing-slot rows and pool
counts are only emitted when every runner source answered.

FINISHED WORK is read over a 24h window: every completed run CREATED in the
last day, and each one's jobs. That is what pass % per pool and per workflow,
queue time and job duration are computed from (github_runs_finished,
github_jobs_finished, github_job_queue_seconds, github_job_duration_seconds),
here rather than in PromQL because 24h of per-job samples is not in
Prometheus. The run list is re-read every HISTORY_REFRESH_SECS, and each
finished run's jobs are fetched once and cached.

Alerting is NOT here. Sentinel (~/.agent/watches/*runners-online.yaml) is the
notification voice for runner pools; this is the screen.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
PORT = int(os.environ.get("PORT", "9320"))
ORGS = os.environ.get("RUNNER_ORGS", "BlackNBrownStudios").split()
# Repos whose repo-scoped runners, active runs and recent runs are read.
RUN_REPOS = os.environ.get("RUN_REPOS", "BlackNBrownStudios/platform").split()
RECENT_PER_REPO = int(os.environ.get("RECENT_PER_REPO", "15"))
# Finished work is every completed run created in this window: the span pass %,
# queue time and "what does this runner / pool run" are judged over.
HISTORY_WINDOW_SECS = 86400
# How often the window's run list is re-read. GitHub has no push API, and the
# watched repos finish ~500 runs a day (2026-10-29), ~10 pages of 100: re-read
# on every 1-minute scrape that is ~600 calls/h of a 5000/h limit the token
# shares with every gh user on the account. Every 10 minutes it is ~60/h, and a
# scrape in between reuses the cached list, so the finished numbers lag by at
# most this much.
HISTORY_REFRESH_SECS = 600
# repo -> (fetched at, completed runs in the window, newest first).
HISTORY = {}
# run id -> its jobs, for COMPLETED runs only: they never change, so each is
# fetched once and the steady-state cost is one call per newly finished run.
# Pruned every scrape to the runs still in the window.
DONE_JOBS = {}
# The token's 5000 calls/h are per USER, shared with every gh session on the
# account and with apps/ci-runner-governor, and live runner state must never
# starve for history. So history's job fetches (the only part that grows with
# the backlog: a cold cache is ~500 calls) get a fixed budget per scrape, shared
# by all repos and spent newest run first, and stop altogether while GitHub says
# fewer than HISTORY_FLOOR calls are left. A cold start fills over ~10 scrapes.
# Until a run's jobs are cached it counts in the run-level series and is
# missing from the job-level ones. The run list itself is not budgeted: it is
# at most 10 pages per repo per HISTORY_REFRESH_SECS.
HISTORY_CALL_BUDGET = int(os.environ.get("HISTORY_CALL_BUDGET", "50"))
HISTORY_FLOOR = int(os.environ.get("HISTORY_FLOOR", "1000"))
# What GitHub last said about the budget, and when to call it again after it
# refused (403/429 at zero remaining, or a secondary limit's Retry-After).
# While refused every call fails here without going out, instead of each scrape
# re-asking ~40 times for the rest of the hour.
RATE = {"remaining": None, "until": 0.0}
# Calls made, by kind: what spends the budget, readable live.
CALLS = {}
BUDGET = {"left": 0}
LOCK = threading.Lock()
# Bounds the jobs fan-out per repo per status, so one repo with a deep queue
# cannot eat the API budget or the scrape timeout.
MAX_RUNS_PER_REPO = int(os.environ.get("MAX_RUNS_PER_REPO", "15"))
# host:pool:slots, whitespace separated.
ROSTER = []
for entry in os.environ.get("RUNNER_ROSTER", "").split():
    host, pool, slots = entry.split(":")
    ROSTER.append((host, pool, int(slots)))
ROSTER_HOSTS = sorted({h for h, _, _ in ROSTER}, key=len, reverse=True)
POOL_LABELS = ("pmp-light", "pmp-heavy", "unity")
# A queued run with no jobs this old never gets a runner (GitHub creates a run's
# jobs within seconds). It is labelled stuck="true" so boards can list it apart
# instead of letting its age stand for the queue's.
JOBLESS_STUCK_SECS = 3600
API_ERRORS = (urllib.error.URLError, OSError, ValueError, KeyError)


class RateLimited(urllib.error.URLError):
    pass


def note_rate(headers, code=None):
    left = headers.get("X-RateLimit-Remaining") if headers else None
    with LOCK:
        if left is not None and left.isdigit():
            RATE["remaining"] = int(left)
        if code in (403, 429) and (left == "0" or headers.get("Retry-After")):
            reset = headers.get("X-RateLimit-Reset") or ""
            retry = headers.get("Retry-After") or ""
            RATE["until"] = max(RATE["until"], float(reset) if left == "0" and reset.isdigit()
                                else time.time() + (int(retry) if retry.isdigit() else 60))


def get(path, kind="other"):
    if time.time() < RATE["until"]:
        raise RateLimited("GitHub rate limit: backing off until reset")
    with LOCK:
        CALLS[kind] = CALLS.get(kind, 0) + 1
    req = urllib.request.Request(f"{API}/{path}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            note_rate(r.headers)
            return json.load(r)
    except urllib.error.HTTPError as e:
        note_rate(e.headers, e.code)
        raise


def spend(n):
    """Take up to n calls from this scrape's history budget; returns how many."""
    with LOCK:
        k = max(0, min(n, BUDGET["left"]))
        BUDGET["left"] -= k
        return k


def runners(source):
    out, page = [], 1
    while True:
        data = get(f"{source}/actions/runners?per_page=100&page={page}", "runners")
        out.extend(data.get("runners", []))
        if len(out) >= data.get("total_count", 0) or not data.get("runners"):
            return out
        page += 1


def ts(s):
    """GitHub ISO-8601 'Z' timestamp -> epoch seconds, parsed as explicit UTC."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def pool_of(labels):
    for p in POOL_LABELS:
        if p in labels:
            return p
    # Full suite runs ask for unity-full, which only unity pool runners carry.
    # apps/ci-runner-governor swaps both for unity-withheld on a runner it holds
    # back; that runner is still in the pool.
    if "unity-full" in labels or "unity-withheld" in labels:
        return "unity"
    if "macOS" in labels:
        return "macos"
    # GitHub matches labels without regard to case, and the runner's own label
    # is "Windows".
    return "windows" if any(l.lower() == "windows" for l in labels) else "other"


def host_of(name, labels):
    # The unity pool role stamps host-<inventory_hostname>; the others do not,
    # but name the runner <inventory_hostname>-..., so match roster hosts as
    # name prefixes (longest first). Unrostered names fall back to the text
    # before the first pool-ish word.
    for l in labels:
        if l.startswith("host-"):
            return l[5:]
    for h in ROSTER_HOSTS:
        if name == h or name.startswith(h + "-"):
            return h
    m = re.match(r"(.+?)-(light|heavy|unity|playground|mac)\b", name)
    return m.group(1) if m else "unknown"


def quantile(xs, q):
    """Nearest-rank quantile of a sorted, non-empty list."""
    return xs[max(0, -(-round(q * 100) * len(xs) // 100) - 1)]


QUANTILES = ("0.5", "0.9")


def esc(v):
    return re.sub(r'(["\\])', r"\\\1", str(v)).replace("\n", " ")


def labels_str(d):
    return ",".join(f'{k}="{esc(v)}"' for k, v in d.items())


TITLE_MAX = 60


def run_labels(run):
    """The run's PR number, trigger and title: what a branch name alone does not say.

    `pull_requests` lists open same-repo PRs whose head is this run's; it is empty
    for a fork PR and for a branch with no PR, so `pr` is blank then, not guessed.
    """
    prs = run.get("pull_requests") or []
    title = (run.get("display_title") or "").splitlines()[0] if run.get("display_title") else ""
    return {"pr": str(prs[0]["number"]) if prs and prs[0].get("number") else "",
            "event": run.get("event") or "", "title": title[:TITLE_MAX]}


NO_RUN = {"pr": "", "event": "", "title": ""}

# Only what render() reads is kept from a finished run or job: a day of raw run
# objects (each carries two full repository objects) is tens of MB, and the pod
# has 64Mi.
RUN_KEYS = ("id", "name", "conclusion", "created_at", "updated_at", "run_started_at", "head_branch", "event", "html_url")
JOB_KEYS = ("name", "conclusion", "runner_name", "labels", "created_at", "started_at", "completed_at", "html_url")


def slim(d, keys):
    return {k: d.get(k) for k in keys}


def finished_runs(repo, now):
    """Completed runs created in the window, newest first.

    GitHub caps a filtered run listing at 1000 results; the busiest repo is
    ~600 a day. Pages after the first are read in parallel, and a run that
    shifts across a page boundary while they are read is de-duplicated by id.
    """
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - HISTORY_WINDOW_SECS))
    q = f"repos/{repo}/actions/runs?status=completed&created={urllib.parse.quote('>=' + since)}&per_page=100"
    first = get(f"{q}&page=1", "history_runs")
    pages = [first]
    n = -(-first.get("total_count", 0) // 100)
    if n > 1:
        with ThreadPoolExecutor(max_workers=4) as ex:
            pages += list(ex.map(lambda p: get(f"{q}&page={p}", "history_runs"), range(2, n + 1)))
    runs = {r["id"]: slim(r, RUN_KEYS) for page in pages for r in page.get("workflow_runs", [])}
    return [runs[i] for i in sorted(runs, reverse=True)]


def low_budget():
    return RATE["remaining"] is not None and RATE["remaining"] < HISTORY_FLOOR


def repo_activity(repo, now):
    """One repo's active runs and their jobs, plus the window's completed runs."""
    out = {"repo": repo, "ok": True, "counts": {}, "jobs": [], "recent": [], "history": []}
    try:
        active = []
        for status in ("in_progress", "queued"):
            data = get(f"repos/{repo}/actions/runs?status={status}&per_page={MAX_RUNS_PER_REPO}", "active_runs")
            out["counts"][status] = data.get("total_count", 0)
            active += data.get("workflow_runs", [])
        for run in active:
            jobs = get(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100", "active_jobs").get("jobs", [])
            if not jobs and run.get("status") == "queued":
                # A queued run with no jobs never gets a runner. GitHub leaves
                # these behind (one platform schedule run sat like this from
                # 2026-09-13), and they hold the queued count up forever, so
                # list it as a waiting row with its age rather than hide it.
                jobs = [{"name": "(run has no jobs)", "status": "queued", "created_at": run.get("created_at"),
                         "head_branch": run.get("head_branch"), "labels": [], "html_url": run.get("html_url", ""),
                         "_jobless": True}]
            for job in jobs:
                job["_workflow"] = run.get("name") or job.get("workflow_name", "")
                job["_run"] = run_labels(run)
                out["jobs"].append(job)
        cached = HISTORY.get(repo)
        if (not cached or now - cached[0] >= HISTORY_REFRESH_SECS) and not low_budget():
            cached = HISTORY[repo] = (now, finished_runs(repo, now))
        if not cached:
            return out
        # The cached list was cut at fetch time; cut again so a run ages out on
        # time between refreshes.
        out["history"] = [r for r in cached[1] if (ts(r.get("created_at")) or 0) >= now - HISTORY_WINDOW_SECS]
        out["recent"] = out["history"][:RECENT_PER_REPO]
        # Uncached finished runs in parallel, newest first, within the scrape's
        # budget (see HISTORY_CALL_BUDGET): sequentially, 60 of them took 51s.
        todo = [run["id"] for run in out["history"] if run["id"] not in DONE_JOBS]
        todo = todo[:spend(len(todo))]

        def jobs_of(rid):
            # One failed call must not discard the ones that answered.
            try:
                return get(f"repos/{repo}/actions/runs/{rid}/jobs?per_page=100", "history_jobs").get("jobs", [])
            except API_ERRORS:
                return None
        with ThreadPoolExecutor(max_workers=6) as ex:
            for rid, jobs in zip(todo, ex.map(jobs_of, todo)):
                if jobs is not None:
                    DONE_JOBS[rid] = [slim(j, JOB_KEYS) for j in jobs]
    except API_ERRORS:
        out["ok"] = False
    return out


def render():
    now = time.time()
    sources = [f"orgs/{o}" for o in ORGS] + [f"repos/{r}" for r in RUN_REPOS]
    BUDGET["left"] = 0 if low_budget() else HISTORY_CALL_BUDGET
    with ThreadPoolExecutor(max_workers=8) as pool:
        runner_futs = {s: pool.submit(runners, s) for s in sources}
        repo_futs = [pool.submit(repo_activity, r, now) for r in RUN_REPOS]
        acts = [f.result() for f in repo_futs]

    L = [
        "# HELP github_runner_exporter_api_ok 1 if this GitHub source answered this scrape",
        "# TYPE github_runner_exporter_api_ok gauge",
    ]
    seen, runners_ok = {}, True
    for src, fut in runner_futs.items():
        try:
            for r in fut.result():
                seen.setdefault(r["id"], ("org" if src.startswith("orgs/") else src.split("/", 1)[1], r))
            ok = 1
        except API_ERRORS:
            ok, runners_ok = 0, False
        L.append(f'github_runner_exporter_api_ok{{source="{esc(src)}",kind="runners"}} {ok}')
    for a in acts:
        L.append(f'github_runner_exporter_api_ok{{source="repos/{esc(a["repo"])}",kind="runs"}} {1 if a["ok"] else 0}')

    # runner_name -> the job it is running right now.
    running = {}
    waiting = []
    for a in acts:
        for j in a["jobs"]:
            if j.get("status") == "in_progress" and j.get("runner_name"):
                running[j["runner_name"]] = (a["repo"], j)
            elif j.get("status") in ("queued", "waiting", "pending", "requested"):
                waiting.append((a["repo"], j))

    L += [
        "# HELP github_runner_state One row per runner: state is running|idle|offline|missing; value is seconds the current job has run (0 otherwise)",
        "# TYPE github_runner_state gauge",
    ]
    by_state, online = {}, {}
    for scope, r in sorted(seen.values(), key=lambda s: s[1]["name"]):
        names = [l["name"] for l in r.get("labels", [])]
        host, pool_ = host_of(r["name"], names), pool_of(names)
        lab = {"name": r["name"], "host": host, "pool": pool_, "scope": scope,
               "state": "", "repo": "", "workflow": "", "job_name": "", "branch": "", "url": "", **NO_RUN}
        value = 0
        if r.get("status") != "online":
            lab["state"] = "offline"
        elif r.get("busy"):
            lab["state"] = "running"
            if r["name"] in running:
                repo, j = running[r["name"]]
                lab.update(repo=repo.split("/", 1)[1], workflow=j["_workflow"], job_name=j.get("name", ""),
                           branch=j.get("head_branch") or "", url=j.get("html_url", ""), **j["_run"])
                started = ts(j.get("started_at"))
                value = int(now - started) if started else 0
            else:
                # Busy on a job in a repo outside $RUN_REPOS, or one that started
                # between the two API reads. Say so instead of a blank row.
                lab["repo"] = "(not watched)"
        else:
            lab["state"] = "idle"
        if lab["state"] != "offline":
            online[(host, pool_)] = online.get((host, pool_), 0) + 1
        by_state[lab["state"]] = by_state.get(lab["state"], 0) + 1
        L.append(f"github_runner_state{{{labels_str(lab)}}} {value}")

    L += [
        "# HELP github_runner_withheld A unity pool runner apps/ci-runner-governor holds back from new jobs",
        "# TYPE github_runner_withheld gauge",
    ]
    for scope, r in sorted(seen.values(), key=lambda s: s[1]["name"]):
        names = [l["name"] for l in r.get("labels", [])]
        if "unity-withheld" in names:
            L.append(f"github_runner_withheld{{{labels_str({'name': r['name'], 'host': host_of(r['name'], names)})}}} 1")

    if runners_ok:
        registered = {}
        for scope, r in seen.values():
            names = [l["name"] for l in r.get("labels", [])]
            k = (host_of(r["name"], names), pool_of(names))
            registered[k] = registered.get(k, 0) + 1
        for host, pool_, slots in ROSTER:
            for n in range(registered.get((host, pool_), 0), slots):
                lab = {"name": f"{host} {pool_} slot {n + 1}", "host": host, "pool": pool_, "scope": "roster",
                       "state": "missing", "repo": "", "workflow": "", "job_name": "", "branch": "", "url": "", **NO_RUN}
                by_state["missing"] = by_state.get("missing", 0) + 1
                L.append(f"github_runner_state{{{labels_str(lab)}}} 0")

    L += [
        "# HELP github_runners_by_state Count of runners in each state (absent while a runner source failed for missing)",
        "# TYPE github_runners_by_state gauge",
    ]
    for state in ("running", "idle", "offline", "missing"):
        if state == "missing" and not runners_ok:
            continue
        L.append(f'github_runners_by_state{{state="{state}"}} {by_state.get(state, 0)}')

    L += [
        "# HELP github_runner_pool_expected Slots the roster says this host runs in this pool",
        "# TYPE github_runner_pool_expected gauge",
        "# HELP github_runner_pool_online Online runners in this host/pool; absent while any runner source failed",
        "# TYPE github_runner_pool_online gauge",
    ]
    for host, pool_, slots in ROSTER:
        L.append(f'github_runner_pool_expected{{host="{host}",pool="{pool_}"}} {slots}')
    if runners_ok:
        for host, pool_ in sorted({(h, p) for h, p, _ in ROSTER} | set(online)):
            L.append(f'github_runner_pool_online{{host="{host}",pool="{pool_}"}} {online.get((host, pool_), 0)}')

    L += [
        "# HELP github_actions_job_waiting A job waiting for a runner; value is seconds since it was queued",
        "# TYPE github_actions_job_waiting gauge",
    ]
    for repo, j in waiting:
        created = ts(j.get("created_at"))
        age = int(now - created) if created else 0
        lab = {"repo": repo.split("/", 1)[1], "workflow": j["_workflow"], "job_name": j.get("name", ""),
               "branch": j.get("head_branch") or "", "status": j.get("status", ""),
               "labels": ",".join(j.get("labels") or []), "pool": pool_of(j.get("labels") or []),
               "stuck": "true" if j.get("_jobless") and age > JOBLESS_STUCK_SECS else "false",
               "url": j.get("html_url", ""), **j["_run"]}
        L.append(f"github_actions_job_waiting{{{labels_str(lab)}}} {age}")

    L += [
        "# HELP github_actions_runs Workflow runs in this status (queued, in_progress)",
        "# TYPE github_actions_runs gauge",
    ]
    for a in acts:
        for status, n in a["counts"].items():
            L.append(f'github_actions_runs{{repo="{esc(a["repo"])}",status="{status}"}} {n}')

    L += [
        "# HELP github_actions_run_recent A recently completed run; value is when it finished (epoch seconds)",
        "# TYPE github_actions_run_recent gauge",
        "# HELP github_actions_run_recent_duration_seconds How long that run took",
        "# TYPE github_actions_run_recent_duration_seconds gauge",
    ]
    for a in acts:
        for run in a["recent"]:
            done, started = ts(run.get("updated_at")), ts(run.get("run_started_at"))
            if not done:
                continue
            lab = labels_str({"repo": a["repo"].split("/", 1)[1], "workflow": run.get("name", ""),
                              "branch": run.get("head_branch") or "", "event": run.get("event", ""),
                              "conclusion": run.get("conclusion") or "", "url": run.get("html_url", "")})
            L.append(f"github_actions_run_recent{{{lab}}} {int(done)}")
            if started:
                L.append(f"github_actions_run_recent_duration_seconds{{{lab}}} {int(done - started)}")
    # What each runner actually did last, and which workflows each pool handles:
    # the answer to "what is this runner for", read from history instead of a
    # hand-written description that drifts from the workflow files.
    pool_of_runner = {}
    for scope, r in seen.values():
        pool_of_runner[r["name"]] = pool_of([l["name"] for l in r.get("labels", [])])
    last, pool_wf = {}, {}
    for a in acts:
        for run in a["history"]:
            for j in DONE_JOBS.get(run["id"], []):
                rn, done = j.get("runner_name"), ts(j.get("completed_at"))
                if not rn or not done or rn not in pool_of_runner:
                    continue  # GitHub-hosted, or a runner since removed
                if done > last.get(rn, (0,))[0]:
                    last[rn] = (done, a["repo"], run.get("name", ""), j)
                k = (pool_of_runner[rn], a["repo"].split("/", 1)[1], run.get("name", ""))
                pool_wf[k] = max(pool_wf.get(k, 0), done)
    L += [
        "# HELP github_runner_last_job The last job this runner finished (within the 24h window); value is when",
        "# TYPE github_runner_last_job gauge",
    ]
    for rn, (done, repo, wf, j) in sorted(last.items()):
        lab = labels_str({"name": rn, "last_repo": repo.split("/", 1)[1], "last_workflow": wf,
                          "last_job": j.get("name", ""), "last_conclusion": j.get("conclusion") or "",
                          "last_url": j.get("html_url", "")})
        L.append(f"github_runner_last_job{{{lab}}} {int(done)}")
    L += [
        "# HELP github_pool_workflow A workflow this pool ran in the last 24h; value is when it last did",
        "# TYPE github_pool_workflow gauge",
    ]
    for (pool_, repo, wf), done in sorted(pool_wf.items()):
        L.append(f"github_pool_workflow{{{labels_str({'pool': pool_, 'repo': repo, 'workflow': wf})}}} {int(done)}")

    # Finished work over the window. Runs count as soon as they are listed; a
    # run's jobs count once they are cached (HISTORY_CALL_BUDGET), so after
    # a restart the job-level numbers fill in over a few scrapes.
    runs_done, jobs_done, queue, took = {}, {}, {}, {}
    for a in acts:
        repo = a["repo"].split("/", 1)[1]
        for run in a["history"]:
            wf = run.get("name") or ""
            k = (repo, wf, run.get("conclusion") or "")
            runs_done[k] = runs_done.get(k, 0) + 1
            for j in DONE_JOBS.get(run["id"], []):
                rn, labels = j.get("runner_name"), j.get("labels") or []
                # A self-hosted job on a runner not listed this scrape (removed
                # since, or its source failed) still belongs to the pool it asked
                # for; everything else (GitHub's runners, or no runner at all,
                # as for a skipped job) is github-hosted.
                pool_ = (pool_of_runner.get(rn) or (pool_of(labels) if "self-hosted" in labels else None)
                         or "github-hosted")
                k = (pool_, repo, wf, j.get("conclusion") or "")
                jobs_done[k] = jobs_done.get(k, 0) + 1
                created, started, done = ts(j.get("created_at")), ts(j.get("started_at")), ts(j.get("completed_at"))
                if not rn or not started:
                    continue
                if created:
                    queue.setdefault(pool_, []).append(max(0, started - created))
                if done:
                    took.setdefault((pool_, wf), []).append(max(0, done - started))
    L += [
        "# HELP github_runs_finished Completed runs created in the last 24h",
        "# TYPE github_runs_finished gauge",
    ]
    for (repo, wf, c), n in sorted(runs_done.items()):
        L.append(f"github_runs_finished{{{labels_str({'repo': repo, 'workflow': wf, 'conclusion': c})}}} {n}")
    L += [
        "# HELP github_jobs_finished Completed jobs of runs created in the last 24h, by the pool that ran them (github-hosted = not our runners)",
        "# TYPE github_jobs_finished gauge",
    ]
    for (pool_, repo, wf, c), n in sorted(jobs_done.items()):
        L.append(f"github_jobs_finished{{{labels_str({'pool': pool_, 'repo': repo, 'workflow': wf, 'conclusion': c})}}} {n}")
    L += [
        "# HELP github_job_queue_seconds Time a finished job (last 24h) waited for a runner, started_at - created_at",
        "# TYPE github_job_queue_seconds gauge",
    ]
    for pool_, xs in sorted(queue.items()):
        xs.sort()
        for q in QUANTILES:
            L.append(f'github_job_queue_seconds{{{labels_str({"pool": pool_, "quantile": q})}}} {int(quantile(xs, float(q)))}')
    L += [
        "# HELP github_job_duration_seconds How long a finished job (last 24h) ran, completed_at - started_at",
        "# TYPE github_job_duration_seconds gauge",
    ]
    for (pool_, wf), xs in sorted(took.items()):
        xs.sort()
        for q in QUANTILES:
            L.append(f'github_job_duration_seconds{{{labels_str({"pool": pool_, "workflow": wf, "quantile": q})}}} {int(quantile(xs, float(q)))}')

    L += [
        "# HELP github_runner_exporter_api_calls_total GitHub API calls made since start, by kind",
        "# TYPE github_runner_exporter_api_calls_total counter",
    ]
    for kind, n in sorted(CALLS.items()):
        L.append(f'github_runner_exporter_api_calls_total{{kind="{kind}"}} {n}')
    L += [
        "# HELP github_runner_exporter_rate_remaining Calls GitHub says the token has left this hour",
        "# TYPE github_runner_exporter_rate_remaining gauge",
    ]
    if RATE["remaining"] is not None:
        L.append(f"github_runner_exporter_rate_remaining {RATE['remaining']}")

    # Pruned against the cached lists, not this scrape's: a scrape where a repo
    # failed (rate limit, network) must not throw away a day of cached jobs and
    # spend ~500 calls refilling them.
    live = {run["id"] for _, runs in HISTORY.values() for run in runs}
    for rid in [k for k in DONE_JOBS if k not in live]:
        del DONE_JOBS[rid]
    return "\n".join(L) + "\n"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body = render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            # Server only, never the API: a GitHub-aware probe would pull the pod
            # out of the Service exactly when api_ok=0 is the thing to see.
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(
        f"github-runner-exporter: orgs={ORGS} repos={len(RUN_REPOS)} roster={len(ROSTER)} "
        f"token={'set' if TOKEN else 'MISSING'} port={PORT}",
        flush=True,
    )
    HTTPServer(("", PORT), H).serve_forever()
