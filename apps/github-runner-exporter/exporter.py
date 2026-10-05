#!/usr/bin/env python3
"""github-runner-exporter - the self-hosted GitHub Actions runner pools, roster-first.

WHY THIS EXISTS:

CI for BlackNBrownStudios runs on home hardware: the platform pmp-light /
pmp-heavy slots (repo-scoped, BlackNBrownStudios/platform), the mac-mini macOS
runner (same repo), and the Unity pool (org-scoped, label `unity`, every game
repo). A self-hosted job WAITS for a runner instead of failing, so a dead slot
does not show up as a red check - it shows up as a PR that sits queued. The only
place that state existed was `gh api .../actions/runners`, one repo at a time.

ROSTER-FIRST, for the same reason as apps/fleet-exporter: GitHub only lists
runners that registered. A slot the ansible play never brought up is not
offline, it is ABSENT, and a dashboard built on the API alone counts it as
nothing. $RUNNER_ROSTER says what SHOULD exist (it mirrors the slot counts in
ansible/inventory.yml: platform_ci_runners, unity_ci_runners,
github_runner_mac), so online-vs-expected is a real number.

Fetched on every scrape, no background loop: Prometheus's scrape interval is the
cadence. GitHub has no push API for runner state. Budget: 2 runner calls + 2
per repo in $RUN_REPOS per scrape, ~720/h at a 60s interval against the PAT's
5000/h.

Alerting is NOT here. Sentinel (~/.agent/watches/*runners-online.yaml) is the
notification voice for runner pools; this is the screen.
"""
import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
PORT = int(os.environ.get("PORT", "9320"))
# API paths whose /actions/runners list we read: a repo scope and an org scope.
RUNNER_SOURCES = os.environ.get(
    "RUNNER_SOURCES", "repos/BlackNBrownStudios/platform orgs/BlackNBrownStudios"
).split()
# Repos whose queued / in-progress workflow runs are counted.
RUN_REPOS = os.environ.get("RUN_REPOS", "BlackNBrownStudios/platform").split()
# host:pool:slots, whitespace separated.
ROSTER = []
for entry in os.environ.get("RUNNER_ROSTER", "").split():
    host, pool, slots = entry.split(":")
    ROSTER.append((host, pool, int(slots)))
ROSTER_HOSTS = sorted({h for h, _, _ in ROSTER}, key=len, reverse=True)
POOL_LABELS = ("pmp-light", "pmp-heavy", "unity")


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


def pool_of(labels):
    for p in POOL_LABELS:
        if p in labels:
            return p
    return "macos" if "macOS" in labels else "other"


def host_of(name, labels):
    # The unity role stamps host-<inventory_hostname>; the platform role does not,
    # but names it <inventory_hostname>-<pool>-<n>, so match the roster's hosts
    # as name prefixes (longest first, so no host can shadow a longer one).
    for l in labels:
        if l.startswith("host-"):
            return l[5:]
    for h in ROSTER_HOSTS:
        if name == h or name.startswith(h + "-"):
            return h
    return "unknown"


def esc(v):
    return re.sub(r'(["\\])', r"\\\1", str(v)).replace("\n", " ")


def render():
    L = [
        "# HELP github_runner_exporter_api_ok 1 if this runner source answered this scrape",
        "# TYPE github_runner_exporter_api_ok gauge",
    ]
    seen, all_ok = {}, True
    for src in RUNNER_SOURCES:
        try:
            for r in runners(src):
                seen[r["id"]] = (src.split("/")[0].rstrip("s"), r)
            ok = 1
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            ok, all_ok = 0, False
        L.append(f'github_runner_exporter_api_ok{{source="{esc(src)}"}} {ok}')

    L += [
        "# HELP github_runner_online 1 if GitHub reports this runner online",
        "# TYPE github_runner_online gauge",
        "# HELP github_runner_busy 1 if this runner is running a job",
        "# TYPE github_runner_busy gauge",
    ]
    online = {}
    for scope, r in sorted(seen.values(), key=lambda s: s[1]["name"]):
        labels = [l["name"] for l in r.get("labels", [])]
        host, pool = host_of(r["name"], labels), pool_of(labels)
        is_on = 1 if r.get("status") == "online" else 0
        lab = f'name="{esc(r["name"])}",host="{esc(host)}",pool="{pool}",scope="{scope}"'
        L.append(f"github_runner_online{{{lab}}} {is_on}")
        L.append(f"github_runner_busy{{{lab}}} {1 if r.get('busy') else 0}")
        online[(host, pool)] = online.get((host, pool), 0) + is_on

    L += [
        "# HELP github_runner_pool_expected Slots the roster says this host runs in this pool",
        "# TYPE github_runner_pool_expected gauge",
        "# HELP github_runner_pool_online Online runners in this host/pool; absent while any source failed",
        "# TYPE github_runner_pool_online gauge",
    ]
    for host, pool, slots in ROSTER:
        L.append(f'github_runner_pool_expected{{host="{host}",pool="{pool}"}} {slots}')
    # Only when every source answered: a failed source must read as "unknown",
    # never as "0 online", or one API blip paints the whole pool down.
    if all_ok:
        keys = {(h, p) for h, p, _ in ROSTER} | set(online)
        for host, pool in sorted(keys):
            L.append(f'github_runner_pool_online{{host="{host}",pool="{pool}"}} {online.get((host, pool), 0)}')

    L += [
        "# HELP github_actions_runs Workflow runs in this status (queued, in_progress)",
        "# TYPE github_actions_runs gauge",
    ]
    for repo in RUN_REPOS:
        for status in ("queued", "in_progress"):
            try:
                n = get(f"repos/{repo}/actions/runs?status={status}&per_page=1").get("total_count", 0)
            except (urllib.error.URLError, OSError, ValueError):
                continue
            L.append(f'github_actions_runs{{repo="{esc(repo)}",status="{status}"}} {n}')
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
        f"github-runner-exporter: sources={RUNNER_SOURCES} repos={len(RUN_REPOS)} "
        f"roster={len(ROSTER)} token={'set' if TOKEN else 'MISSING'} port={PORT}",
        flush=True,
    )
    HTTPServer(("", PORT), H).serve_forever()
