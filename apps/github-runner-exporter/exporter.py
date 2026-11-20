#!/usr/bin/env python3
"""github-runner-exporter - what every self-hosted GitHub Actions runner is doing.

WHY THIS EXISTS:

CI for BlackNBrownStudios runs on home hardware: the org light / heavy
slots, the org Unity pool, which serves every game repo, and the mac-mini
(repo-scoped on platform). A self-hosted job
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

TOPOLOGY-FIRST for slots, same reason as apps/fleet-exporter: GitHub only lists
runners that registered, so a slot whose ansible play never ran would otherwise
be invisible. $TOPOLOGY is apps/ci-topology/topology.json, rendered from
ansible/inventory.yml by scripts/render-ci-topology.py: every slot's name, host,
caps (the job kinds it takes) and, for a repo runner, its repo. Each slot with no
registered runner of its name is a state="missing" row. A runner is counted once
per cap (github_runner_pool_slot, pool=<cap>); one no slot names is listed but
counted against nothing.

Fetched on every scrape, no background loop: Prometheus's scrape interval is the
cadence (GitHub has no push API for runner state). Calls run on a small thread
pool, and history stops starting calls at HISTORY_DEADLINE_SECS, so a scrape
stays well inside the ServiceMonitor timeout.

A failed source never reads as "offline": its runners are simply absent and
github_runner_exporter_api_ok{source} says why. Missing-slot rows and pool
counts are only emitted when every runner source answered.

FINISHED WORK is read over a 24h window: every completed run CREATED in the
last day, and each one's jobs, keyed by job kind (pool=<kind>, as ci_runner_job
names a running job's). That is what pass % per kind and per workflow,
queue time and job duration are computed from (github_runs_finished,
github_jobs_finished, github_job_queue_seconds, github_job_duration_seconds),
here rather than in PromQL because 24h of per-job samples is not in
Prometheus. The run list is re-read every HISTORY_REFRESH_SECS, and each
finished run's jobs are fetched once and cached.

The same cache is served as JSON at /api/ci/details?cursor=&limit=100: each
finished run in the window with every job of its exact attempt (paged, steps
slimmed), whether that read was complete, and a changes cursor. It is the one
collector of job detail for the RUN_REPOS and FEED_REPOS repos; schedules-sync reads it through
the API server's Service proxy every 10 minutes and keeps the 90-day archive, and
fetches job detail itself only for repos outside the `repos` list this serves.
The feed's `runs` are the run half: each run of either list each time its state
moves (queued, in progress, completed, a new attempt), from the reads above at no
extra call, so schedules-sync can write its run ledger from here instead of
polling actions/runs itself. A run that leaves the active lists while older than
the window, which the completed list cannot show (a re-run of an old run), is read
by id with the history, from its budget, until it reads finished. `repos` and
`forgejo_repos` are the one list of CI repos schedules-sync syncs: it polls the
Forgejo ones itself (their runs are not in this feed).

Those totals are trailing-window gauges: runs enter and leave them at once, so
no PromQL over them can say when anything happened. github_runs_by_hour and
github_run_failed say that instead, from the same list and with no extra calls.

FORGEJO ACTIONS (git.kblab.me) are rows in the same series with forge="forgejo"
and, where a series has a pool, pool="forgejo"; every GitHub row says forge="github". The runners are the
topology's slots with forge "forgejo" (one task each, ci-host's forgejo-runner-linux
role), and their state is the admin runners API (`admin/actions/runners`, so
FORGEJO_TOKEN needs read:admin): `active` reads running, `idle` idle, `offline`
offline, and a slot whose name never registered reads missing. Forgejo has no API
that says which runner runs which task, so a running slot carries no job, and no
slot carries a last job.
The API gives no time a job was queued, so a waiting job's age is from the first
scrape that saw it (reset on an exporter restart). github_job_queue_seconds for
pool="forgejo" is the same clock: a job that left the waiting list waited from the
first scrape that saw it to the first that missed it (a cancelled one counts too),
and a task no scrape saw waiting counts 0, so a wait reads within one scrape
interval (60s). It covers jobs since the exporter started, at most the window. Finished work is every task
created in the window, by its status. Off unless $FORGEJO_REPOS is set.

Alerting is NOT here. Sentinel (~/.agent/watches/*runners-online.yaml) is the
notification voice for runner pools; this is the screen.
"""
import json
import os
import re
import threading
import time
from collections import Counter, OrderedDict
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN", "")


def env_key(owner):
    """An owner as an env var suffix: upper-cased, anything but a letter or digit as _."""
    return re.sub(r"[^A-Z0-9]", "_", owner.upper())


def owner_tokens(env):
    """{GITHUB_TOKEN_<OWNER> suffix: token} for every non-empty one."""
    return {k[len("GITHUB_TOKEN_"):]: v for k, v in env.items() if k.startswith("GITHUB_TOKEN_") and v}


# The token for one owner's repos and org, from GITHUB_TOKEN_<OWNER> (env_key): a
# fine-grained PAT reads only its resource owner's private repos, so another
# owner's private repos need a token of their own. An owner with none uses
# GITHUB_TOKEN. Every token here is one user's (Kblack0610), so all of them draw
# on one 5000/h pool, and RATE stays keyed by scope, not by token.
OWNER_TOKENS = owner_tokens(os.environ)
PORT = int(os.environ.get("PORT", "9320"))
ORGS = os.environ.get("RUNNER_ORGS", "BlackNBrownStudios").split()

def load_topology(path):
    """apps/ci-topology/topology.json, rendered from ansible/inventory.yml: every CI host's
    site and the slots it gives CI, each with its caps (the job kinds it takes), and the
    runner label vocabulary."""
    with open(path) as f:
        return json.load(f)


RENDERED = load_topology(os.environ.get("TOPOLOGY", "/topology/topology.json"))
TOPOLOGY = RENDERED["hosts"]
# slot name -> {host, caps, repo, forge, borrows}
SLOTS = {s["name"]: {"host": h, "caps": s["caps"], "repo": s.get("repo"), "forge": s.get("forge", "github"),
                     "borrows": s.get("borrows")}
         for h, e in TOPOLOGY.items() for s in e["slots"]}
