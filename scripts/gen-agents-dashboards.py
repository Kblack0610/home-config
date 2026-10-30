#!/usr/bin/env python3
"""Generate the five Grafana "Agents" dashboards into apps/monitoring/dashboards/agents-*.json.

Edit this, not the JSON: python3 scripts/gen-agents-dashboards.py

Sources, and who publishes them:
  Prometheus  claude_code_*      Claude Code OTel metrics via Alloy (apps/alloy/config.alloy)
              agent_session_*,   node_exporter textfiles on the workstation, written by
              agent_bench_*,     fleet-sessions, fleet-bench and day-log (~/.dotfiles)
              agent_day_*
              mqtt_*{topic=~agents_*}  runner and watch state, agent-mqtt-publish -> mqtt2prom
  Loki        {service_name="claude-code"}  Claude Code OTel events (body = event name,
              attributes = its fields), secrets redacted by Alloy before Loki
  ClickHouse  default.events_core  Langfuse v4 spans, read-only user grafana_reader
Long ranges: switch the Prometheus datasource variable to "VictoriaMetrics (long)" (2y)."""

import json
import os
import sys

PROM = {"type": "prometheus", "uid": "${datasource}"}
LOKI = {"type": "loki", "uid": "${loki}"}
CH = {"type": "grafana-clickhouse-datasource", "uid": "langfuse-clickhouse"}
CLAUDE = '{service_name="claude-code"}'
# Textfile metrics come from one host; drop its scrape labels so a series is one session.
WS = "max without (instance, job, host) "
# mqtt2prom scrape labels, so one row per topic.
MQ = "max without (instance, pod, container, endpoint, job, namespace, service) "
LANGFUSE_TRACE = "https://langfuse.kblab.me/project/${__data.fields.project_id}/traces/${__data.fields.trace_id}"

# Dataviz categorical slots (agent-web charts.js), fixed per series, never cycled.
C1, C2, C3, C4 = "#3987e5", "#d95926", "#199e70", "#c98500"
BUCKET_COLORS = {"NEEDS": "red", "STALLED": "orange", "WORKING": C1, "UNCLEAR": "#66645e", "DONE": "green", "LANDED": "purple"}
TOKEN_COLORS = {"input": C1, "output": C2, "cacheRead": C3, "cacheCreation": C4}
OUTCOME_MAP = [{"type": "value", "options": {
    "0": {"text": "ok", "color": "green", "index": 0},
    "1": {"text": "WARN", "color": "orange", "index": 1},
    "2": {"text": "FAIL", "color": "red", "index": 2}}}]
TRIP_MAP = [{"type": "value", "options": {
    "0": {"text": "ok", "color": "green", "index": 0},
    "1": {"text": "TRIP", "color": "red", "index": 1}}}]

pid = [0]


def P(d):
    pid[0] += 1
    d["id"] = pid[0]
    return d


def thr(*steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def prom(expr, ref="A", legend="__auto", instant=False, fmt="time_series", interval=None):
    t = {"datasource": PROM, "editorMode": "code", "expr": expr, "format": fmt, "instant": instant,
         "range": not instant, "legendFormat": legend, "refId": ref}
    if interval:
        t["interval"] = interval
    return t


def loki(expr, ref="A", legend="__auto", instant=False):
    return {"datasource": LOKI, "editorMode": "code", "expr": expr, "legendFormat": legend,
            "queryType": "instant" if instant else "range", "refId": ref}


def sql(q, ref="A", fmt=1):
    """fmt: 0 time series (long rows are split into one series per text column), 1 table."""
    return {"datasource": CH, "editorType": "sql", "rawSql": q, "format": fmt, "queryType": "table" if fmt else "timeseries",
            "refId": ref}


def pos(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def color_by_name(colors):
    return [{"matcher": {"id": "byName", "options": n}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
            for n, c in colors.items()]


def stat(title, desc, target, gp, unit="none", steps=(("text", None),), no_value="0", decimals=None, ds=PROM):
    d = {"color": {"mode": "thresholds"}, "unit": unit, "noValue": no_value, "thresholds": thr(*steps)}
    if decimals is not None:
        d["decimals"] = decimals
    return P({"type": "stat", "title": title, "description": desc, "gridPos": gp, "datasource": ds, "targets": [target],
              "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                          "colorMode": "background" if len(steps) > 1 else "none", "graphMode": "none",
                          "textMode": "value", "justifyMode": "center"},
              "fieldConfig": {"defaults": d, "overrides": []}})


def ts(title, desc, targets, gp, unit="none", stack=False, bars=False, overrides=None, ds=PROM, decimals=None, interval=None):
    custom = {"drawStyle": "bars" if bars else "line", "lineInterpolation": "smooth", "lineWidth": 2,
              "fillOpacity": 80 if bars else (30 if stack else 0), "barAlignment": -1,
              "stacking": {"mode": "normal" if stack else "none"}, "spanNulls": False, "showPoints": "never"}
    d = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if decimals is not None:
        d["decimals"] = decimals
    p = {"type": "timeseries", "title": title, "description": desc, "gridPos": gp, "datasource": ds, "targets": targets,
         "fieldConfig": {"defaults": d, "overrides": overrides or []},
         "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi", "sort": "desc"}}}
    if interval:
        p["interval"] = interval
    return P(p)


