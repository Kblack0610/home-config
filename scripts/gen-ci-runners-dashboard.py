#!/usr/bin/env python3
"""Generate apps/monitoring/dashboards/ci-runners.json (Grafana "CI / Runners").

Edit this, not the JSON: python3 scripts/gen-ci-runners-dashboard.py
Series come from apps/github-runner-exporter."""

import json, sys
# Strip the scrape-target labels: one row per runner, and no doubles while two
# exporter pods are scraped during a rollout.
W = "max without (instance, pod, job, namespace, service, endpoint, container) "
DS = {"type": "prometheus", "uid": "${datasource}"}
pid = [0]


def tgt(expr, ref="A", instant=True, legend="__auto", fmt="table"):
    return {"datasource": DS, "editorMode": "code", "expr": expr, "format": fmt, "instant": instant,
            "range": not instant, "legendFormat": legend, "refId": ref}


def thr(*steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def P(d):
    pid[0] += 1
    d["id"] = pid[0]
    d["datasource"] = DS
    return d


def link(title):
    return {"id": "links", "value": [{"title": title, "url": "${__data.fields.url}", "targetBlank": True}]}


def hide(*names):
    return [{"matcher": {"id": "byName", "options": n}, "properties": [{"id": "custom.hidden", "value": True}]} for n in names]


def stat(title, desc, expr, x, w, steps, no_value="0", mappings=None):
    return P({"type": "stat", "title": title, "description": desc, "gridPos": {"h": 4, "w": w, "x": x, "y": 0},
              "targets": [tgt(expr, fmt="time_series")],
              "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                          "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"},
              "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": mappings or [], "unit": "none",
                                           "noValue": no_value, "thresholds": thr(*steps)}, "overrides": []}})


STATE_MAP = [{"type": "value", "options": {
    "running": {"text": "RUNNING", "color": "blue", "index": 0},
    "idle": {"text": "idle", "color": "#3a3f47", "index": 1},
    "offline": {"text": "OFFLINE", "color": "red", "index": 2},
    "missing": {"text": "MISSING", "color": "orange", "index": 3}}}]
CONCLUSION_MAP = [{"type": "value", "options": {
    "success": {"text": "success", "color": "green", "index": 0},
    "failure": {"text": "FAILED", "color": "red", "index": 1},
    "cancelled": {"text": "cancelled", "color": "#6e7079", "index": 2},
    "skipped": {"text": "skipped", "color": "#6e7079", "index": 3},
    "timed_out": {"text": "timed out", "color": "orange", "index": 4}}}]

# `pr` is blank for a run with no same-repo PR (schedule, push, fork), so the link is too.
PR_LINK = {"matcher": {"id": "byName", "options": "pr"}, "properties": [
    {"id": "links", "value": [{"title": "Open PR on GitHub", "targetBlank": True,
                               "url": "https://github.com/BlackNBrownStudios/${__data.fields.repo}/pull/${__data.fields.pr}"}]},
    {"id": "custom.width", "value": 70}]}
# Machines that run CI: every host with roster slots, plus the workstation, whose
# local Unity gate slots (cc:host_local_slots) are CI without being runners. cc:host_*
# come from apps/monitoring/prometheus-rules-command-center.yaml (group command-center.hosts).
CI_HOSTS = " and on (host) (max by (host) (github_runner_pool_expected) or cc:host_local_slots)"
MACHINE_COLS = [("cc:host_cpu_busy_ratio", "cpu", "percentunit"), ("cc:host_cpu_busy_ratio:avg6h", "cpu 6h", "percentunit"),
                ("cc:host_load1", "load", "none"),
                ("cc:host_cores", "cores", "none"), ("cc:host_mem_used_ratio", "mem", "percentunit"),
                ("cc:host_temp_c", "temp", "celsius")]