TOPOLOGY_HOSTS = sorted(TOPOLOGY, key=len, reverse=True)
# Repos whose repo-scoped runners, active runs and recent runs are read: RUN_REPOS, plus
# every repo a topology slot registers its runner to (a repo runner is listed only there).
RUN_REPOS = os.environ.get("RUN_REPOS", "BlackNBrownStudios/platform").split()
RUN_REPOS += sorted({s["repo"] for s in SLOTS.values() if s["repo"]} - set(RUN_REPOS))
# Repos read only for the /api/ci/details feed, their runs and job detail for schedules-sync:
# no runner listing (that needs admin on the repo, and the pool counts need every runner source
# to answer) and none of the metrics, which stay the pool's, RUN_REPOS'.
FEED_REPOS = [r for r in os.environ.get("FEED_REPOS", "").split() if r not in RUN_REPOS]
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
# run id -> the record /api/ci/details serves for it: repo, run, attempt, whether
# every job of that attempt was read, and seq, its place in the changes feed.
# Pruned with DONE_JOBS. The feed is the window's job detail for the repos in
# RUN_REPOS, for schedules-sync to archive; a reader that sends back the last
# `next` it got sees only records stored or changed since. seq restarts with the
# process, so each answer carries EPOCH and a reader that sees a new one starts
# from 0 again.
FEED = {}
# run id -> the run record /api/ci/details serves in `runs`: seq, forge, repo and
# the run (RUN_KEYS), stored again only when its state moved forward (STATUS_RANK,
# then the attempt). Shares FEED_SEQ, so one cursor reads both halves in order.
RUN_FEED = {}
FEED_SEQ = [0]
FEED_LOCK = threading.Lock()
EPOCH = int(time.time())
FEED_LIMIT = 100
# The tokens' 5000 calls/h are per USER, one pool for GITHUB_TOKEN and every
# GITHUB_TOKEN_<OWNER> (all Kblack0610's), shared with every gh session on the
# account and with apps/ci-runner-governor, and live runner state must never
# starve for history. So history's job fetches (the only part that grows with
# the backlog: a cold cache is ~2,400 calls) get a fixed budget per scrape, shared
# by all repos one run from each in turn (fetch_details), and stop altogether while GitHub says
# fewer than HISTORY_FLOOR calls are left. A cold start fills over ~50 scrapes.
# Until a run's jobs are cached it counts in the run-level series and is
# missing from the job-level ones. The run list itself is not budgeted: it is
# at most 10 pages per repo per HISTORY_REFRESH_SECS.
HISTORY_CALL_BUDGET = int(os.environ.get("HISTORY_CALL_BUDGET", "50"))
HISTORY_FLOOR = int(os.environ.get("HISTORY_FLOOR", "1000"))
# History's job fetches and run-list refreshes stop starting once a scrape has
# run this long. A scrape past the ServiceMonitor's 30s scrapeTimeout reads as
# up=0, every series here goes stale for that minute, and every board blanks
# with it (2026-11-03: p90 20-27s and 9 timeouts in a day, each restart's cold
# cache backfilling inside the scrape). Live runner and active-run reads are
# never cut; a cold cache fills over more scrapes instead.
HISTORY_DEADLINE_SECS = float(os.environ.get("HISTORY_DEADLINE_SECS", "8"))
# What GitHub last said about the budget, and when to call it again after it
# refused (403/429 at zero remaining, or a secondary limit's Retry-After).
# While refused every call fails here without going out, instead of each scrape
# re-asking ~40 times for the rest of the hour. Kept per scope (the path's first
# segment) because GitHub charges orgs/ calls to a different bucket than
# repos/ calls (2026-10-29: org runners had 4278 left while every repo call was
# refused), and one empty bucket must not hide the other's runners.
RATE = {}
# Calls GitHub charged, by kind: what spends the budget, readable live.
CALLS = {}
# Calls GitHub answered 304 Not Modified, by kind: free, counted apart.
NOT_MODIFIED = {}
# path -> (ETag, body) of its last 200. A conditional request that GitHub
# answers 304 is not charged to the limit, and most of a scrape re-reads lists
# that have not changed since the last one: on 2026-10-29 active runs, their
# jobs and the runner lists were ~1,200 of ~2,150 calls an hour. Bounded,
# least recently used out, since active runs' job paths come and go. The body
# is kept as bytes and parsed per call, so no caller shares a mutable object.
# Only paths a later scrape asks for again are kept: the history run list's
# path carries a moving created>= bound, and a completed run's jobs are read
# once into DONE_JOBS, so caching either only held dead bodies (~1 MB a run
# list page) until the pod was OOMKilled at 64Mi every ~11 minutes (2026-10-29).
ETAGS = OrderedDict()
ETAG_KINDS = frozenset({"runners", "active_runs", "active_jobs"})
# The live set is ~200 paths (runner pages, two run lists per repo, and up to
# MAX_RUNS_PER_REPO jobs lists per status per repo); the rest is headroom.
ETAG_MAX = 400
BUDGET = {"left": 0, "until": 0.0}
LOCK = threading.Lock()
# One render at a time: it rewrites the module caches above. /health does not take it.
RENDER_LOCK = threading.Lock()
# Bounds the jobs fan-out per repo per status, so one repo with a deep queue
# cannot eat the API budget or the scrape timeout.
MAX_RUNS_PER_REPO = int(os.environ.get("MAX_RUNS_PER_REPO", "15"))


def site_of(host):
    """"cloud" for a topology host off the home LAN, "local" for every other."""
    return TOPOLOGY.get(host, {}).get("site", "local")


# unity-build and unity-wide (a group's lead slot: aws-build-1, ms-a2), pools of their own so build and wide waits stay
# out of the gate pool's numbers. unity-wide is before unity: a wide job asks for both.
POOL_LABELS = ("light", "heavy", "unity-wide", "unity", "unity-build")
# The labels apps/ci-runner-governor swaps a runner's pool labels for while it holds it back.
# light-withheld: a repo runner while the governor has not lent it the Unity slot it borrows.
LABELS = RENDERED["labels"]
WITHHELD_LABELS = {LABELS["withheld"]: LABELS["pool"], LABELS["build_withheld"]: LABELS["build"],
                   LABELS["seat_withheld"]: "light"}
# A queued run with no jobs this old never gets a runner (GitHub creates a run's
# jobs within seconds). It is labelled stuck="true" so boards can list it apart
# instead of letting its age stand for the queue's.
JOBLESS_STUCK_SECS = 3600
API_ERRORS = (urllib.error.URLError, OSError, ValueError, KeyError)


class RateLimited(urllib.error.URLError):
    pass


def rate(scope):
    return RATE.setdefault(scope, {"remaining": None, "until": 0.0})


def note_rate(scope, headers, code=None):
    left = headers.get("X-RateLimit-Remaining") if headers else None
    with LOCK:
        r = rate(scope)
        if left is not None and left.isdigit():
            r["remaining"] = int(left)
        if code in (403, 429) and (left == "0" or headers.get("Retry-After")):
            reset = headers.get("X-RateLimit-Reset") or ""
            retry = headers.get("Retry-After") or ""
            r["until"] = max(r["until"], float(reset) if left == "0" and reset.isdigit()
                             else time.time() + (int(retry) if retry.isdigit() else 60))


def token_for(path):
    parts = path.split("/", 2)
    owner = env_key(parts[1]) if parts[0] in ("repos", "orgs") and len(parts) > 1 else ""
    return OWNER_TOKENS.get(owner, TOKEN)


