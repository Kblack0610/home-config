#!/usr/bin/env python3
"""github-runner-exporter - what every self-hosted GitHub Actions runner is doing.

WHY THIS EXISTS:

CI for BlackNBrownStudios runs on home hardware: the platform pmp-light /
pmp-heavy slots and the mac-mini (repo-scoped on platform), the org Unity pool,
and repo-scoped Unity runners on single game repos (hp-victus-playground on
unity-core-playground, hp-victus-unity-2 on dodginballs). A self-hosted job
WAITS for a runner instead of failing, so a dead slot shows up as a PR sitting
queued, not as a red check. Before this, the only view was `gh api` one repo at
a time, and it could not say what a busy runner was busy WITH.

The question the dashboard answers is "what is running where, and what is
waiting", so the main series is github_runner_state: one row per runner with
the repo / workflow / job_name / branch / job URL it is running and for how
long. (`job_name`, never `job`: Prometheus owns the `job` label for the scrape
target and renames ours to exported_job, which put "github-runner-exporter" in
every job column of the first deploy.)
GitHub's runner object does not carry its current job, so that comes from the
other side: every in-progress run in $RUN_REPOS, its jobs, matched on
job.runner_name.

ROSTER-FIRST for slot counts, same reason as apps/fleet-exporter: GitHub only
lists runners that registered, so a slot whose ansible play never ran would
otherwise be invisible. $RUNNER_ROSTER mirrors ansible/inventory.yml and
scripts/check-runner-roster.py keeps them equal. Each missing slot is a
state="missing" row. Runners outside the roster (the repo-scoped Unity ones
are not ansible-managed) are listed but not counted against anything.

Fetched on every scrape, no background loop: Prometheus's scrape interval is the
cadence (GitHub has no push API for runner state). Calls run on a small thread
pool so a scrape stays well inside the ServiceMonitor timeout.

A failed source never reads as "offline": its runners are simply absent and
github_runner_exporter_api_ok{source} says why. Missing-slot rows and pool
counts are only emitted when every runner source answered.

Alerting is NOT here. Sentinel (~/.agent/watches/*runners-online.yaml) is the
notification voice for runner pools; this is the screen.
"""
import json
import os
import re
import time
import urllib.error
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
# Completed runs per repo mined for "what does this runner / pool run". Wider
# than the Recent runs table because most runs are GitHub-hosted: at 15,
# platform showed one self-hosted workflow. Jobs are cached, so this costs
# ~HISTORY_PER_REPO calls per repo once, then one per newly finished run.
HISTORY_PER_REPO = int(os.environ.get("HISTORY_PER_REPO", "60"))
# run id -> its jobs, for COMPLETED runs only: they never change, so each is
# fetched once and the steady-state cost is one call per newly finished run.
# Pruned every scrape to the runs still in the recent window.
DONE_JOBS = {}
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
API_ERRORS = (urllib.error.URLError, OSError, ValueError, KeyError)