def bars(title, desc, target, gp, unit="none", ds=PROM, steps=(("blue", None),)):
    return P({"type": "bargauge", "title": title, "description": desc, "gridPos": gp, "datasource": ds, "targets": [target],
              "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "valueMode": "text",
                          "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "minVizHeight": 16,
                          "maxVizHeight": 22, "namePlacement": "left", "sizing": "manual"},
              "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "thresholds"}, "thresholds": thr(*steps), "min": 0},
                              "overrides": []}})


def timeline(title, desc, target, gp, mappings=None, steps=(("green", None),), unit="none"):
    return P({"type": "state-timeline", "title": title, "description": desc, "gridPos": gp, "datasource": PROM, "targets": [target],
              "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8, "alignValue": "left",
                          "legend": {"showLegend": False}, "tooltip": {"mode": "single"}},
              "fieldConfig": {"defaults": {"unit": unit, "mappings": mappings or [], "color": {"mode": "thresholds"},
                                           "thresholds": thr(*steps), "custom": {"lineWidth": 0, "fillOpacity": 85}},
                              "overrides": []}})


def table(title, desc, targets, gp, ds=PROM, transformations=None, overrides=None, sort=None):
    opts = {"showHeader": True, "cellHeight": "sm"}
    if sort:
        opts["sortBy"] = [{"displayName": sort, "desc": True}]
    return P({"type": "table", "title": title, "description": desc, "gridPos": gp, "datasource": ds, "targets": targets,
              "transformations": transformations or [], "options": opts,
              "fieldConfig": {"defaults": {"noValue": "-", "custom": {"align": "left"}}, "overrides": overrides or []}})