def machines(y):
    refs = "ABCDEFG"
    targets = [tgt(f"{m}{CI_HOSTS}", refs[i]) for i, (m, _, _) in enumerate(MACHINE_COLS)]
    # Runners busy, or for the workstation its local gate slots held.
    targets.append(tgt('count by (host) (' + W + '(github_runner_state{state="running"})) or cc:host_local_busy', refs[len(MACHINE_COLS)]))
    rename = {f"Value #{refs[i]}": col for i, (_, col, _) in enumerate(MACHINE_COLS)}
    rename[f"Value #{refs[len(MACHINE_COLS)]}"] = "busy runners"
    units = [{"matcher": {"id": "byName", "options": col}, "properties": [{"id": "unit", "value": u}, {"id": "decimals", "value": 0 if u != "none" else 1}]}
             for _, col, u in MACHINE_COLS]
    heat = [{"matcher": {"id": "byName", "options": col}, "properties": [
        {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "color", "value": {"mode": "thresholds"}},
        {"id": "thresholds", "value": thr(*steps)}]} for col, steps in
        (("cpu", (("text", None), ("orange", 0.7), ("red", 0.9))), ("cpu 6h", (("text", None), ("orange", 0.7), ("red", 0.9))), ("mem", (("text", None), ("orange", 0.8), ("red", 0.92))),
         ("temp", (("text", None), ("orange", 85), ("red", 95))))]
    return P({"type": "table", "title": "Machines: load on every host that runs CI",
              "description": "One row per CI host: CPU busy now and averaged over 6h (the headroom a rebalance is judged on), 1-minute load against its cores, memory used, CPU package temperature, and how many of its runners are busy (for the workstation, how many of its local Unity gate slots are held). '-' = that host exports no such series (macOS has no temperature sensor in node_exporter).",
              "gridPos": {"h": 8, "w": 24, "x": 0, "y": y}, "targets": targets,
              "transformations": [{"id": "joinByField", "options": {"byField": "host", "mode": "outer"}},
                                  {"id": "organize", "options": {"excludeByName": {"Time": True, **{f"Time {i}": True for i in range(1, 9)}},
                                                                 "renameByName": rename}}],
              "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "cpu", "desc": True}]},
              "fieldConfig": {"defaults": {"noValue": "-", "custom": {"align": "left"}}, "overrides": units + heat}})