def get(path):
    req = urllib.request.Request(f"{API}/{path}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def runners(source):
    out, page = [], 1
    while True:
        data = get(f"{source}/actions/runners?per_page=100&page={page}")
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
    return "macos" if "macOS" in labels else "other"


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


def esc(v):
    return re.sub(r'(["\\])', r"\\\1", str(v)).replace("\n", " ")


def labels_str(d):
    return ",".join(f'{k}="{esc(v)}"' for k, v in d.items())


def repo_activity(repo):
    """One repo's active runs and their jobs, plus recent completed runs."""
    out = {"repo": repo, "ok": True, "counts": {}, "jobs": [], "recent": [], "history": []}
    try:
        active = []
        for status in ("in_progress", "queued"):
            data = get(f"repos/{repo}/actions/runs?status={status}&per_page={MAX_RUNS_PER_REPO}")
            out["counts"][status] = data.get("total_count", 0)
            active += data.get("workflow_runs", [])
        for run in active:
            jobs = get(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100").get("jobs", [])
            if not jobs and run.get("status") == "queued":
                # A queued run with no jobs never gets a runner. GitHub leaves
                # these behind (one platform schedule run sat like this from
                # 2026-09-13), and they hold the queued count up forever, so
                # list it as a waiting row with its age rather than hide it.
                jobs = [{"name": "(run has no jobs)", "status": "queued", "created_at": run.get("created_at"),
                         "head_branch": run.get("head_branch"), "labels": [], "html_url": run.get("html_url", "")}]
            for job in jobs:
                job["_workflow"] = run.get("name") or job.get("workflow_name", "")
                out["jobs"].append(job)
        data = get(f"repos/{repo}/actions/runs?status=completed&per_page={max(RECENT_PER_REPO, HISTORY_PER_REPO)}")
        out["history"] = data.get("workflow_runs", [])
        out["recent"] = out["history"][:RECENT_PER_REPO]
        # Uncached finished runs in parallel: a cold cache is ~HISTORY_PER_REPO
        # calls per repo, which sequentially took 51s, past the scrape timeout.
        todo = [run["id"] for run in out["history"] if run["id"] not in DONE_JOBS]
        with ThreadPoolExecutor(max_workers=6) as ex:
            for rid, jobs in zip(todo, ex.map(lambda i: get(f"repos/{repo}/actions/runs/{i}/jobs?per_page=100").get("jobs", []), todo)):
                DONE_JOBS[rid] = jobs
    except API_ERRORS:
        out["ok"] = False
    return out


def render():
    now = time.time()
    sources = [f"orgs/{o}" for o in ORGS] + [f"repos/{r}" for r in RUN_REPOS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        runner_futs = {s: pool.submit(runners, s) for s in sources}
        repo_futs = [pool.submit(repo_activity, r) for r in RUN_REPOS]
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
               "state": "", "repo": "", "workflow": "", "job_name": "", "branch": "", "url": ""}
        value = 0
        if r.get("status") != "online":
            lab["state"] = "offline"
        elif r.get("busy"):
            lab["state"] = "running"
            if r["name"] in running:
                repo, j = running[r["name"]]
                lab.update(repo=repo.split("/", 1)[1], workflow=j["_workflow"], job_name=j.get("name", ""),
                           branch=j.get("head_branch") or "", url=j.get("html_url", ""))
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

    if runners_ok:
        registered = {}
        for scope, r in seen.values():
            names = [l["name"] for l in r.get("labels", [])]
            k = (host_of(r["name"], names), pool_of(names))
            registered[k] = registered.get(k, 0) + 1
        for host, pool_, slots in ROSTER:
            for n in range(registered.get((host, pool_), 0), slots):
                lab = {"name": f"{host} {pool_} slot {n + 1}", "host": host, "pool": pool_, "scope": "roster",
                       "state": "missing", "repo": "", "workflow": "", "job_name": "", "branch": "", "url": ""}
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
        lab = {"repo": repo.split("/", 1)[1], "workflow": j["_workflow"], "job_name": j.get("name", ""),
               "branch": j.get("head_branch") or "", "status": j.get("status", ""),
               "labels": ",".join(j.get("labels") or []), "url": j.get("html_url", "")}
        L.append(f"github_actions_job_waiting{{{labels_str(lab)}}} {int(now - created) if created else 0}")

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
        "# HELP github_runner_last_job The last job this runner finished (within the recent-runs window); value is when",
        "# TYPE github_runner_last_job gauge",
    ]
    for rn, (done, repo, wf, j) in sorted(last.items()):
        lab = labels_str({"name": rn, "last_repo": repo.split("/", 1)[1], "last_workflow": wf,
                          "last_job": j.get("name", ""), "last_conclusion": j.get("conclusion") or "",
                          "last_url": j.get("html_url", "")})
        L.append(f"github_runner_last_job{{{lab}}} {int(done)}")
    L += [
        "# HELP github_pool_workflow A workflow this pool ran recently; value is when it last did",
        "# TYPE github_pool_workflow gauge",
    ]
    for (pool_, repo, wf), done in sorted(pool_wf.items()):
        L.append(f"github_pool_workflow{{{labels_str({'pool': pool_, 'repo': repo, 'workflow': wf})}}} {int(done)}")
    live = {run["id"] for a in acts for run in a["history"]}
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