def get(path, kind="other"):
    scope = path.split("/", 1)[0]
    if time.time() < rate(scope)["until"]:
        raise RateLimited(f"GitHub rate limit ({scope}): backing off until reset")
    req = urllib.request.Request(f"{API}/{path}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    token = token_for(path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with LOCK:
        cached = ETAGS.get(path) if kind in ETAG_KINDS else None
    if cached:
        req.add_header("If-None-Match", cached[0])
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            note_rate(scope, r.headers)
            body, etag = r.read(), r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        note_rate(scope, e.headers, e.code)
        if e.code == 304 and cached:
            with LOCK:
                NOT_MODIFIED[kind] = NOT_MODIFIED.get(kind, 0) + 1
                if path in ETAGS:
                    ETAGS.move_to_end(path)
            return json.loads(cached[1])
        with LOCK:
            CALLS[kind] = CALLS.get(kind, 0) + 1
        raise
    except API_ERRORS:
        with LOCK:
            CALLS[kind] = CALLS.get(kind, 0) + 1
        raise
    with LOCK:
        CALLS[kind] = CALLS.get(kind, 0) + 1
        if etag and kind in ETAG_KINDS:
            ETAGS[path] = (etag, body)
            ETAGS.move_to_end(path)
            while len(ETAGS) > ETAG_MAX:
                ETAGS.popitem(last=False)
    return json.loads(body)


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
    """ISO-8601 timestamp -> epoch seconds. The 'Z' form is parsed as explicit UTC; Forgejo
    writes its server's zone (Z on this one today), so an offset form is read with its offset."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        pass
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t.timestamp() if t.tzinfo else None


def pool_of(labels):
    for p in POOL_LABELS:
        if p in labels:
            return p
    # Full suite runs ask for unity-full, which only unity pool runners carry.
    if "unity-full" in labels:
        return "unity"
    # A runner apps/ci-runner-governor holds back is still in its pool.
    for label, pool in WITHHELD_LABELS.items():
        if label in labels:
            return pool
    if "macOS" in labels:
        return "macos"
    # GitHub matches labels without regard to case, and the runner's own label
    # is "Windows".
    return "windows" if any(l.lower() == "windows" for l in labels) else "other"


def caps_of(name, labels):
    """The job kinds a runner takes: its topology slot's caps, or for a runner no slot
    names (registered by hand, or since removed from inventory) the one its labels say."""
    slot = SLOTS.get(name)
    return slot["caps"] if slot else [pool_of(labels)]


def host_of(name, labels):
    # A topology slot names its host. Otherwise: the unity pool role stamps
    # host-<inventory_hostname>; the others name the runner <inventory_hostname>-...,
    # so match topology hosts as name prefixes (longest first). Other names fall
    # back to the text before the first pool-ish word.
    if name in SLOTS:
        return SLOTS[name]["host"]
    for l in labels:
        if l.startswith("host-"):
            return l[5:]
    for h in TOPOLOGY_HOSTS:
        if name == h or name.startswith(h + "-"):
            return h
    m = re.match(r"(.+?)-(light|heavy|unity|playground|mac)\b", name)
    return m.group(1) if m else "unknown"


def job_kind(name, runner_labels, job_labels):
    """The kind of job a busy runner runs, for ci_runner_job: forgejo for a Forgejo seat, repo for
    a repo runner on a borrowed slot, else the pool the job asks for, else the runner's one cap.
    None when that leaves it open (a slot of several caps on a job whose labels name none)."""
    slot = SLOTS.get(name, {})
    if slot.get("forge") == "forgejo":
        return "forgejo"
    if slot.get("repo") and slot.get("borrows"):
        return "repo"
    kind = pool_of(job_labels)
    if kind != "other":
        return kind
    caps = caps_of(name, runner_labels)
    return caps[0] if len(caps) == 1 else None


def done_kind(job, runner_labels):
    """The kind a finished job keys the history series by, as job_kind names a running one's for
    ci_runner_job, never a runner's first cap; open (its labels name no kind on a slot of several
    caps) is other. runner_labels is {name: [label]} of the runners listed this scrape. A
    self-hosted job on a runner not listed (removed since, or its source failed) still has the kind
    it asked for; everything else (GitHub's runners, or no runner at all, as for a skipped job) is
    github-hosted."""
    rn, labels = job.get("runner_name"), job.get("labels") or []
    if rn not in runner_labels and "self-hosted" not in labels:
        return "github-hosted"
    return job_kind(rn, runner_labels.get(rn, []), labels) or "other"


def github_hosted(job):
    """A job on GitHub's own runners: its runner group says so, or it asks for neither
    self-hosted nor a pool of ours (runs-on: [unity, unity-gate] is ours)."""
    if job.get("runner_group_name"):
        return job["runner_group_name"] == "GitHub Actions"
    labels = job.get("labels") or []
    return bool(labels) and "self-hosted" not in labels and pool_of(labels) == "other"


SLOT_KEYS = ("name", "host", "site", "state", "scope", "forge")


def slot_row(lab, cap, value):
    """A github_runner_pool_slot line: one runner row's identity, once per cap it takes."""
    return f"github_runner_pool_slot{{{labels_str({**{k: lab[k] for k in SLOT_KEYS}, 'pool': cap})}}} {value}"


def runner_label_sets(seen):
    """(scope, caps, lowercased labels) per registered runner, offline ones too (a stopped
    instance's runners stay registered). A runner the governor withholds counts the labels
    it gets back on admit: its caps, and unity-full on a full host's unity slot."""
    out = []
    for scope, r in seen:
        names = [l["name"] for l in r.get("labels", [])]
        caps = caps_of(r["name"], names)
        have = {l.lower() for l in names}
        if WITHHELD_LABELS.keys() & have:
            have |= set(caps)
            # A wide lead asks for unity too when admitted (governor lead_labels()).
            if "unity-wide" in caps:
                have.add("unity")
            if {"unity", "unity-wide"} & set(caps) and TOPOLOGY.get(host_of(r["name"], names), {}).get("full"):
                have.add("unity-full")
        out.append((scope, caps, have))
    return out


def wait_kind(repo, job, takers):
    """The pool label of a waiting job: the cap it asks for; when it names none (runs-on:
    self-hosted), the one kind the runners that can take it leave, else other, as job_kind
    names it once it runs. "github-hosted" for GitHub's own runners, "jobless" for a run with no jobs yet, and "unserved" when no
    registered runner this repo may use carries every label it asks for, so it waits
    forever (ar-1872). takers is runner_label_sets(), or None while a runner source
    failed, when nothing is called unserved."""
    labels = job.get("labels") or []
    if job.get("_jobless"):
        return "jobless"
    if github_hosted(job):
        return "github-hosted"
    cap = pool_of(labels)
    if takers is None:
        return cap
    want = {l.lower() for l in labels}
    org_ok = repo.split("/", 1)[0] in ORGS
    fits = [caps for scope, caps, have in takers
            if (scope == repo or (scope == "org" and org_ok)) and want <= have]
    if not fits:
        return "unserved"
    kinds = {c for caps in fits for c in caps}
    return cap if cap != "other" or len(kinds) != 1 else kinds.pop()


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
MAIN_BRANCHES = ("main", "master")


def run_scope(run):
    """main for a run of the default branch, branch for a pull request's or a work branch's.

    A red on main is everyone's problem; a red gate on a work branch is the gate doing its
    job, so the two are told apart before anyone reads a pass rate.
    """
    if run.get("event") != "pull_request" and run.get("head_branch") in MAIN_BRANCHES:
        return "main"
    return "branch"


RUN_KEYS = ("id", "name", "status", "conclusion", "created_at", "updated_at", "run_started_at", "head_branch", "event",
            "html_url", "run_attempt", "head_sha", "path", "display_title")
JOB_KEYS = ("id", "name", "conclusion", "runner_name", "labels", "created_at", "started_at", "completed_at", "html_url",
            "run_attempt")
# Steps are kept for the feed (the opened run's step fold), slimmed and held as one
# compact JSON blob per job, decoded only when the feed serves it: as dicts a
# 26-step job held ~12 KB, and the window's ~2,400 runs (~3 jobs each, 13 repos)
# filled the job cache past the pod's 96Mi before it reached the playground's runs.
STEP_KEYS = ("number", "name", "conclusion", "started_at", "completed_at")
# The conclusions listed run by run. A cancelled run is counted but not listed:
# most are a push superseding its own earlier run.
FAILED_CONCLUSIONS = ("failure", "timed_out")


def slim(d, keys):
    return {k: d.get(k) for k in keys}


def slim_job(j):
    steps = [slim(s, STEP_KEYS) for s in j.get("steps") or []]
    return {**slim(j, JOB_KEYS), "steps": json.dumps(steps, separators=(",", ":")).encode()}


def feed_job(j):
    return {**j, "steps": json.loads(j["steps"])}


def attempt_jobs(repo, run):
    """Every job of the run's exact attempt, paged. (jobs, complete): a page that
    fails after the first keeps what was read and says it is partial."""
    base = f"repos/{repo}/actions/runs/{run['id']}/attempts/{run.get('run_attempt') or 1}/jobs?per_page=100"
    jobs, p = [], 1
    while True:
        if p > 1 and time.time() >= BUDGET["until"]:
            return jobs, False
        try:
            data = get(f"{base}&page={p}", "history_jobs")
        except API_ERRORS:
            if p == 1:
                raise
            return jobs, False
        page = data.get("jobs", [])
        jobs += page
        if not page or len(jobs) >= data.get("total_count", 0):
            return jobs, True
        p += 1


def feed_store(repo, run, jobs, complete):
    with FEED_LOCK:
        FEED_SEQ[0] += 1
        FEED[run["id"]] = {"seq": FEED_SEQ[0], "forge": "github", "repo": repo, "run_id": run["id"],
                           "attempt": run.get("run_attempt") or 1, "complete": complete, "run": run}
        DONE_JOBS[run["id"]] = jobs


# Where a run's status sits in its life: a read that would move it back (a cached
# completed list behind a re-run the active list already shows) is not stored.
STATUS_RANK = {"requested": 0, "waiting": 0, "pending": 0, "queued": 1, "in_progress": 2, "completed": 3}


# An open run record older than this is dropped unread: no run of ours stays open a week,
# and one GitHub never answers for must not be held (and read) forever.
RUN_FEED_OPEN_SECS = 7 * 86400


def run_state(run):
    """What has to change for a run to be served again: the ledger's row changes with it. A
    pull_request run's display_title is its PR's title now, so a retitle moves it and not updated_at."""
    done = run.get("status") == "completed"
    return (run.get("run_attempt") or 1, STATUS_RANK.get(run.get("status"), 0), run.get("conclusion") if done else None,
            run.get("run_started_at"), run.get("updated_at") if done else None, run.get("display_title"))


def run_order(run):
    """Where a read of a run sits in its life; a read behind the stored one is older news."""
    return (run.get("run_attempt") or 1, STATUS_RANK.get(run.get("status"), 0), run.get("updated_at") or "")


def run_feed_store(repo, run):
    with FEED_LOCK:
        old = RUN_FEED.get(run["id"])
        if old and (run_order(old["run"]) > run_order(run) or run_state(old["run"]) == run_state(run)):
            return
        FEED_SEQ[0] += 1
        RUN_FEED[run["id"]] = {"seq": FEED_SEQ[0], "forge": "github", "repo": repo, "stored_at": int(time.time()),
                               "run": slim(run, RUN_KEYS)}


def feed_page(cursor, limit):
    """The changes feed: job records and run records with seq > cursor, oldest change
    first, `limit` of the two together."""
    with FEED_LOCK:
        recs = sorted([(r["seq"], 0, r) for r in FEED.values() if r["seq"] > cursor]
                      + [(r["seq"], 1, r) for r in RUN_FEED.values() if r["seq"] > cursor], key=lambda x: x[0])
        page = recs[:limit]
        records = [{**r, "jobs": [feed_job(j) for j in DONE_JOBS.get(r["run_id"], [])]}
                   for _, half, r in page if half == 0]
        runs = [r for _, half, r in page if half == 1]
    return {"epoch": EPOCH, "repos": RUN_REPOS + FEED_REPOS, "forgejo_repos": FORGEJO_REPOS,
            "window_secs": HISTORY_WINDOW_SECS,
            "next": page[-1][0] if page else cursor, "more": len(recs) > limit, "records": records, "runs": runs}


def lost_runs(repo, active, now):
    """Runs the feed holds open that are off the active lists read this scrape and older than
    the window, so the completed list will never show how they ended: each is read by id,
    from the history budget, the longest unread first, until a read says it finished. A run
    a capped active list left off is read too, and stays open. One GitHub no longer has (404)
    is dropped."""
    edge, ids = now - HISTORY_WINDOW_SECS, {r["id"] for r in active}
    with FEED_LOCK:
        lost = sorted((r for r in RUN_FEED.values() if r["repo"] == repo and r["run"].get("status") != "completed"
                       and r["run"]["id"] not in ids and (ts(r["run"].get("created_at")) or 0) < edge),
                      key=lambda r: r.get("tried", 0))
        lost = lost[:spend(len(lost))]
        for r in lost:
            r["tried"] = now
    out = []
    for rec in lost:
        rid = rec["run"]["id"]
        try:
            out.append(get(f"repos/{repo}/actions/runs/{rid}", "lost_runs"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                with FEED_LOCK:
                    RUN_FEED.pop(rid, None)
        except API_ERRORS:
            pass
    return out


def finished_runs(repo, now):
    """Completed runs created in the window, newest first.

    GitHub caps a filtered run listing at 1000 results; the busiest repo is
    ~600 a day. Pages after the first are read in parallel, and a run that
    shifts across a page boundary while they are read is de-duplicated by id.
    """
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - HISTORY_WINDOW_SECS))
    q = f"repos/{repo}/actions/runs?status=completed&created={urllib.parse.quote('>=' + since)}&per_page=100"
    def page(p):
        # Slimmed as each page lands: a raw page of 100 runs is ~1 MB parsed.
        data = get(f"{q}&page={p}", "history_runs")
        return data.get("total_count", 0), [{**slim(r, RUN_KEYS), **run_labels(r)} for r in data.get("workflow_runs", [])]
    total, first = page(1)
    pages = [first]
    n = -(-total // 100)
    if n > 1:
        # All pages at once: ~3s each, and four at a time put the playground's ten
        # pages at 12s of a cold scrape (2026-11-03).
        with ThreadPoolExecutor(max_workers=10) as ex:
            pages += [runs for _, runs in ex.map(page, range(2, n + 1))]
    runs = {r["id"]: r for pg in pages for r in pg}
    return [runs[i] for i in sorted(runs, reverse=True)]


def stale_detail(run):
    """A finished run is read again when its attempt moved (a re-run finished) or the
    last read was partial."""
    rec = FEED.get(run["id"])
    return not rec or not rec["complete"] or rec["attempt"] != (run.get("run_attempt") or 1)


def fetch_details(acts):
    """Every repo's uncached finished runs, within the scrape's budget (see
    HISTORY_CALL_BUDGET): one from each repo in turn, each repo's newest first,
    so a repo with a deep backlog gets its share from the first scrape. Taken
    first-come by each repo as it finished its own listing, the slowest one (the
    playground: 30 active runs and 10 history pages) reached the budget last on
    every scrape and none of its ~1000 runs a day was ever read (2026-11-12).
    In parallel: sequentially, 60 of them took 51s."""
    queues = [(a["repo"], a.get("todo") or []) for a in acts]
    order = [(repo, q[i]) for i in range(max((len(q) for _, q in queues), default=0))
             for repo, q in queues if i < len(q)]
    order = order[:spend(len(order))]

    def jobs_of(item):
        # One failed call must not discard the ones that answered; one not
        # started by the deadline waits for a later scrape (HISTORY_DEADLINE_SECS).
        if time.time() >= BUDGET["until"]:
            return None
        try:
            return attempt_jobs(*item)
        except API_ERRORS:
            return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        for (repo, run), got in zip(order, ex.map(jobs_of, order)):
            if got is not None:
                feed_store(repo, run, [slim_job(j) for j in got[0]], got[1])


def low_budget():
    # History reads only repos/ endpoints, so that bucket is the one to guard.
    left = rate("repos")["remaining"]
    return left is not None and left < HISTORY_FLOOR


def repo_activity(repo, now):
    """One repo's active runs and their jobs, plus the window's completed runs."""
    out = {"repo": repo, "ok": True, "counts": {}, "jobs": [], "recent": [], "history": []}
    try:
        active = []
        for status in ("in_progress", "queued"):
            data = get(f"repos/{repo}/actions/runs?status={status}&per_page={MAX_RUNS_PER_REPO}", "active_runs")
            out["counts"][status] = data.get("total_count", 0)
            active += data.get("workflow_runs", [])
        # In parallel: one at a time, the playground's 30 active runs took 12s of a
        # cold scrape (2026-11-03). One run's failed read must not discard the
        # repo: GitHub answers a stuck queued run's jobs with a 502 after 10s
        # (playground, 2026-11-11), which blanked its runners and 24h history.
        def run_jobs(run):
            try:
                return get(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100", "active_jobs").get("jobs", [])
            except API_ERRORS:
                return None
        with ThreadPoolExecutor(max_workers=6) as ex:
            fetched = list(ex.map(run_jobs, active))
        for run in active:
            run_feed_store(repo, run)
        for run, jobs in zip(active, fetched):
            if not jobs and run.get("status") == "queued":
                # A queued run with no jobs, or none GitHub will list, never gets
                # a runner. GitHub leaves these behind (one platform schedule run
                # sat like this from 2026-09-13), and they hold the queued count up
                # forever, so list it as a waiting row with its age rather than hide it.
                jobs = [{"name": "(run has no jobs)" if jobs is not None else "(jobs unreadable)",
                         "status": "queued", "created_at": run.get("created_at"),
                         "head_branch": run.get("head_branch"), "labels": [], "html_url": run.get("html_url", ""),
                         "_jobless": True}]
            for job in jobs or ():
                job["_workflow"] = run.get("name") or job.get("workflow_name", "")
                job["_run"] = run_labels(run)
                out["jobs"].append(job)
        cached = HISTORY.get(repo)
        stale = not cached or (now - cached[0] >= HISTORY_REFRESH_SECS and time.time() < BUDGET["until"])
        refreshed = stale and not low_budget()
        if refreshed:
            cached = HISTORY[repo] = (now, finished_runs(repo, now))
        if not cached:
            return out
        # The cached list was cut at fetch time; cut again so a run ages out on
        # time between refreshes.
        out["history"] = [r for r in cached[1] if (ts(r.get("created_at")) or 0) >= now - HISTORY_WINDOW_SECS]
        out["recent"] = out["history"][:RECENT_PER_REPO]
        for run in out["history"]:
            run_feed_store(repo, run)
        # Before the job detail, which can spend the whole budget on a cold or busy scrape;
        # with the history refresh, every HISTORY_REFRESH_SECS, not every scrape.
        for run in lost_runs(repo, active, now) if refreshed else ():
            run_feed_store(repo, run)
        # The runs whose job detail is missing or stale, newest first: read by
        # fetch_details once every repo has answered, so no repo's place in the
        # scrape decides whether it gets any of the budget.
        out["todo"] = [run for run in out["history"] if stale_detail(run)]
    except API_ERRORS:
        out["ok"] = False
    return out


# Forgejo Actions: the runners and their tasks from the Forgejo API.
# The module docstring, FORGEJO ACTIONS, says which fact comes from where and why.
FORGEJO_REPOS = os.environ.get("FORGEJO_REPOS", "").split()
FORGEJO_API = os.environ.get("FORGEJO_API", "http://forgejo-http.forgejo.svc:3000/api/v1").rstrip("/")
FORGEJO_WEB = os.environ.get("FORGEJO_WEB", "https://git.kblab.me").rstrip("/")
FORGEJO_TOKEN = os.environ.get("FORGEJO_TOKEN", "")
# The topology's Forgejo seats; each one is a runner of that name.
FJ_SLOTS = sorted(n for n, s in SLOTS.items() if s["forge"] == "forgejo")
FJ_STATE = {"active": "running", "idle": "idle", "offline": "offline"}
# Forgejo's largest page. Asking for more is not an error: it answers with every
# task the repo ever had (2026-11-03: limit=100 returned 2498).
FORGEJO_PAGE = 50
# repo -> {task id: task}, the window's tasks; repo -> when they were last read in full.
FJ_TASKS = {}
FJ_REFRESHED = {}
# waiting job id -> when a scrape first saw it waiting.
FJ_SEEN = {}
# (when a scrape first missed it, seconds it was seen waiting), for each job that left the
# waiting list in the window: Forgejo's queue time, which its API does not keep (a task is
# created when a runner takes the job, so a task's created_at is its start).
FJ_WAITED = []
FJ_TASK_KEYS = ("id", "name", "head_branch", "event", "display_title", "status", "workflow_id", "url",
                "created_at", "updated_at", "run_started_at")
FJ_DONE = frozenset({"success", "failure", "cancelled", "skipped"})


def fget(path):
    """A Forgejo API GET. Forgejo answers `null` for an empty list."""
    req = urllib.request.Request(f"{FORGEJO_API}/{path}")
    req.add_header("Accept", "application/json")
    if FORGEJO_TOKEN:
        req.add_header("Authorization", f"token {FORGEJO_TOKEN}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# The governor's /tasks: {seat: Forgejo task id} for each seat running a job. A seat names
# its task from the one-job log line; Forgejo's API has no runner-to-task field, so without
# it a running seat's row carries no repo or workflow (hc-571).
GOVERNOR_URL = os.environ.get("GOVERNOR_URL", "").rstrip("/")
# The repo a busy runner's row names when its job is in no repo this exporter reads.
NOT_WATCHED = "(not watched)"


def governor_tasks():
    """{seat: task id} from the governor, or {} when it is unset or does not answer."""
    if not GOVERNOR_URL:
        return {}
    try:
        with urllib.request.urlopen(f"{GOVERNOR_URL}/tasks", timeout=5) as r:
            got = json.loads(r.read())
        return {k: int(v) for k, v in got.items()} if isinstance(got, dict) else {}
    except (*API_ERRORS, TypeError):
        return {}


def forgejo_runners():
    """Each topology Forgejo slot with its runner's state and labels. A name registered
    more than once (a slot re-registered) is its newest runner, the highest id."""
    newest, page = {}, 1
    while True:
        batch = fget(f"admin/actions/runners?limit={FORGEJO_PAGE}&page={page}") or []
        for r in batch:
            if r["name"] in FJ_SLOTS and r["id"] > newest.get(r["name"], {}).get("id", -1):
                newest[r["name"]] = r
        if len(batch) < FORGEJO_PAGE:
            break
        page += 1
    out = []
    for name in FJ_SLOTS:
        r = newest.get(name)
        out.append({"name": name, "host": SLOTS[name]["host"],
                    "state": FJ_STATE.get(r["status"], "offline") if r else "missing",
                    "labels": sorted(r.get("labels") or []) if r else [],
                    # None until the runner first runs: Forgejo learns labels from its Declare call,
                    # which a one-job seat makes only when it starts a job (register sends none).
                    "declared": bool(r) and r.get("labels") is not None})
    return out


def forgejo_repo(repo, now):
    """One repo's waiting and running jobs, and its tasks of the window (cached in FJ_TASKS).

    Every scrape reads the newest page of tasks, and more until every running job's task
    is known; every HISTORY_REFRESH_SECS it reads back to the window's start, which
    is what brings a finished task's final status in.
    """
    out = {"repo": repo, "ok": True, "jobs": []}
    try:
        out["jobs"] = fget(f"repos/{repo}/actions/runners/jobs") or []
        want = {j["task_id"] for j in out["jobs"] if j.get("status") == "running" and j.get("task_id")}
        tasks = FJ_TASKS.setdefault(repo, {})
        full = now - FJ_REFRESHED.get(repo, 0) >= HISTORY_REFRESH_SECS
        page = 1
        while True:
            batch = (fget(f"repos/{repo}/actions/tasks?limit={FORGEJO_PAGE}&page={page}") or {}).get("workflow_runs") or []
            for t in batch:
                tasks[t["id"]] = slim(t, FJ_TASK_KEYS)
            whole = len(batch) < FORGEJO_PAGE or (ts(batch[-1].get("created_at")) or 0) < now - HISTORY_WINDOW_SECS
            if whole and full:
                FJ_REFRESHED[repo] = now
            if whole or (not full and want <= tasks.keys()) or time.time() >= BUDGET["until"]:
                break
            page += 1
        for tid in [k for k, t in tasks.items() if (ts(t.get("created_at")) or 0) < now - HISTORY_WINDOW_SECS]:
            del tasks[tid]
    except API_ERRORS:
        out["ok"] = False
    return out


def forgejo_rows(runners_fut, repo_futs, now, seat_tasks=None):
    """Forgejo's rows for each series, as {metric: [(labels, value)]}, and its api_ok lines.
    seat_tasks ({seat: task id}, from the governor) names the job on a running seat's row."""
    rows, ok = {}, []

    def add(metric, lab, v):
        rows.setdefault(metric, []).append(({**lab, "forge": "forgejo"}, v))

    try:
        fr = runners_fut.result()
        ok.append(("forgejo:admin/actions/runners", "runners", 1))
    except API_ERRORS:
        fr = None
        ok.append(("forgejo:admin/actions/runners", "runners", 0))
    # The labels some live runner takes: a waiting job asking for any other is stuck. A
    # registered runner that has not declared its labels yet may take any, so while one
    # exists nothing is called stuck (the governor skips a stuck wait, and a seat it never
    # lends never declares).
    live = [r for r in fr or [] if r["state"] != "missing"]
    taken = {lab for r in live for lab in r["labels"]}
    judged = fr is not None and all(r["declared"] for r in live)
    facts = [f.result() for f in repo_futs]
    waiting_ids = set()
    for f in facts:
        ok.append((f"forgejo:repos/{f['repo']}", "runs", 1 if f["ok"] else 0))
        short = f["repo"].split("/", 1)[1]
        for j in f["jobs"]:
            if j.get("status") == "waiting":
                waiting_ids.add(j["id"])
                age = int(now - FJ_SEEN.setdefault(j["id"], now))
                runs_on = j.get("runs_on") or []
                stuck = judged and not set(runs_on) <= taken
                # job_id keeps each job its own series: the same job of two queued runs has the
                # same name and labels, and Prometheus drops all but one of a scrape's twins, so
                # the governor saw 8 of 30 jobs at the newest one's age (2026-11-18).
                add("github_actions_job_waiting",
                    {"repo": short, "workflow": "", "job_name": j.get("name", ""), "job_id": str(j["id"]),
                     "branch": "", "status": "waiting",
                     "labels": ",".join(runs_on), "pool": "forgejo", "stuck": "true" if stuck else "false",
                     "url": f"{FORGEJO_WEB}/{f['repo']}/actions", **NO_RUN}, age)
    if all(f["ok"] for f in facts):
        for jid in [k for k in FJ_SEEN if k not in waiting_ids]:
            FJ_WAITED.append((now, int(now - FJ_SEEN.pop(jid))))
    FJ_WAITED[:] = [w for w in FJ_WAITED if w[0] >= now - HISTORY_WINDOW_SECS]

    finished = [(r.split("/", 1)[1], t) for r, tasks in FJ_TASKS.items() if r in FORGEJO_REPOS
                for t in tasks.values() if t.get("status") in FJ_DONE]
    # Every task started since the exporter did is a job a runner took; one no scrape saw
    # waiting was taken between two scrapes, so it waited under one scrape interval: 0.
    started = sum(1 for r, tasks in FJ_TASKS.items() if r in FORGEJO_REPOS for t in tasks.values()
                  if (ts(t.get("created_at")) or 0) >= max(EPOCH, now - HISTORY_WINDOW_SECS))
    waits = sorted([w for _, w in FJ_WAITED] + [0] * max(0, started - len(FJ_WAITED)))
    for q in QUANTILES if waits else ():
        add("github_job_queue_seconds", {"pool": "forgejo", "quantile": q}, int(quantile(waits, float(q))))
    if fr is not None:
        by_state = dict.fromkeys(("running", "idle", "offline", "missing"), 0)
        for r in fr:
            lab = {"name": r["name"], "host": r["host"], "site": site_of(r["host"]),
                   "scope": "topology" if r["state"] == "missing" else "instance", "labels": ",".join(r["labels"]),
                   "state": r["state"], "repo": "", "workflow": "", "job_name": "", "branch": "", "url": "", **NO_RUN}
            tid = (seat_tasks or {}).get(r["name"]) if r["state"] == "running" else None
            if tid is not None:
                # The task, else this scrape's running job of it (its name only). A task found in
                # neither, with every watched repo read, still says the seat is busy on something.
                repo, t = next(((rp, tasks[tid]) for rp, tasks in FJ_TASKS.items() if tid in tasks),
                               next(((f["repo"], j) for f in facts for j in f["jobs"] if j.get("task_id") == tid),
                                    ("", {})))
                unseen = NOT_WATCHED if all(f["ok"] for f in facts) else ""
                lab.update(repo=repo.split("/", 1)[1] if repo else unseen,
                           workflow=t.get("workflow_id") or "", job_name=t.get("name") or "",
                           branch=t.get("head_branch") or "",
                           url=t.get("url") or (f"{FORGEJO_WEB}/{repo}/actions" if repo else ""))
            add("github_runner_state", lab, 0)
            add("github_runner_pool_slot", {**{k: lab[k] for k in SLOT_KEYS if k != "forge"}, "pool": "forgejo"}, 0)
            by_state[r["state"]] += 1
        for state, n in by_state.items():
            add("github_runners_by_state", {"state": state}, n)

    counts, took, pool_wf = {}, {}, {}
    for repo, t in finished:
        wf = t.get("workflow_id") or ""
        k = (repo, wf, t["status"])
        counts[k] = counts.get(k, 0) + 1
        started, done = ts(t.get("run_started_at")), ts(t.get("updated_at"))
        if done:
            pool_wf[(repo, wf)] = max(pool_wf.get((repo, wf), 0), done)
        if started and done and t["status"] != "skipped":
            took.setdefault(wf, []).append(max(0, done - started))
    for (repo, wf, c), n in sorted(counts.items()):
        add("github_jobs_finished", {"pool": "forgejo", "repo": repo, "workflow": wf, "conclusion": c}, n)
    for wf, xs in sorted(took.items()):
        xs.sort()
        for q in QUANTILES:
            add("github_job_duration_seconds", {"pool": "forgejo", "workflow": wf, "quantile": q}, int(quantile(xs, float(q))))
    for (repo, wf), done in sorted(pool_wf.items()):
        add("github_pool_workflow", {"pool": "forgejo", "repo": repo, "workflow": wf}, int(done))
    return rows, ok


def render():
    now = time.time()
    sources = [f"orgs/{o}" for o in ORGS] + [f"repos/{r}" for r in RUN_REPOS]
    BUDGET["left"] = 0 if low_budget() else HISTORY_CALL_BUDGET
    BUDGET["until"] = now + HISTORY_DEADLINE_SECS
    with ThreadPoolExecutor(max_workers=8) as pool:
        runner_futs = {s: pool.submit(runners, s) for s in sources}
        repo_futs = [pool.submit(repo_activity, r, now) for r in RUN_REPOS + FEED_REPOS]
        fj_runners, fj_tasks = (pool.submit(forgejo_runners), pool.submit(governor_tasks)) if FORGEJO_REPOS else (None, None)
        fj_repos = [pool.submit(forgejo_repo, r, now) for r in FORGEJO_REPOS]
        acts = [f.result() for f in repo_futs]
        fetch_details(acts)
    fj, fj_ok = forgejo_rows(fj_runners, fj_repos, now, fj_tasks.result()) if FORGEJO_REPOS else ({}, [])

    def forgejo(metric):
        return [f"{metric}{{{labels_str(lab)}}} {v}" for lab, v in fj.get(metric, [])]

    L = [
        "# HELP github_runner_exporter_api_ok 1 if this source answered this scrape",
        "# TYPE github_runner_exporter_api_ok gauge",
    ]
    seen, runners_ok = {}, True
    for src, fut in runner_futs.items():
        # A runner id is not unique: a repo runner's counts within its repo (deskslim-2's
        # runner 23 in .dotfiles-private and in gungan-speak), and a personal repo's can match
        # an org's. The owner (case-insensitive, as GitHub logins are) and the name make the
        # key; the name is one slot's, so one runner seen through two sources counts once.
        scope = "org" if src.startswith("orgs/") else src.split("/", 1)[1]
        owner = src.split("/")[1].lower()
        try:
            for r in fut.result():
                seen.setdefault((owner, r["id"], r["name"]), (scope, r))
            ok = 1
        except API_ERRORS:
            ok, runners_ok = 0, False
        L.append(f'github_runner_exporter_api_ok{{source="{esc(src)}",kind="runners",forge="github"}} {ok}')
    for a in acts:
        L.append(f'github_runner_exporter_api_ok{{source="repos/{esc(a["repo"])}",kind="runs",forge="github"}} {1 if a["ok"] else 0}')
    acts = [a for a in acts if a["repo"] in RUN_REPOS]  # a FEED_REPOS repo has fed the feed above: no metrics
    for src, kind, ok in fj_ok:
        L.append(f'github_runner_exporter_api_ok{{source="{esc(src)}",kind="{kind}",forge="forgejo"}} {ok}')

    # runner_name -> the job it is running right now.
    running = {}
    waiting = []
    hosted = {a["repo"]: 0 for a in acts if a["ok"]}
    for a in acts:
        for j in a["jobs"]:
            if j.get("status") == "in_progress" and j.get("runner_name"):
                running[j["runner_name"]] = (a["repo"], j)
                if a["repo"] in hosted and github_hosted(j):
                    hosted[a["repo"]] += 1
            elif j.get("status") in ("queued", "waiting", "pending", "requested"):
                waiting.append((a["repo"], j))

    L += [
        "# HELP github_runner_state One row per runner: state is running|idle|offline|missing; value is seconds the current job has run (0 otherwise)",
        "# TYPE github_runner_state gauge",
    ]
    by_state, online, slot_rows, kinds = {}, {}, [], {}
    for scope, r in sorted(seen.values(), key=lambda s: s[1]["name"]):
        names = [l["name"] for l in r.get("labels", [])]
        host, caps = host_of(r["name"], names), caps_of(r["name"], names)
        # labels: what ci-runner-governor matches waiting jobs against, offline runners too.
        # No pool: a runner takes each of its caps (github_runner_pool_slot has a row per cap),
        # and what a busy one runs is its job's kind (ci_runner_job).
        lab = {"name": r["name"], "host": host, "site": site_of(host), "scope": scope,
               "labels": ",".join(sorted(names)),
               "state": "", "repo": "", "workflow": "", "job_name": "", "branch": "", "url": "", **NO_RUN,
               "forge": "github"}
        value = 0
        if r.get("status") != "online":
            lab["state"] = "offline"
        elif r.get("busy"):
            lab["state"] = "running"
            kinds[r["name"]] = job_kind(r["name"], names, running.get(r["name"], (None, {}))[1].get("labels") or [])
            if r["name"] in running:
                repo, j = running[r["name"]]
                lab.update(repo=repo.split("/", 1)[1], workflow=j["_workflow"], job_name=j.get("name", ""),
                           branch=j.get("head_branch") or "", url=j.get("html_url", ""), **j["_run"])
                started = ts(j.get("started_at"))
                value = int(now - started) if started else 0
            else:
                # Busy on a job in a repo outside $RUN_REPOS, or one that started
                # between the two API reads. Say so instead of a blank row.
                lab["repo"] = NOT_WATCHED
        else:
            lab["state"] = "idle"
        # Online counts each GitHub topology slot once, as expected does: a runner
        # registered by hand next to a dead slot (an org one, or a ci_repo_runners one),
        # or a second registration of a live slot's name, would otherwise fill the gap
        # and keep CiHostRunnersOffline quiet while that slot's jobs wait. pool_slot rows
        # and the unserved takers still count a hand-made runner: it does take jobs.
        if lab["state"] != "offline" and SLOTS.get(r["name"], {}).get("forge") == "github":
            for cap in caps:
                online.setdefault((host, cap), set()).add(r["name"])
        by_state[lab["state"]] = by_state.get(lab["state"], 0) + 1
        L.append(f"github_runner_state{{{labels_str(lab)}}} {value}")
        slot_rows += [slot_row(lab, cap, value) for cap in caps]
    if runners_ok:
        # A topology slot with no registered runner of its name: its play never ran, or
        # its runner was removed. Unknown while a source failed, so no row then.
        registered = {r["name"] for _, r in seen.values()}
        for name, slot in sorted(SLOTS.items()):
            if slot["forge"] != "github":
                continue  # forgejo_rows says which Forgejo seats are missing
            if name in registered:
                continue
            lab = {"name": name, "host": slot["host"], "site": site_of(slot["host"]),
                   "scope": "topology", "labels": "",
                   "state": "missing", "repo": "", "workflow": "", "job_name": "", "branch": "", "url": "", **NO_RUN,
                   "forge": "github"}
            by_state["missing"] = by_state.get("missing", 0) + 1
            L.append(f"github_runner_state{{{labels_str(lab)}}} 0")
            slot_rows += [slot_row(lab, cap, 0) for cap in slot["caps"]]
    L += forgejo("github_runner_state")

    L += [
        "# HELP github_runner_pool_slot One row per runner per cap (job kind) it takes, pool=<cap>; value as github_runner_state",
        "# TYPE github_runner_pool_slot gauge",
    ] + slot_rows + forgejo("github_runner_pool_slot")

    L += [
        "# HELP github_runner_withheld A runner apps/ci-runner-governor holds back from new jobs: a Unity slot, or a repo runner with no slot lent to it",
        "# TYPE github_runner_withheld gauge",
    ]
    for scope, r in sorted(seen.values(), key=lambda s: s[1]["name"]):
        names = [l["name"] for l in r.get("labels", [])]
        if WITHHELD_LABELS.keys() & set(names):
            L.append(f"github_runner_withheld{{{labels_str({'name': r['name'], 'host': host_of(r['name'], names)})}}} 1")

    L += [
        "# HELP github_runners_by_state Count of runners in each state (absent while a runner source failed for missing)",
        "# TYPE github_runners_by_state gauge",
    ]
    for state in ("running", "idle", "offline", "missing"):
        if state == "missing" and not runners_ok:
            continue
        L.append(f'github_runners_by_state{{state="{state}",forge="github"}} {by_state.get(state, 0)}')
    L += forgejo("github_runners_by_state")

    # The kind of job each busy runner runs, so ci-runner-cpu's cores and memory (keyed by the same
    # runner name) group by kind: cc:job_kind_cores and cc:job_kind_mem, read by
    # scripts/ci-kind-weights.py. No row for an idle runner or a kind left open.
    kinds.update({lab["name"]: "forgejo" for lab, _ in fj.get("github_runner_state", []) if lab["state"] == "running"})
    L += [
        "# HELP ci_runner_job 1 per busy runner, labelled with the kind of job it runs (unity, light, heavy, forgejo, repo, ...)",
        "# TYPE ci_runner_job gauge",
    ] + [f"ci_runner_job{{{labels_str({'runner': n, 'kind': k})}}} 1" for n, k in sorted(kinds.items()) if k]

    L += [
        "# HELP github_runner_pool_expected Topology slots on this host that take this cap (pool=<cap>)",
        "# TYPE github_runner_pool_expected gauge",
        "# HELP github_runner_pool_online Online runners a topology slot names on this host that take this cap; absent while any runner source failed",
        "# TYPE github_runner_pool_online gauge",
    ]
    expected = Counter((s["host"], cap) for s in SLOTS.values() if s["forge"] == "github" for cap in s["caps"])
    for (host, cap), n in sorted(expected.items()):
        L.append(f'github_runner_pool_expected{{host="{host}",pool="{cap}"}} {n}')
    if runners_ok:
        for host, cap in sorted(set(expected) | set(online)):
            L.append(f'github_runner_pool_online{{host="{host}",pool="{cap}"}} {len(online.get((host, cap), ()))}')

    L += [
        "# HELP github_actions_job_waiting A job waiting for a runner; value is seconds since it was queued",
        "# TYPE github_actions_job_waiting gauge",
    ]
    takers = runner_label_sets(seen.values())
    for repo, j in waiting:
        created = ts(j.get("created_at"))
        age = int(now - created) if created else 0
        lab = {"repo": repo.split("/", 1)[1], "workflow": j["_workflow"], "job_name": j.get("name", ""),
               "branch": j.get("head_branch") or "", "status": j.get("status", ""),
               "labels": ",".join(j.get("labels") or []),
               "pool": wait_kind(repo, j, takers if runners_ok else None),
               "stuck": "true" if j.get("_jobless") and age > JOBLESS_STUCK_SECS else "false",
               "url": j.get("html_url", ""), **j["_run"], "forge": "github"}
        L.append(f"github_actions_job_waiting{{{labels_str(lab)}}} {age}")
    L += forgejo("github_actions_job_waiting")

    L += [
        "# HELP github_actions_runs Workflow runs in this status (queued, in_progress)",
        "# TYPE github_actions_runs gauge",
    ]
    for a in acts:
        for status, n in a["counts"].items():
            L.append(f'github_actions_runs{{repo="{esc(a["repo"])}",status="{status}"}} {n}')

    L += [
        "# HELP github_hosted_jobs_running Jobs running now on GitHub's own runners, not ours (absent for a repo whose runs failed)",
        "# TYPE github_hosted_jobs_running gauge",
    ]
    for repo, n in sorted(hosted.items()):
        L.append(f'github_hosted_jobs_running{{repo="{esc(repo.split("/", 1)[1])}"}} {n}')

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
    runner_labels = {r["name"]: [l["name"] for l in r.get("labels", [])] for _, r in seen.values()}
    # What each runner actually did last, and which workflows each kind of job comes from:
    # the answer to "what is this runner for", read from history instead of a
    # hand-written description that drifts from the workflow files.
    last, pool_wf = {}, {}
    for a in acts:
        for run in a["history"]:
            for j in DONE_JOBS.get(run["id"], []):
                rn, done = j.get("runner_name"), ts(j.get("completed_at"))
                if not rn or not done or rn not in runner_labels:
                    continue  # GitHub-hosted, or a runner since removed
                if done > last.get(rn, (0,))[0]:
                    last[rn] = (done, a["repo"], run.get("name", ""), j)
                k = (done_kind(j, runner_labels), a["repo"].split("/", 1)[1], run.get("name", ""))
                pool_wf[k] = max(pool_wf.get(k, 0), done)
    L += [
        "# HELP github_runner_last_job The last job this runner finished (within the 24h window); value is when",
        "# TYPE github_runner_last_job gauge",
    ]
    for rn, (done, repo, wf, j) in sorted(last.items()):
        lab = labels_str({"name": rn, "last_repo": repo.split("/", 1)[1], "last_workflow": wf,
                          "last_job": j.get("name", ""), "last_conclusion": j.get("conclusion") or "",
                          "last_url": j.get("html_url", ""), "forge": "github"})
        L.append(f"github_runner_last_job{{{lab}}} {int(done)}")
    L += forgejo("github_runner_last_job")
    L += [
        "# HELP github_pool_workflow A workflow whose jobs of this kind (pool=<kind>) ran on our runners in the last 24h; value is when it last did",
        "# TYPE github_pool_workflow gauge",
    ]
    for (pool_, repo, wf), done in sorted(pool_wf.items()):
        L.append(f"github_pool_workflow{{{labels_str({'pool': pool_, 'repo': repo, 'workflow': wf, 'forge': 'github'})}}} {int(done)}")
    L += forgejo("github_pool_workflow")

    # Finished work over the window. Runs count as soon as they are listed; a
    # run's jobs count once they are cached (HISTORY_CALL_BUDGET), so after
    # a restart the job-level numbers fill in over a few scrapes.
    runs_done, jobs_done, queue, took = {}, {}, {}, {}
    # When each run finished: updated_at, as github_actions_run_recent reads it.
    by_hour, failed = {}, []
    for a in acts:
        repo = a["repo"].split("/", 1)[1]
        for run in a["history"]:
            wf = run.get("name") or ""
            k = (repo, wf, run.get("conclusion") or "", run_scope(run))
            runs_done[k] = runs_done.get(k, 0) + 1
            finished = ts(run.get("updated_at"))
            if finished:
                k = (int(finished) // 3600 * 3600, repo, run.get("conclusion") or "", run_scope(run))
                by_hour[k] = by_hour.get(k, 0) + 1
                if run.get("conclusion") in FAILED_CONCLUSIONS:
                    failed.append((repo, run, int(finished)))
            for j in DONE_JOBS.get(run["id"], []):
                rn, pool_ = j.get("runner_name"), done_kind(j, runner_labels)
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
        "# HELP github_runs_finished Completed runs created in the last 24h; scope main = the default branch, not a pull request",
        "# TYPE github_runs_finished gauge",
    ]
    for (repo, wf, c, scope), n in sorted(runs_done.items()):
        L.append(f"github_runs_finished{{{labels_str({'repo': repo, 'workflow': wf, 'conclusion': c, 'scope': scope})}}} {n}")
    L += [
        "# HELP github_runs_by_hour Completed runs of the last 24h by the hour they finished; hour = its start, epoch seconds",
        "# TYPE github_runs_by_hour gauge",
    ]
    for (hour, repo, c, scope), n in sorted(by_hour.items()):
        L.append(f"github_runs_by_hour{{{labels_str({'hour': hour, 'repo': repo, 'conclusion': c, 'scope': scope})}}} {n}")
    L += [
        "# HELP github_run_failed A run of the last 24h that failed or timed out; value is when it finished (epoch seconds)",
        "# TYPE github_run_failed gauge",
    ]
    for repo, run, finished in sorted(failed, key=lambda f: f[1]["id"]):
        lab = labels_str({"repo": repo, "workflow": run.get("name") or "", "branch": run.get("head_branch") or "",
                          "event": run.get("event") or "", "scope": run_scope(run), "conclusion": run.get("conclusion"),
                          "pr": run.get("pr") or "", "title": run.get("title") or "", "url": run.get("html_url") or ""})
        L.append(f"github_run_failed{{{lab}}} {finished}")
    L += [
        "# HELP github_jobs_finished Completed jobs of runs created in the last 24h, by job kind as ci_runner_job names it (pool=<kind>; github-hosted = not our runners)",
        "# TYPE github_jobs_finished gauge",
    ]
    for (pool_, repo, wf, c), n in sorted(jobs_done.items()):
        L.append(f"github_jobs_finished{{{labels_str({'pool': pool_, 'repo': repo, 'workflow': wf, 'conclusion': c, 'forge': 'github'})}}} {n}")
    L += forgejo("github_jobs_finished")
    L += [
        "# HELP github_job_queue_seconds Time a finished job (last 24h) waited for a runner, started_at - created_at, by job kind (pool=<kind>)",
        "# TYPE github_job_queue_seconds gauge",
    ]
    for pool_, xs in sorted(queue.items()):
        xs.sort()
        for q in QUANTILES:
            L.append(f'github_job_queue_seconds{{{labels_str({"pool": pool_, "quantile": q})}}} {int(quantile(xs, float(q)))}')
    L += forgejo("github_job_queue_seconds")
    L += [
        "# HELP github_job_duration_seconds How long a finished job (last 24h) ran, completed_at - started_at, by job kind (pool=<kind>)",
        "# TYPE github_job_duration_seconds gauge",
    ]
    for (pool_, wf), xs in sorted(took.items()):
        xs.sort()
        for q in QUANTILES:
            L.append(f'github_job_duration_seconds{{{labels_str({"pool": pool_, "workflow": wf, "quantile": q, "forge": "github"})}}} {int(quantile(xs, float(q)))}')
    L += forgejo("github_job_duration_seconds")

    L += [
        "# HELP github_runner_exporter_api_calls_total GitHub API calls charged to the limit since start, by kind",
        "# TYPE github_runner_exporter_api_calls_total counter",
    ]
    for kind, n in sorted(CALLS.items()):
        L.append(f'github_runner_exporter_api_calls_total{{kind="{kind}"}} {n}')
    L += [
        "# HELP github_runner_exporter_api_not_modified_total GitHub API calls answered 304 from the ETag cache (free), by kind",
        "# TYPE github_runner_exporter_api_not_modified_total counter",
    ]
    for kind, n in sorted(NOT_MODIFIED.items()):
        L.append(f'github_runner_exporter_api_not_modified_total{{kind="{kind}"}} {n}')
    L += [
        "# HELP github_runner_exporter_rate_remaining Calls GitHub says the token has left this hour, per bucket (orgs/repos)",
        "# TYPE github_runner_exporter_rate_remaining gauge",
    ]
    for scope, r in sorted(RATE.items()):
        if r["remaining"] is not None:
            L.append(f'github_runner_exporter_rate_remaining{{scope="{scope}"}} {r["remaining"]}')

    # Pruned against the cached lists, not this scrape's: a scrape where a repo
    # failed (rate limit, network) must not throw away a day of cached jobs and
    # spend ~500 calls refilling them.
    live = {run["id"] for _, runs in HISTORY.values() for run in runs}
    with FEED_LOCK:
        for rid in [k for k in DONE_JOBS if k not in live]:
            del DONE_JOBS[rid]
            FEED.pop(rid, None)
        # A run record goes a window after its last change once it is finished and out of
        # the cached lists (a lost run's end is out of them from the start); an open one
        # stays until a read says how it ended (lost_runs), or RUN_FEED_OPEN_SECS.
        t = time.time()
        for rid in [k for k, r in RUN_FEED.items() if k not in live and r["stored_at"] < t - (
                HISTORY_WINDOW_SECS if r["run"].get("status") == "completed" else RUN_FEED_OPEN_SECS)]:
            del RUN_FEED[rid]
    return "\n".join(L) + "\n"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        # Routed on the path alone: the API server's Service proxy can hand on
        # a trailing slash or a query, and an exact match on self.path answered
        # those 404 (hub-core reaches this pod only through that proxy).
        url = urllib.parse.urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        if path == "/metrics":
            with RENDER_LOCK:
                body = render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/ci/details":
            q = urllib.parse.parse_qs(url.query)
            try:
                cursor = int(q.get("cursor", ["0"])[0])
                limit = max(1, min(FEED_LIMIT, int(q.get("limit", [str(FEED_LIMIT)])[0])))
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            body = json.dumps(feed_page(cursor, limit), separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/health":
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


def server(addr):
    # Threaded so /health answers while a scrape renders: on one thread a 25s scrape
    # timed the probes out and readiness pulled the pod from the Service, so the
    # next scrape failed too (2026-11-03).
    return ThreadingHTTPServer(addr, H)


if __name__ == "__main__":
    print(
        f"github-runner-exporter: orgs={ORGS} repos={len(RUN_REPOS)} feed_repos={len(FEED_REPOS)} slots={len(SLOTS)} "
        f"token={'set' if TOKEN else 'MISSING'} owner_tokens={sorted(OWNER_TOKENS)} port={PORT}",
        flush=True,
    )
    server(("", PORT)).serve_forever()