def pass_pct(col):
    """A pass ratio column: percent, red under 70%, orange under 90%."""
    return {"matcher": {"id": "byName", "options": col}, "properties": [
        {"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 0},
        {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "color", "value": {"mode": "thresholds"}},
        {"id": "thresholds", "value": thr(("red", None), ("orange", 0.7), ("green", 0.9))}]}


def pools(y):
    """One row per pool: busy/slots, waiting, oldest, and its last 24h. Read side by side they show balance."""
    cols = [("cc:pool_busy", "busy"), ("cc:pool_slots", "slots"), ("cc:pool_waiting", "waiting"),
            ("cc:pool_oldest_wait_seconds", "oldest wait"), ("cc:pool_done_24h", "done 24h"),
            ("cc:pool_pass_ratio_24h", "pass 24h"), ("cc:pool_cancelled_24h", "cancelled 24h")]
    refs = "ABCDEFG"
    return P({"type": "table", "title": "Pools: busy, slots and queue",
              "description": "One row per runner pool (cc:pool_* in prometheus-rules-command-center.yaml). A pool with every slot busy and jobs waiting, next to pools with idle slots, is where capacity should move. Queued runs GitHub never gave jobs are left out (they show in 'Waiting for a runner'). The 24h columns count jobs of runs created in the last day: pass = success / (success + failure + timed out), so cancelled jobs (the playground cancels gates for closed PRs on purpose) are counted apart. github-hosted = jobs that ran on GitHub's runners.",
              "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
              "targets": [tgt(m, refs[i]) for i, (m, _) in enumerate(cols)],
              "transformations": [{"id": "joinByField", "options": {"byField": "pool", "mode": "outer"}},
                                  {"id": "organize", "options": {"excludeByName": {"Time": True, **{f"Time {i}": True for i in range(1, len(cols) + 1)}},
                                                                 "renameByName": {f"Value #{refs[i]}": c for i, (_, c) in enumerate(cols)}}}],
              "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "waiting", "desc": True}]},
              "fieldConfig": {"defaults": {"noValue": "-", "custom": {"align": "left"}}, "overrides": [
                  {"matcher": {"id": "byName", "options": "oldest wait"}, "properties": [
                      {"id": "unit", "value": "dtdurations"}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                      {"id": "color", "value": {"mode": "thresholds"}},
                      {"id": "thresholds", "value": thr(("text", None), ("orange", 900), ("red", 3600))}]},
                  {"matcher": {"id": "byName", "options": "waiting"}, "properties": [
                      {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "color", "value": {"mode": "thresholds"}},
                      {"id": "thresholds", "value": thr(("text", None), ("orange", 1), ("red", 10))}]},
                  pass_pct("pass 24h")]}})


def finished(y):
    """One row per repo/workflow over the last 24h: what a person means by "Tests is red"."""
    def by(sel):
        return f"sum by (repo, workflow) ({W}(github_runs_finished{sel}))"
    zero = " or " + by("") + " * 0"
    cols = [(by(""), "runs"),
            ("(" + by('{conclusion="success"}') + zero + ") / (" + by('{conclusion=~"success|failure|timed_out"}') + " > 0)", "pass"),
            (by('{conclusion=~"failure|timed_out"}') + zero, "failed"),
            (by('{conclusion="cancelled"}') + zero, "cancelled")]
    refs = "ABCD"
    return P({"type": "table", "title": "Finished, last 24h by workflow",
              "description": "Every workflow run created in the last 24h, one row per repo and workflow, most failures first. pass = success / (success + failure + timed out); failed counts timed out too; cancelled runs (closed-PR gates, superseded pushes) are counted apart. '-' in pass = nothing in the day passed or failed.",
              "gridPos": {"h": 10, "w": 24, "x": 0, "y": y},
              "targets": [tgt(e, refs[i]) for i, (e, _) in enumerate(cols)],
              "transformations": [{"id": "merge", "options": {}},
                                  {"id": "organize", "options": {
                                      "excludeByName": {"Time": True},
                                      "indexByName": {"repo": 0, "workflow": 1, **{f"Value #{refs[i]}": 2 + i for i in range(len(cols))}},
                                      "renameByName": {f"Value #{refs[i]}": c for i, (_, c) in enumerate(cols)}}}],
              "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "failed", "desc": True}]},
              "fieldConfig": {"defaults": {"noValue": "-", "custom": {"align": "left"}}, "overrides": [
                  pass_pct("pass"),
                  {"matcher": {"id": "byName", "options": "failed"}, "properties": [
                      {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "color", "value": {"mode": "thresholds"}},
                      {"id": "thresholds", "value": thr(("text", None), ("red", 1))}]}]}})


panels = [
    stat("Running", "Runners executing a job right now.", 'sum(' + W + '(github_runners_by_state{state="running"}))', 0, 4,
         [("#3a3f47", None), ("blue", 1)]),
    stat("Idle", "Online runners with nothing to do.", 'sum(' + W + '(github_runners_by_state{state="idle"}))', 4, 4,
         [("#3a3f47", None)]),
    stat("Waiting", "Jobs queued for a runner across the watched repos. A self-hosted job waits instead of failing, so this is where a dead or full pool shows up.",
         "count(" + W + "(github_actions_job_waiting)) or vector(0)", 8, 4, [("green", None), ("yellow", 1), ("red", 5)]),
    stat("Offline / missing", "Registered runners that are offline, plus roster slots that never registered (ansible/inventory.yml says they should exist).",
         'sum(' + W + '(github_runners_by_state{state=~"offline|missing"}))', 12, 4, [("green", None), ("red", 1)]),
    stat("GitHub API", "Whether the exporter can read GitHub. 'no data' means the token is missing or rejected: the tables below are then empty because nothing could be read, not because nothing is running.",
         "min(github_runner_exporter_api_ok)", 16, 8, [("red", None), ("green", 1)], no_value="no data",
         mappings=[{"type": "value", "options": {"0": {"text": "FAILING (token?)", "color": "red"}, "1": {"text": "OK", "color": "green"}}}]),

    machines(4),
    pools(12),

    P({"type": "table", "title": "Runners: what is running where",
       "description": "One row per runner. RUNNING rows show the job and how long it has been going; the 'last' columns show the last job it finished, which is what that runner is actually used for. Click a job to open it on GitHub. MISSING = a slot the roster expects that never registered.",
       "gridPos": {"h": 13, "w": 24, "x": 0, "y": 20},
       "targets": [tgt(W + "(github_runner_state)", "A"), tgt(W + "(github_runner_last_job) * 1000", "B")],
       "transformations": [{"id": "joinByField", "options": {"byField": "name", "mode": "outer"}},
                           {"id": "organize", "options": {
           "excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "__name__": True, "scope": True, "event": True},
           "indexByName": {"state": 0, "name": 1, "host": 2, "pool": 3, "repo": 4, "pr": 5, "branch": 6, "title": 7, "workflow": 8,
                           "job_name": 9, "Value #A": 10, "last_conclusion": 11, "last_repo": 12, "last_workflow": 13, "last_job": 14,
                           "Value #B": 15, "url": 16, "last_url": 17},
           "renameByName": {"name": "runner", "job_name": "job", "Value #A": "running for", "last_conclusion": "last result",
                            "last_repo": "last repo", "last_workflow": "last workflow", "last_job": "last job", "Value #B": "last finished"}}}],
       "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "running for", "desc": True}]},
       "fieldConfig": {"defaults": {"noValue": "-", "custom": {"align": "left"}},
                       "overrides": [
                           {"matcher": {"id": "byName", "options": "state"}, "properties": [
                               {"id": "mappings", "value": STATE_MAP},
                               {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                               {"id": "custom.width", "value": 110}]},
                           {"matcher": {"id": "byName", "options": "running for"}, "properties": [
                               {"id": "unit", "value": "dtdurations"},
                               {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": ""}}}]}]},
                           {"matcher": {"id": "byName", "options": "job"}, "properties": [link("Open job on GitHub")]},
                           PR_LINK,
                           {"matcher": {"id": "byName", "options": "last job"}, "properties": [
                               {"id": "links", "value": [{"title": "Open job on GitHub", "url": "${__data.fields.last_url}", "targetBlank": True}]}]},
                           {"matcher": {"id": "byName", "options": "last result"}, "properties": [
                               {"id": "mappings", "value": CONCLUSION_MAP},
                               {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "custom.width", "value": 100}]},
                           {"matcher": {"id": "byName", "options": "last finished"}, "properties": [{"id": "unit", "value": "dateTimeFromNow"}]},
                       ] + hide("url", "last_url")}}),

    P({"type": "table", "title": "What each pool runs",
       "description": "Workflows each runner pool finished in the last 24h, newest first. Read from job history, so it stays true when the workflow files change.",
       "gridPos": {"h": 10, "w": 24, "x": 0, "y": 33},
       "targets": [tgt(W + "(github_pool_workflow) * 1000")],
       "transformations": [{"id": "organize", "options": {
           "excludeByName": {"Time": True},
           "indexByName": {"pool": 0, "repo": 1, "workflow": 2, "Value": 3},
           "renameByName": {"Value": "last ran"}}}],
       "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "pool", "desc": False}]},
       "fieldConfig": {"defaults": {"noValue": "-", "custom": {"align": "left"}},
                       "overrides": [{"matcher": {"id": "byName", "options": "last ran"}, "properties": [{"id": "unit", "value": "dateTimeFromNow"}]}]}}),

    P({"type": "table", "title": "Waiting for a runner",
       "description": "Queued jobs, oldest first. 'needs' is the runs-on label set, so you can see which pool is short. '(run has no jobs)' is a run GitHub left queued with no jobs: it will never start, cancel it.",
       "gridPos": {"h": 6, "w": 24, "x": 0, "y": 43},
       "targets": [tgt(W + "(github_actions_job_waiting)")],
       "transformations": [{"id": "organize", "options": {
           "excludeByName": {"Time": True, "__name__": True, "status": True, "event": True, "title": True},
           "indexByName": {"repo": 0, "pr": 1, "branch": 2, "workflow": 3, "job_name": 4, "labels": 5, "Value": 6, "url": 7},
           "renameByName": {"labels": "needs", "job_name": "job", "Value": "waiting for"}}}],
       "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "waiting for", "desc": True}]},
       "fieldConfig": {"defaults": {"noValue": "-", "custom": {"align": "left"}},
                       "overrides": [
                           {"matcher": {"id": "byName", "options": "waiting for"}, "properties": [
                               {"id": "unit", "value": "dtdurations"},
                               {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "color", "value": {"mode": "thresholds"}},
                               {"id": "thresholds", "value": thr(("text", None), ("orange", 900), ("red", 3600))}]},
                           {"matcher": {"id": "byName", "options": "job"}, "properties": [link("Open job on GitHub")]},
                           PR_LINK,
                       ] + hide("url")}}),

    P({"type": "table", "title": "Recent runs",
       "description": "The last completed runs per watched repo, newest first. Click the workflow to open the run.",
       "gridPos": {"h": 10, "w": 24, "x": 0, "y": 49},
       "targets": [tgt(W + "(github_actions_run_recent) * 1000", "A"), tgt(W + "(github_actions_run_recent_duration_seconds)", "B")],
       "transformations": [{"id": "merge", "options": {}},
                           {"id": "organize", "options": {
                               "excludeByName": {"Time": True},
                               "indexByName": {"conclusion": 0, "repo": 1, "workflow": 2, "branch": 3, "event": 4, "Value #A": 5, "Value #B": 6, "url": 7},
                               "renameByName": {"Value #A": "finished", "Value #B": "took"}}}],
       "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "finished", "desc": True}]},
       "fieldConfig": {"defaults": {"noValue": "-", "custom": {"align": "left"}},
                       "overrides": [
                           {"matcher": {"id": "byName", "options": "conclusion"}, "properties": [
                               {"id": "mappings", "value": CONCLUSION_MAP},
                               {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                               {"id": "custom.width", "value": 110}]},
                           {"matcher": {"id": "byName", "options": "finished"}, "properties": [{"id": "unit", "value": "dateTimeFromNow"}]},
                           {"matcher": {"id": "byName", "options": "took"}, "properties": [{"id": "unit", "value": "dtdurations"}]},
                           {"matcher": {"id": "byName", "options": "workflow"}, "properties": [link("Open run on GitHub")]},
                       ] + hide("url")}}),

    finished(59),

    P({"type": "row", "title": "Trends", "collapsed": True, "gridPos": {"h": 1, "w": 24, "x": 0, "y": 69}, "panels": [
        P({"type": "timeseries", "title": "Busy runners by pool", "gridPos": {"h": 8, "w": 12, "x": 0, "y": 70},
           "targets": [tgt('count by (pool) (' + W + '(github_runner_state{state="running"}))', instant=False, legend="{{pool}}", fmt="time_series")],
           "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "noValue": "0",
                                        "custom": {"drawStyle": "line", "lineInterpolation": "stepAfter", "fillOpacity": 10}}, "overrides": []},
           "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}}),
        P({"type": "timeseries", "title": "Waiting jobs by repo", "gridPos": {"h": 8, "w": 12, "x": 12, "y": 70},
           "targets": [tgt("count by (repo) (" + W + "(github_actions_job_waiting))", instant=False, legend="{{repo}}", fmt="time_series")],
           "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "noValue": "0",
                                        "custom": {"drawStyle": "line", "lineInterpolation": "stepAfter", "fillOpacity": 0}}, "overrides": []},
           "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}}),
    ]}),
]

dash = {"uid": "kb-ci-runners", "title": "CI / Runners",
        "description": "What every self-hosted GitHub Actions runner for BlackNBrownStudios is doing, what is waiting, and what just finished. Fed by apps/github-runner-exporter. Alerts are Sentinel's, not this dashboard's.",
        "tags": ["ci", "runners", "github", "homelab"], "timezone": "browser", "schemaVersion": 39, "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {"list": [{"current": {}, "hide": 0, "includeAll": False, "multi": False, "name": "datasource", "options": [],
                                 "query": "prometheus", "queryValue": "", "refresh": 1, "regex": "", "skipUrlSync": False, "type": "datasource", "label": "Data source"}]},
        "panels": panels}
import os
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps/monitoring/dashboards/ci-runners.json")
with open(out, "w") as f:
    json.dump(dash, f, indent=2)
    f.write("\n")
