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

    P({"type": "table", "title": "Runners: what is running where",
       "description": "One row per runner. RUNNING rows show the job and how long it has been going; click the job to open it on GitHub. MISSING = a slot the roster expects that never registered.",
       "gridPos": {"h": 12, "w": 24, "x": 0, "y": 4},
       "targets": [tgt(W + "(github_runner_state)")],
       "transformations": [{"id": "organize", "options": {
           "excludeByName": {"Time": True, "__name__": True, "scope": True},
           "indexByName": {"state": 0, "name": 1, "pool": 2, "host": 3, "repo": 4, "workflow": 5, "job_name": 6, "branch": 7, "Value": 8, "url": 9},
           "renameByName": {"name": "runner", "job_name": "job", "Value": "running for"}}}],
       "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "running for", "desc": True}]},
       "fieldConfig": {"defaults": {"noValue": "no runner data (see GitHub API)", "custom": {"align": "left"}},
                       "overrides": [
                           {"matcher": {"id": "byName", "options": "state"}, "properties": [
                               {"id": "mappings", "value": STATE_MAP},
                               {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                               {"id": "custom.width", "value": 110}]},
                           {"matcher": {"id": "byName", "options": "running for"}, "properties": [
                               {"id": "unit", "value": "dtdurations"},
                               {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": ""}}}]}]},
                           {"matcher": {"id": "byName", "options": "job"}, "properties": [link("Open job on GitHub")]},
                       ] + hide("url")}}),

    P({"type": "table", "title": "Waiting for a runner",
       "description": "Queued jobs, oldest first. 'needs' is the runs-on label set, so you can see which pool is short. '(run has no jobs)' is a run GitHub left queued with no jobs: it will never start, cancel it.",
       "gridPos": {"h": 8, "w": 24, "x": 0, "y": 16},
       "targets": [tgt(W + "(github_actions_job_waiting)")],
       "transformations": [{"id": "organize", "options": {
           "excludeByName": {"Time": True, "__name__": True, "status": True},
           "indexByName": {"repo": 0, "workflow": 1, "job_name": 2, "branch": 3, "labels": 4, "Value": 5, "url": 6},
           "renameByName": {"labels": "needs", "job_name": "job", "Value": "waiting for"}}}],
       "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "waiting for", "desc": True}]},
       "fieldConfig": {"defaults": {"noValue": "Nothing waiting", "custom": {"align": "left"}},
                       "overrides": [
                           {"matcher": {"id": "byName", "options": "waiting for"}, "properties": [
                               {"id": "unit", "value": "dtdurations"},
                               {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "color", "value": {"mode": "thresholds"}},
                               {"id": "thresholds", "value": thr(("text", None), ("orange", 900), ("red", 3600))}]},
                           {"matcher": {"id": "byName", "options": "job"}, "properties": [link("Open job on GitHub")]},
                       ] + hide("url")}}),

    P({"type": "table", "title": "Recent runs",
       "description": "The last completed runs per watched repo, newest first. Click the workflow to open the run.",
       "gridPos": {"h": 10, "w": 24, "x": 0, "y": 24},
       "targets": [tgt(W + "(github_actions_run_recent) * 1000", "A"), tgt(W + "(github_actions_run_recent_duration_seconds)", "B")],
       "transformations": [{"id": "merge", "options": {}},
                           {"id": "organize", "options": {
                               "excludeByName": {"Time": True},
                               "indexByName": {"conclusion": 0, "repo": 1, "workflow": 2, "branch": 3, "event": 4, "Value #A": 5, "Value #B": 6, "url": 7},
                               "renameByName": {"Value #A": "finished", "Value #B": "took"}}}],
       "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "finished", "desc": True}]},
       "fieldConfig": {"defaults": {"noValue": "no recent runs", "custom": {"align": "left"}},
                       "overrides": [
                           {"matcher": {"id": "byName", "options": "conclusion"}, "properties": [
                               {"id": "mappings", "value": CONCLUSION_MAP},
                               {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                               {"id": "custom.width", "value": 110}]},
                           {"matcher": {"id": "byName", "options": "finished"}, "properties": [{"id": "unit", "value": "dateTimeFromNow"}]},
                           {"matcher": {"id": "byName", "options": "took"}, "properties": [{"id": "unit", "value": "dtdurations"}]},
                           {"matcher": {"id": "byName", "options": "workflow"}, "properties": [link("Open run on GitHub")]},
                       ] + hide("url")}}),

    P({"type": "row", "title": "Trends", "collapsed": True, "gridPos": {"h": 1, "w": 24, "x": 0, "y": 34}, "panels": [
        P({"type": "timeseries", "title": "Busy runners by pool", "gridPos": {"h": 8, "w": 12, "x": 0, "y": 35},
           "targets": [tgt('count by (pool) (' + W + '(github_runner_state{state="running"}))', instant=False, legend="{{pool}}", fmt="time_series")],
           "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "noValue": "0",
                                        "custom": {"drawStyle": "line", "lineInterpolation": "stepAfter", "fillOpacity": 10}}, "overrides": []},
           "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}}),
        P({"type": "timeseries", "title": "Waiting jobs by repo", "gridPos": {"h": 8, "w": 12, "x": 12, "y": 35},
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