def logs(title, desc, expr, gp):
    return P({"type": "logs", "title": title, "description": desc, "gridPos": gp, "datasource": LOKI,
              "targets": [loki(expr)],
              "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                          "dedupStrategy": "none", "prettifyLogMessage": False}})


def text(content, gp):
    return P({"type": "text", "title": "", "gridPos": gp, "options": {"mode": "markdown", "content": content},
              "transparent": True})


def row(title, y, collapsed=False, panels=None):
    return P({"type": "row", "title": title, "collapsed": collapsed, "gridPos": pos(0, y, 24, 1), "panels": panels or []})


def col(name, *props):
    return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": k, "value": v} for k, v in props]}


def hide(*names):
    return [col(n, ("custom.hidden", True)) for n in names]


def merge_rename(rename, exclude=("Time",)):
    return [{"id": "merge", "options": {}},
            {"id": "organize", "options": {"excludeByName": {e: True for e in exclude}, "renameByName": rename}}]



def v_prom():
    return {"name": "datasource", "label": "Metrics", "type": "datasource", "query": "prometheus", "hide": 0,
            "current": {"text": "Prometheus", "value": "prometheus"}, "regex": "", "refresh": 1, "options": []}


def v_loki():
    return {"name": "loki", "label": "Logs", "type": "datasource", "query": "loki", "hide": 0, "current": {},
            "regex": "", "refresh": 1, "options": []}


def v_query(name, label, query, multi=True, all_value=".*", ds=PROM, hide_=0, sort=1):
    v = {"name": name, "label": label, "type": "query", "datasource": ds, "hide": hide_, "refresh": 2, "sort": sort,
         "query": query, "definition": query if isinstance(query, str) else "", "multi": multi, "includeAll": multi,
         "current": {"text": "All", "value": "$__all"} if multi else {}, "options": [], "regex": ""}
    if multi:
        v["allValue"] = all_value
    return v


def dash(uid, title, desc, panels, variables, time_from="now-24h", refresh="1m", links=True):
    d = {"uid": uid, "title": title, "description": desc, "tags": ["agents", "claude-code", "homelab"],
         "timezone": "browser", "schemaVersion": 39, "refresh": refresh, "time": {"from": time_from, "to": "now"},
         "templating": {"list": variables}, "panels": panels, "editable": True, "graphTooltip": 1}
    if links:
        d["links"] = [{"title": "Agents", "type": "dashboards", "tags": ["agents"], "asDropdown": True, "includeVars": False,
                       "keepTime": True}]
    return d



def overview():
    pid[0] = 0
    cf = '{project=~"$project", model=~"$model"}'
    tok = f'claude_code_token_usage_tokens_total{cf}'
    y = 0
    tiles = [
        stat("Working", "Sessions fleet-sessions files under WORKING right now (agent_sessions).",
             prom(f'sum({WS}(agent_sessions{{bucket="WORKING"}}))', instant=True), pos(0, y, 3, 4)),
        stat("Need you", "Sessions in NEEDS: waiting on the human.",
             prom(f'sum({WS}(agent_sessions{{bucket="NEEDS"}}))', instant=True), pos(3, y, 3, 4),
             steps=(("green", None), ("red", 1))),
        stat("Stalled", "Sessions in STALLED: idle without having landed.",
             prom(f'sum({WS}(agent_sessions{{bucket="STALLED"}}))', instant=True), pos(6, y, 3, 4),
             steps=(("green", None), ("orange", 1))),
        stat("Asks open", "agent-ask questions waiting on the human. Empty when agent-ask could not be read.",
             prom(f'max({WS}(agent_asks_open))', instant=True), pos(9, y, 3, 4), no_value="-",
             steps=(("green", None), ("red", 1))),
        stat("Cost", "Claude Code spend over the dashboard range, from claude_code_cost_usage_USD_total (the client's own estimate).",
             prom(f"sum(increase(claude_code_cost_usage_USD_total{cf}[$__range]))", instant=True), pos(12, y, 3, 4),
             unit="currencyUSD", decimals=0),
        stat("Tokens", "Every token type over the range: input, output, cache read and cache creation.",
             prom(f"sum(increase({tok}[$__range]))", instant=True), pos(15, y, 3, 4), unit="short"),
        stat("Cache hit", "Cache-read tokens over all prompt tokens (input + cache read + cache creation). Higher is cheaper.",
             prom(f'sum(increase(claude_code_token_usage_tokens_total{{project=~"$project", model=~"$model", type="cacheRead"}}[$__range]))'
                  f' / sum(increase(claude_code_token_usage_tokens_total{{project=~"$project", model=~"$model", type=~"input|cacheRead|cacheCreation"}}[$__range]))',
                  instant=True), pos(18, y, 3, 4), unit="percentunit", decimals=0, no_value="-"),
        stat("PRs opened", "Pull requests Claude Code opened over the range.",
             prom('sum(increase(claude_code_pull_request_count_total{project=~"$project"}[$__range]))', instant=True),
             pos(21, y, 3, 4), decimals=0),
    ]
    y += 4
    panels = tiles + [
        ts("Sessions by bucket", "Live sessions per fleet-sessions bucket, stacked. NEEDS and STALLED are the ones to look at.",
           [prom(f"sum by (bucket) ({WS}(agent_sessions))", legend="{{bucket}}")], pos(0, y, 16, 8), stack=True, decimals=0,
           overrides=color_by_name(BUCKET_COLORS)),
        bars("Merged today by area", "PRs merged today per area, from the day-log (agent_day_merged).",
             prom(f"sort_desc({WS}(agent_day_merged))", legend="{{area}}", instant=True), pos(16, y, 8, 8)),
    ]
    y += 8
    panels += [
        ts("Cost per hour by project", "Spend per hour, one series per canonical project (claude-telemetry.sh sets it). "
           "Sessions started outside the claude wrappers carry no project and show as the empty series.",
           [prom(f"sum by (project) (increase(claude_code_cost_usage_USD_total{cf}[$__interval]))", legend="{{project}}")],
           pos(0, y, 12, 8), unit="currencyUSD", bars=True, stack=True, interval="1h"),
        ts("Cost per hour by model", "Spend per hour, one series per model.",
           [prom(f"sum by (model) (increase(claude_code_cost_usage_USD_total{cf}[$__interval]))", legend="{{model}}")],
           pos(12, y, 12, 8), unit="currencyUSD", bars=True, stack=True, interval="1h"),
    ]
    y += 8
    panels += [
        ts("Tokens per hour by type", "Cache read dominates when caching works; a jump in cacheCreation means prompts are being rebuilt.",
           [prom(f"sum by (type) (increase({tok}[$__interval]))", legend="{{type}}")], pos(0, y, 12, 8), unit="short",
           bars=True, stack=True, interval="1h", overrides=color_by_name(TOKEN_COLORS)),
        ts("Lines changed per hour", "Lines added and removed by Claude Code edits.",
           [prom('sum by (type) (increase(claude_code_lines_of_code_count_total{project=~"$project"}[$__interval]))', legend="{{type}}")],
           pos(12, y, 6, 8), unit="short", bars=True, interval="1h", overrides=color_by_name({"added": C3, "removed": C2})),
        ts("Commits and PRs per hour", "Commits and pull requests Claude Code created.",
           [prom('sum(increase(claude_code_commit_count_total{project=~"$project"}[$__interval]))', "A", "commits"),
            prom('sum(increase(claude_code_pull_request_count_total{project=~"$project"}[$__interval]))', "B", "PRs")],
           pos(18, y, 6, 8), bars=True, interval="1h", decimals=0, overrides=color_by_name({"commits": C1, "PRs": C2})),
    ]
    y += 8
    panels.append(timeline(
        "Context per session", "Context tokens each session holds, one row per tmux target (agent_session_context_tokens). "
        "Amber from 150k, red from 200k: auto-compaction fires around 240k.",
        prom(f"max by (target) ({WS}(agent_session_context_tokens))", legend="{{target}}"), pos(0, y, 24, 10),
        steps=(("#2b4f7a", None), ("orange", 150000), ("red", 200000)), unit="short"))
    y += 10
    panels += [
        bars("Waiting on the human, last 24h", "Hours sessions spent waiting on the human, per project (fleet-bench).",
             prom(f"sort_desc({WS}(agent_bench_waiting_hours))", legend="{{project}}", instant=True), pos(0, y, 8, 8),
             unit="h", steps=(("blue", None),)),
        bars("Stalled, last 24h", "Hours sessions sat STALLED, per project (fleet-bench).",
             prom(f"sort_desc({WS}(agent_bench_stalled_hours))", legend="{{project}}", instant=True), pos(8, y, 8, 8),
             unit="h", steps=(("orange", None),)),
        bars("Spend today by project", "Today's spend per project from the day-log (agent_day_cost_usd), TOTAL row excluded.",
             prom(f"sort_desc({WS}(agent_day_cost_usd))", legend="{{project}}", instant=True), pos(16, y, 8, 8),
             unit="currencyUSD", steps=(("blue", None),)),
    ]
    variables = [v_prom(),
                 v_query("project", "Project", "label_values(claude_code_cost_usage_USD_total, project)"),
                 v_query("model", "Model", "label_values(claude_code_cost_usage_USD_total, model)")]
    return dash("kb-agents-overview", "Agents / Overview",
                "What the Claude Code fleet is doing and what it costs. Sessions and bench come from the workstation's "
                "textfiles (fleet-sessions, fleet-bench, day-log); cost, tokens and output from Claude Code's own OTel "
                "metrics. Switch Metrics to 'VictoriaMetrics (long)' for ranges past 7 days.",
                panels, variables)



def session():
    pid[0] = 0
    sel = '{target="$target"}'
    sid = '{session_id="$session_id"}'
    y = 0
    panels = [
        stat("Context", "Context tokens the session holds now.", prom(f"max({WS}(agent_session_context_tokens{sel}))", instant=True),
             pos(0, y, 4, 4), unit="short", no_value="-", steps=(("text", None), ("orange", 150000), ("red", 200000))),
        stat("Idle", "Minutes since the session went idle.", prom(f"max({WS}(agent_session_idle_minutes{sel}))", instant=True),
             pos(4, y, 4, 4), unit="m", no_value="-"),
        stat("Age", "Minutes since the session started.", prom(f"max({WS}(agent_session_age_minutes{sel}))", instant=True),
             pos(8, y, 4, 4), unit="m", no_value="-"),
        stat("Phases", "Plan phases ticked out of the total, from the session's plan checklist.",
             prom(f'max({WS}(agent_session_phases_done{sel})) / max({WS}(agent_session_phases_total{sel}))', instant=True),
             pos(12, y, 4, 4), unit="percentunit", no_value="-", decimals=0),
        stat("Cost", "Spend of this Claude Code session over the range.",
             prom(f"sum(increase(claude_code_cost_usage_USD_total{sid}[$__range]))", instant=True), pos(16, y, 4, 4),
             unit="currencyUSD", decimals=2, no_value="-"),
        stat("Tokens", "All tokens of this session over the range.",
             prom(f"sum(increase(claude_code_token_usage_tokens_total{sid}[$__range]))", instant=True), pos(20, y, 4, 4),
             unit="short", no_value="-"),
    ]
    y += 4
    panels += [
        ts("Context tokens", "How full the session's context is over time; a sharp drop is a compaction.",
           [prom(f"max({WS}(agent_session_context_tokens{sel}))", legend="context")], pos(0, y, 8, 8), unit="short"),
        ts("Idle minutes", "Minutes idle; a sawtooth is a session that keeps getting nudged.",
           [prom(f"max({WS}(agent_session_idle_minutes{sel}))", legend="idle")], pos(8, y, 8, 8), unit="m"),
        ts("Branch state", "Commits behind base and uncommitted files in the session's worktree.",
           [prom(f"max({WS}(agent_session_behind{sel}))", "A", "behind"), prom(f"max({WS}(agent_session_dirty{sel}))", "B", "dirty")],
           pos(16, y, 8, 8), decimals=0),
    ]
    y += 8
    panels += [
        ts("Cost per hour by model", "This session's spend per hour.",
           [prom(f"sum by (model) (increase(claude_code_cost_usage_USD_total{sid}[$__interval]))", legend="{{model}}")],
           pos(0, y, 12, 8), unit="currencyUSD", bars=True, stack=True, interval="1h"),
        ts("Tokens per hour by type", "This session's tokens per hour.",
           [prom(f"sum by (type) (increase(claude_code_token_usage_tokens_total{sid}[$__interval]))", legend="{{type}}")],
           pos(12, y, 12, 8), unit="short", bars=True, stack=True, interval="1h", overrides=color_by_name(TOKEN_COLORS)),
    ]
    y += 8
    s_line = f'{CLAUDE} |= "$session_id"'
    panels += [
        bars("Tool calls by tool", "Tool results of this session over the range (Loki).",
             loki(f'sort_desc(sum by (tool) (count_over_time({s_line} |= "claude_code.tool_result" | json tool=`attributes.tool_name` [$__range])))',
                  legend="{{tool}}", instant=True), pos(0, y, 8, 10), ds=LOKI),
        logs("Errors and failed tools", "API errors and tool results that did not succeed, for this session.",
             f'{s_line} |~ "claude_code\\\\.(api_error|tool_result)" | json ev=`body`, tool=`attributes.tool_name`, ok=`attributes.success`, '
             f'status=`attributes.status_code`, err=`attributes.error` | ok="false" or ev="claude_code.api_error" '
             f'| line_format "{{{{.ev}}}} {{{{.tool}}}}{{{{.status}}}} {{{{.err}}}}"', pos(8, y, 16, 10)),
    ]
    y += 10
    panels.append(logs("Prompts", "What was asked in this session, newest first. Secrets are redacted by Alloy before Loki.",
                       f'{s_line} |= "claude_code.user_prompt" | json prompt=`attributes.prompt` | line_format "{{{{.prompt}}}}"',
                       pos(0, y, 24, 10)))
    variables = [v_prom(), v_loki(),
                 v_query("target", "Session", "label_values(agent_session_info, target)", multi=False),
                 v_query("session_id", "session_id", 'label_values(agent_session_info{target="$target"}, session_id)',
                         multi=False, hide_=0)]
    return dash("kb-agents-session", "Agents / Session",
                "One session in depth. Pick its tmux target; agent_session_info (fleet-sessions) maps it to the Claude "
                "Code session_id that the cost, token and Loki panels filter on. A session with no session_id yet shows "
                "only the fleet panels.",
                panels, variables, time_from="now-12h")



def errors():
    pid[0] = 0

    def ev(name):
        return f'{CLAUDE} |= "claude_code.{name}"'

    def count(name, extra="", rng="$__range"):
        return f"sum(count_over_time({ev(name)}{extra} [{rng}]))"

    y = 0
    panels = [
        stat("API requests", "Model calls over the range.", loki(count("api_request"), instant=True), pos(0, y, 4, 4), ds=LOKI),
        stat("API errors", "Failed model calls (rate limits, overload, network).", loki(count("api_error"), instant=True),
             pos(4, y, 4, 4), ds=LOKI, steps=(("green", None), ("orange", 1), ("red", 50))),
        stat("Tool calls", "Tool results over the range.", loki(count("tool_result"), instant=True), pos(8, y, 4, 4), ds=LOKI),
        stat("Tool failures", "Tool results with success=false.",
             loki(count("tool_result", ' | json ok=`attributes.success` | ok="false"'), instant=True), pos(12, y, 4, 4), ds=LOKI,
             steps=(("green", None), ("orange", 1))),
        stat("Rejected tools", "Tool calls the permission layer or a hook refused.",
             loki(count("tool_decision", ' | json d=`attributes.decision` | d="reject"'), instant=True), pos(16, y, 4, 4), ds=LOKI,
             steps=(("green", None), ("orange", 1))),
        stat("Compactions", "Context compactions, auto and manual.", loki(count("compaction"), instant=True), pos(20, y, 4, 4), ds=LOKI),
    ]
    y += 4
    panels += [
        ts("API errors by status", "Failed model calls per interval, by HTTP status. 429 is rate limiting, 529 overload.",
           [loki(f'sum by (status) (count_over_time({ev("api_error")} | json status=`attributes.status_code` [$__interval]))',
                 legend="{{status}}")], pos(0, y, 12, 8), ds=LOKI, bars=True, stack=True, interval="10m"),
        ts("API latency by model, p50 and p95", "Whole-request duration of model calls (api_request duration_ms).",
           [loki(f'quantile_over_time(0.95, {ev("api_request")} | json model=`attributes.model`, d=`attributes.duration_ms` | unwrap d [$__interval]) by (model)',
                 "A", "p95 {{model}}"),
            loki(f'quantile_over_time(0.5, {ev("api_request")} | json model=`attributes.model`, d=`attributes.duration_ms` | unwrap d [$__interval]) by (model)',
                 "B", "p50 {{model}}")], pos(12, y, 12, 8), unit="ms", ds=LOKI, interval="10m"),
    ]
    y += 8
    tr = f'{ev("tool_result")} | json tool=`attributes.tool_name`'
    panels.append(table(
        "Tools: calls, failures and p95 duration", "One row per tool over the range. A tool that fails often or got slow is the lead.",
        [loki(f"sum by (tool) (count_over_time({tr} [$__range]))", "A", instant=True),
         loki(f'sum by (tool) (count_over_time({tr}, ok=`attributes.success` | ok="false" [$__range]))', "B", instant=True),
         loki(f"quantile_over_time(0.95, {tr}, d=`attributes.duration_ms` | unwrap d [$__range]) by (tool)", "C", instant=True)],
        pos(0, y, 12, 10), ds=LOKI, sort="calls",
        transformations=merge_rename({"Value #A": "calls", "Value #B": "failed", "Value #C": "p95"}),
        overrides=[col("p95", ("unit", "ms")), col("failed", ("custom.cellOptions", {"type": "color-text"}),
                                                   ("color", {"mode": "thresholds"}), ("thresholds", thr(("text", None), ("orange", 1))))]))
    hk = f'{ev("hook_execution_complete")} | json hook=`attributes.hook_name`'
    panels.append(table(
        "Hooks: runs, blocks and p95 duration", "One row per hook over the range. Blocks are hooks that stopped a tool call or a turn.",
        [loki(f"sum by (hook) (count_over_time({hk} [$__range]))", "A", instant=True),
         loki(f"sum by (hook) (sum_over_time({hk}, b=`attributes.num_blocking` | unwrap b [$__range]))", "B", instant=True),
         loki(f"quantile_over_time(0.95, {hk}, d=`attributes.total_duration_ms` | unwrap d [$__range]) by (hook)", "C", instant=True)],
        pos(12, y, 12, 10), ds=LOKI, sort="p95",
        transformations=merge_rename({"Value #A": "runs", "Value #B": "blocks", "Value #C": "p95"}),
        overrides=[col("p95", ("unit", "ms"))]))
    y += 10
    panels += [
        bars("Skills activated", "Skill activations by name over the range.",
             loki(f'sort_desc(sum by (skill) (count_over_time({ev("skill_activated")} | json skill=`attributes["skill.name"]` [$__range])))',
                  legend="{{skill}}", instant=True), pos(0, y, 8, 10), ds=LOKI),
        ts("Compactions per hour", "Auto compactions mean a session ran its context full.",
           [loki(f'sum by (trigger) (count_over_time({ev("compaction")} | json trigger=`attributes.trigger` [$__interval]))',
                 legend="{{trigger}}")], pos(8, y, 8, 10), ds=LOKI, bars=True, stack=True, interval="1h", decimals=0),
        ts("Subagents finished per hour", "Subagents that completed, by type.",
           [loki(f'sum by (type) (count_over_time({ev("subagent_completed")} | json type=`attributes.agent_type` [$__interval]))',
                 legend="{{type}}")], pos(16, y, 8, 10), ds=LOKI, bars=True, stack=True, interval="1h", decimals=0),
    ]
    y += 10
    panels.append(logs("API errors", "Every failed model call, newest first.",
                       f'{ev("api_error")} | json status=`attributes.status_code`, model=`attributes.model`, err=`attributes.error`, '
                       f'sid=`attributes["session.id"]` | line_format "{{{{.status}}}} {{{{.model}}}} {{{{.err}}}}  session {{{{.sid}}}}"',
                       pos(0, y, 24, 8)))
    y += 8
    panels.append(logs("Prompt search", "Prompts matching the Search box (a regex, case-insensitive). Empty search lists all. "
                       "Secrets are redacted by Alloy before Loki.",
                       f'{ev("user_prompt")} |~ "(?i)$search" | json prompt=`attributes.prompt`, sid=`attributes["session.id"]` '
                       f'| line_format "{{{{.sid}}}}  {{{{.prompt}}}}"', pos(0, y, 24, 12)))
    variables = [v_loki(),
                 {"name": "search", "label": "Search prompts", "type": "textbox", "query": "", "current": {"text": "", "value": ""},
                  "hide": 0, "options": []}]
    return dash("kb-agents-errors", "Agents / Errors and tools",
                "Claude Code's event log in Loki: model call errors and latency, tool and hook behaviour, skills, "
                "compactions and prompt search. Loki keeps a year; secrets are redacted by Alloy before they land.",
                panels, variables, time_from="now-24h")



def runners():
    pid[0] = 0

    def named(metric, kind, over="{}"):
        sel = over.format(f'{metric}{{topic=~"agents_.+_{kind}_.+"}}')
        return (f'label_replace(label_replace({MQ}({sel}), "{kind}", "$2", "topic", "agents_(.+?)_{kind}_(.+)"),'
                f' "host", "$1", "topic", "agents_(.+?)_{kind}_(.+)")')

    def by(kind, metric):
        return f"max by ({kind}, host) ({named(metric, kind)})"

    y = 0
    panels = [
        stat("Runners", "agentctl runners publishing state.", prom('count(mqtt_failed{topic=~"agents_.+_runner_.+"})', instant=True),
             pos(0, y, 4, 4)),
        stat("Failed", "Runners whose last run failed.", prom("cc:runners_failed", instant=True), pos(4, y, 4, 4),
             steps=(("green", None), ("red", 1))),
        stat("Running", "Runners with a run in flight.", prom("cc:runners_running", instant=True), pos(8, y, 4, 4)),
        stat("Watches", "Sentinel watches publishing state.", prom("cc:watches_total", instant=True), pos(12, y, 4, 4)),
        stat("Tripped", "Watches in TRIP now.", prom("cc:watches_tripped", instant=True), pos(16, y, 4, 4),
             steps=(("green", None), ("red", 1))),
        stat("Stale", "Watches whose feed stopped updating.", prom("cc:watches_stale", instant=True), pos(20, y, 4, 4),
             steps=(("green", None), ("orange", 1))),
    ]
    y += 4
    panels.append(table(
        "Runners", "One row per runner: when its last run finished, how long it took, and how it went "
        "(outcome_code from agent-mqtt-publish: 0 ok, 1 warn for BLOCKED or PENDING, 2 fail for FAILED or STALLED).",
        [prom(f"{by('runner', 'mqtt_updated_epoch')} * 1000", "A", instant=True, fmt="table"),
         prom(by("runner", "mqtt_duration_s"), "B", instant=True, fmt="table"),
         prom(by("runner", "mqtt_outcome_code"), "C", instant=True, fmt="table"),
         prom(by("runner", "mqtt_running"), "D", instant=True, fmt="table")],
        pos(0, y, 12, 12), sort="last run",
        transformations=merge_rename({"Value #A": "last run", "Value #B": "took", "Value #C": "outcome", "Value #D": "running"}),
        overrides=[col("last run", ("unit", "dateTimeFromNow")), col("took", ("unit", "s")),
                   col("outcome", ("mappings", OUTCOME_MAP), ("custom.cellOptions", {"type": "color-background", "mode": "basic"}),
                       ("custom.width", 90)),
                   col("running", ("mappings", [{"type": "value", "options": {"0": {"text": "-"}, "1": {"text": "RUNNING", "color": "blue"}}}]),
                       ("custom.cellOptions", {"type": "color-text"}), ("custom.width", 90))]))
    panels.append(table(
        "Watches", "One row per Sentinel watch: TRIP now, stale feed, and since when it has been in its current state.",
        [prom(by("watch", "mqtt_tripped"), "A", instant=True, fmt="table"),
         prom(by("watch", "mqtt_stale"), "B", instant=True, fmt="table"),
         prom(f"{by('watch', 'mqtt_since_epoch')} * 1000", "C", instant=True, fmt="table")],
        pos(12, y, 12, 12), sort="tripped",
        transformations=merge_rename({"Value #A": "tripped", "Value #B": "stale", "Value #C": "since"}),
        overrides=[col("tripped", ("mappings", TRIP_MAP), ("custom.cellOptions", {"type": "color-background", "mode": "basic"}),
                       ("custom.width", 80)),
                   col("stale", ("mappings", [{"type": "value", "options": {"0": {"text": "-"}, "1": {"text": "STALE", "color": "orange"}}}]),
                       ("custom.cellOptions", {"type": "color-text"}), ("custom.width", 80)),
                   col("since", ("unit", "dateTimeFromNow"))]))
    y += 12
    panels.append(timeline("Runner outcomes", "Outcome of each runner's last run over time.",
                           prom(f"max by (runner) ({named('mqtt_outcome_code', 'runner')})", legend="{{runner}}"),
                           pos(0, y, 24, 10), mappings=OUTCOME_MAP))
    y += 10
    panels.append(timeline("Watch state", "TRIP periods of each watch over time.",
                           prom(f"max by (watch) ({named('mqtt_tripped', 'watch')})", legend="{{watch}}"),
                           pos(0, y, 24, 10), mappings=TRIP_MAP))
    y += 10
    panels.append(ts("Minutes in TRIP per day", "How long each watch spent tripped, per day. A watch that trips daily is noise or a real chronic fault.",
                     [prom(f"sum by (watch) ({named('mqtt_tripped', 'watch', 'avg_over_time({}[1d])')}) * 1440", legend="{{watch}}")],
                     pos(0, y, 24, 8), unit="m", bars=True, stack=True, interval="1d"))
    return dash("kb-agents-runners", "Agents / Runners and watches",
                "agentctl runners and Sentinel watches as published over MQTT (agent-mqtt-publish -> mosquitto -> mqtt2prom). "
                "The numeric fields (updated_epoch, duration_s, outcome_code, since_epoch) start on 2026-10-29.",
                panels, [v_prom()], time_from="now-7d")



def langfuse():
    pid[0] = 0
    base = "FROM default.events_core WHERE $__timeFilter(start_time) AND service_name IN (${service:singlequote})"
    gen = base + " AND type = 'GENERATION'"
    dur = "dateDiff('millisecond', start_time, end_time)"
    y = 0
    panels = [
        stat("Spans", "Spans Langfuse stored over the range.", sql(f"SELECT count() {base}"), pos(0, y, 6, 4), ds=CH),
        stat("Traces", "Distinct traces (one per Claude Code interaction or LiteLLM request).",
             sql(f"SELECT uniq(trace_id) {base}"), pos(6, y, 6, 4), ds=CH),
        stat("LLM cost", "Sum of total_cost on GENERATION spans (Langfuse's model prices).",
             sql(f"SELECT sum(total_cost) {gen}"), pos(12, y, 6, 4), ds=CH, unit="currencyUSD", decimals=2),
        stat("LLM p95", "95th percentile duration of GENERATION spans.", sql(f"SELECT quantile(0.95)({dur}) {gen}"),
             pos(18, y, 6, 4), ds=CH, unit="ms"),
    ]
    y += 4
    panels += [
        ts("Spans by type", "Spans per interval by Langfuse type (GENERATION, TOOL, SPAN, AGENT).",
           [sql(f"SELECT $__timeInterval(start_time) AS time, type, count() AS spans {base} GROUP BY time, type ORDER BY time", fmt=0)],
           pos(0, y, 12, 8), ds=CH, bars=True, stack=True),
        ts("Cost by model", "GENERATION cost per interval by model.",
           [sql(f"SELECT $__timeInterval(start_time) AS time, provided_model_name AS model, sum(total_cost) AS cost {gen} "
                "GROUP BY time, model ORDER BY time", fmt=0)], pos(12, y, 12, 8), ds=CH, unit="currencyUSD", bars=True, stack=True),
    ]
    y += 8
    panels.append(ts("LLM latency p95 by model", "95th percentile GENERATION duration per interval.",
                     [sql(f"SELECT $__timeInterval(start_time) AS time, provided_model_name AS model, quantile(0.95)({dur}) AS p95 {gen} "
                          "GROUP BY time, model ORDER BY time", fmt=0)], pos(0, y, 24, 8), ds=CH, unit="ms"))
    y += 8
    panels.append(table(
        "Models", "One row per model over the range: calls, cost, latency and tokens.",
        [sql(f"SELECT provided_model_name AS model, count() AS calls, sum(total_cost) AS cost, quantile(0.5)({dur}) AS p50, "
             f"quantile(0.95)({dur}) AS p95, sum(usage_details['input']) AS input, sum(usage_details['output']) AS output {gen} "
             "GROUP BY model ORDER BY cost DESC")],
        pos(0, y, 12, 10), ds=CH,
        overrides=[col("cost", ("unit", "currencyUSD"), ("decimals", 2)), col("p50", ("unit", "ms")), col("p95", ("unit", "ms")),
                   col("input", ("unit", "short")), col("output", ("unit", "short"))]))
    panels.append(table(
        "Tools", "TOOL spans by name over the range: calls, p95 duration and errors (level ERROR).",
        [sql(f"SELECT name AS tool, count() AS calls, quantile(0.95)({dur}) AS p95, countIf(level = 'ERROR') AS errors "
             f"{base} AND type = 'TOOL' GROUP BY tool ORDER BY calls DESC LIMIT 40")],
        pos(12, y, 12, 10), ds=CH, overrides=[col("p95", ("unit", "ms"))]))
    y += 10
    panels.append(table(
        "Slowest traces", "The 20 longest traces in the range. Click a trace to open it in Langfuse.",
        [sql("SELECT trace_id, project_id, any(trace_name) AS name, any(service_name) AS service, min(start_time) AS started, "
             f"dateDiff('millisecond', min(start_time), max(end_time)) AS duration, sum(total_cost) AS cost, count() AS spans {base} "
             "GROUP BY trace_id, project_id ORDER BY duration DESC LIMIT 20")],
        pos(0, y, 24, 10), ds=CH,
        overrides=[col("trace_id", ("links", [{"title": "Open in Langfuse", "url": LANGFUSE_TRACE, "targetBlank": True}])),
                   col("duration", ("unit", "ms")), col("cost", ("unit", "currencyUSD"), ("decimals", 3)),
                   col("started", ("unit", "dateTimeAsLocal"))] + hide("project_id")))
    service = {"name": "service", "label": "Service", "type": "query", "datasource": CH, "hide": 0, "refresh": 2, "sort": 1,
               "query": "SELECT DISTINCT service_name FROM default.events_core WHERE start_time > now() - INTERVAL 7 DAY",
               "definition": "", "multi": True, "includeAll": True, "current": {"text": "All", "value": "$__all"},
               "options": [], "regex": ""}
    return dash("kb-agents-langfuse", "Agents / Langfuse",
                "Langfuse's spans straight from its ClickHouse (default.events_core, Langfuse v4), read-only. "
                "events_core is a ReplacingMergeTree, so counts can run slightly high until ClickHouse merges duplicates. "
                "Trace rows link to langfuse.kblab.me.",
                panels, [service], time_from="now-24h")


OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps/monitoring/dashboards")
for name, build in (("overview", overview), ("session", session), ("errors", errors), ("runners", runners), ("langfuse", langfuse)):
    path = os.path.join(sys.argv[1] if len(sys.argv) > 1 else OUT, f"agents-{name}.json")
    with open(path, "w") as f:
        json.dump(build(), f, indent=2)
        f.write("\n")
