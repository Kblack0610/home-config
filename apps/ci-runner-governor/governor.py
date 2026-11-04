#!/usr/bin/env python3
"""ci-runner-governor - how many Unity pool runners may take new jobs, per host.

WHY THIS EXISTS:

The Unity pool is a fixed set of registered runners (ansible/inventory.yml,
unity_ci_runners). How many of them a host can carry depends on what else it is
doing: razer also runs four platform slots, asus hosts Forgejo and the databases,
hp-victus reserves cores for the game server. A fixed count is either too high
on a busy night (asus at 94 C, razer stalling 28% of the time on CPU) or leaves
an idle host idle. Registering a runner is slow (ansible, a cold Library/ of
15-60 minutes), so capacity moves the other way: every slot stays registered and
warm, and the governor decides which of them are ADMITTED, meaning they carry
the `unity` label that gate jobs ask for.

A withheld runner has `unity` and `unity-full` swapped for `unity-withheld`.
GitHub matches labels when it assigns a job, so a runner withheld while busy
finishes that job and takes no other; nothing is killed or restarted.

Inputs, all from Prometheus, once per CYCLE_SECONDS: each host's CPU idle share
and CPU pressure (node_exporter, 5-minute rates; the load average misleads on
razer, whose slots are pinned to cores, so work queues on some cores while others
idle), CPU temperature and free memory; and from github-runner-exporter, every
pool runner's state, which are withheld, and how many pool jobs wait.

Per host and cycle the verdict is HOT (withhold one runner), ROOM (re-admit one,
when jobs wait) or STEADY. After an action the host is left alone for
cooldown_seconds, since a runner's job takes minutes to show in the 5-minute
rates. The pool never drops below pool_min_admitted, which keeps Sentinel's
unity-runners-online watch (>=1 runner labelled unity) green, and never admits
more than pool_max_admitted: each admitted runner starts a job, and with it a
Unity activation, every shard, and about 15 activations in 90 minutes is where
activation began failing.

A host short of disk is FULL: every slot goes, one per cycle, past the host's floor and
its cooldown, since free disk has no 5-minute lag and a Unity slot can write tens of GB
in a job. Only the pool floor holds. Kubelet evicts at 5% free and does not stop until 15%,
and on 2026-10-31 that took the cluster's monitoring and pager down for 8 hours. A host
comes back only above the higher admit line.

A standby host (config "standby", for a host with no CPU pressure or sensors to
judge; none since 2026-11-03) is overflow: GitHub has no way to prefer a runner, so its
slots stay withheld until more jobs wait than the other hosts' idle slots and this
cycle's admits can take. Then one slot per cycle comes in, up to the host's
max_admitted, and goes back once nothing waits. A job pinned to its host label
needs no pool label, so a withheld slot takes it.

A standby host with a "power" block (aws-burst-1) is also switched on and off. When a
standby admit would want it, its instance is stopped and a waiting job asks only for labels
its runners carry, the instance is started. Those labels are read from the exporter's
github_runner_state rows for the host, offline ones included, plus the pool labels a
withheld runner gets back on admit; with none read it is never started. So a job pinned
to the LAN (`lan`) does not start an off-LAN host that could never take it. A start costs
money, so it waits for start_min_excess jobs it can take past what the other hosts cover,
held for start_after_seconds; a short burst never starts it. Its slots count as room only
once their runners report online, so a start, or a failed one, is never room. Once no slot
on it has been busy for idle_off_seconds, or it has run max_hours_day this UTC day, its
slots are withheld one per cycle and the instance is stopped once none is busy, so a slot
never loses a job to the stop and comes back withheld. The ConfigMap
ci-runner-governor-override (the agent-web Infra page's button) beats all of that until its
end time: "on" starts it and keeps it from idle-off and the day cap, "off" drains and stops
it and keeps it stopped. The month's cap is an AWS Budgets action, outside the governor.
A power block with "live": true acts on that host for real (labels and power) even under
DRY_RUN. Without AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY the hook logs why once and
does nothing.

For the agent-web Infra page it also exports, all as numbers so no series churns: each
powered host's rule progress (idle seconds against idle_off_seconds, the jobs it could take
past the other hosts against start_min_excess, the start hold against start_after_seconds),
its usd_per_hour, its last start or stop, whether each host is acted on live, and, with a
"budget" block in config.json, the AWS budget's month: spend, forecast, limit, alert lines
and the stop action's threshold and status. Budgets has no event API and refreshes its
numbers a few times a day, so it is read once per BUDGET_POLL_SECONDS with the same key;
a failed read keeps the last numbers, sets ci_governor_budget_ok 0, and never touches a
power decision.

DRY_RUN=1 (the default) logs and exports every decision and changes nothing.
Live, it needs GITHUB_TOKEN with Organization > Self-hosted runners: write.

The loop is a poll: neither host load nor the GitHub queue has an event this
can await (workflow_job webhooks could replace the queue half later).
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Lock, Thread

PROM = os.environ.get("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090").rstrip("/")
API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
PORT = int(os.environ.get("PORT", "9321"))
CONFIG = os.environ.get("GOVERNOR_CONFIG", "/app/config.json")
AWS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
CYCLE_SECONDS = 60
# A power block's settings when config.json leaves one out. A start costs money (about
# $1.85/h for aws-burst-1), so only a deep queue held this long starts one, and a day of
# running time is capped; the month's cap is an AWS Budgets action (platform infra/aws-ci-burst).
POWER_DEFAULTS = {"idle_off_seconds": 900, "start_min_excess": 4, "start_after_seconds": 300, "max_hours_day": 12}
# The budget's numbers move a few times a day and the API has no event, so one read an hour.
BUDGET_POLL_SECONDS = 3600
OVERRIDE_DIR = os.environ.get("GOVERNOR_OVERRIDE_DIR", "/override")
API_ERRORS = (urllib.error.URLError, OSError, ValueError, KeyError)
POWER_ERRORS = API_ERRORS + (ET.ParseError,)
BUDGET_ERRORS = API_ERRORS + (TypeError,)

UNAME = "on(instance) group_left(nodename) node_uname_info"
QUERIES = {
    "idle": f'avg by (nodename) (rate(node_cpu_seconds_total{{mode="idle"}}[5m]) * {UNAME})',
    "psi": f"max by (nodename) (rate(node_pressure_cpu_waiting_seconds_total[5m]) * {UNAME})",
    "tctl": ("max by (nodename) (node_hwmon_temp_celsius * on(instance, chip) group_left(chip_name) "
             f'node_hwmon_chip_names{{chip_name=~"k10temp|coretemp"}} * {UNAME})'),
    "mem": f"max by (nodename) (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes * {UNAME})",
    "disk": ('max by (nodename) (node_filesystem_avail_bytes{mountpoint="/"} / '
             f'node_filesystem_size_bytes{{mountpoint="/"}} * {UNAME})'),
}


def load_config(path):
    with open(path) as f:
        return with_defaults(json.load(f))


def with_defaults(cfg):
    """Each host's thresholds: the defaults with its own overrides on top."""
    for host, over in cfg["hosts"].items():
        merged = {k: dict(v) if isinstance(v, dict) else v for k, v in cfg["defaults"].items()}
        for k, v in over.items():
            merged[k] = {**merged[k], **v} if isinstance(v, dict) else v
        cfg["hosts"][host] = merged
    return cfg


def prom(query):
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    with urllib.request.urlopen(url, timeout=10) as r:
        body = json.load(r)
    if body.get("status") != "success":
        raise ValueError(f"prometheus: {body.get('error', 'query failed')}")
    return body["data"]["result"]


def by_host(result):
    return {r["metric"].get("nodename") or r["metric"].get("host"): float(r["value"][1]) for r in result}


def observe(pool_label):
    """One reading of every host and pool runner. Raises when any source fails, so a
    blind cycle decides nothing instead of reading as an idle machine."""
    hosts = {k: by_host(prom(q)) for k, q in QUERIES.items()}
    runners = {}
    for r in prom(f'github_runner_state{{pool="{pool_label}",state=~"running|idle"}}'):
        m = r["metric"]
        if m.get("scope") == "org":
            runners[m["name"]] = {"host": m["host"], "busy": m["state"] == "running",
                                  "elapsed": float(r["value"][1]), "withheld": False}
    # The exporter drops these rows when it cannot list the org's runners, so none means
    # the list is gone, not that the pool is empty.
    if not runners:
        raise ValueError(f"no {pool_label} runners in the metrics")
    for r in prom("github_runner_withheld"):
        if r["metric"]["name"] in runners:
            runners[r["metric"]["name"]]["withheld"] = True
    waiting = [label_set(r["metric"].get("labels", ""))
               for r in prom(f'github_actions_job_waiting{{pool="{pool_label}",stuck!="true"}}')]
    return hosts, runners, waiting


def label_set(joined):
    # GitHub matches labels without regard to case.
    return frozenset(l.lower() for l in joined.split(",") if l)


def host_labels(cfg, host):
    """Every label a pool runner on host carries, in any state (a stopped instance's runners
    stay registered, offline), plus the pool labels a withheld one gets back on admit.
    Empty when no runner of it is listed."""
    have = set()
    for r in prom(f'github_runner_state{{host="{host}",pool="{cfg["pool_label"]}"}}'):
        have |= label_set(r["metric"].get("labels", ""))
    return frozenset(have | label_set(f'{cfg["pool_label"]},{cfg["full_label"]}')) if have else frozenset()


def verdict(h, t):
    """FULL, HOT, ROOM or STEADY for one host's readings h against its thresholds t. A
    missing reading never counts as room, and only temperature may be absent (hp-victus's
    coretemp is the only sensor some kernels expose). Disk comes first so a host that is
    full is treated as full whatever else is missing."""
    idle, psi, mem, tctl, disk = (h.get(k) for k in ("idle", "psi", "mem", "tctl", "disk"))
    w, a = t["withhold"], t["admit"]
    if disk is not None and disk < w["disk"]:
        return "FULL", f"free disk {disk:.2f} < {w['disk']}"
    if None in (idle, psi, mem):
        return "STEADY", "no reading"
    if idle < w["idle"]:
        return "HOT", f"idle {idle:.2f} < {w['idle']}"
    if psi > w["psi"]:
        return "HOT", f"cpu pressure {psi:.2f} > {w['psi']}"
    if tctl is not None and tctl >= w["tctl"]:
        return "HOT", f"tctl {tctl:.0f} >= {w['tctl']}"
    if mem < w["mem"]:
        return "HOT", f"free memory {mem:.2f} < {w['mem']}"
    if (idle >= a["idle"] and psi <= a["psi"] and mem >= a["mem"] and (tctl is None or tctl < a["tctl"])
            and disk is not None and disk >= a["disk"]):
        return "ROOM", f"idle {idle:.2f}, cpu pressure {psi:.2f}"
    return "STEADY", f"idle {idle:.2f}, cpu pressure {psi:.2f}"


def decide(cfg, hosts, runners, waiting, last_action, now, instances=None):
    """[(host, action, runner, verdict, why)]: at most one action per host, withholds
    first so a re-admit elsewhere can use the room the pool keeps. waiting is one label
    set per waiting pool job; instances is power_readings() for the standby hosts with a
    power block."""
    queue = len(waiting)
    admitted = sum(1 for r in runners.values() if not r["withheld"])
    # A standby host can go to zero, so only the other hosts hold the pool's floor.
    floor_held = sum(1 for r in runners.values() if not r["withheld"] and r["host"] in cfg["hosts"])
    plans = []
    for host in sorted(cfg["hosts"]):
        t = cfg["hosts"][host]
        mine = {n: r for n, r in runners.items() if r["host"] == host}
        v, why = verdict({k: hosts[k].get(host) for k in QUERIES}, t)
        cooling = v != "FULL" and now - last_action.get(host, 0) < cfg["cooldown_seconds"]
        on = sorted((n for n, r in mine.items() if not r["withheld"]),
                    # Idle first, then the busy one nearest its end (longest running).
                    key=lambda n: (mine[n]["busy"], -mine[n]["elapsed"], n))
        off = sorted(n for n, r in mine.items() if r["withheld"])
        action, runner = "hold", ""
        if cooling:
            why = f"cooldown ({why})"
        elif ((v == "FULL" and on) or (v == "HOT" and len(on) > t["min_admitted"])) \
                and floor_held > cfg["pool_min_admitted"]:
            action, runner = "withhold", on[0]
            admitted -= 1
            floor_held -= 1
        elif v == "ROOM" and off and queue > 0:
            action, runner = "admit", off[0]
        elif v in ("HOT", "FULL") and on:
            why += "; at the floor"
        plans.append((host, action, runner, v, why))
    out = []
    for host, action, runner, v, why in sorted(plans, key=lambda p: p[1] != "withhold"):
        if action == "admit":
            if admitted >= cfg["pool_max_admitted"]:
                action, runner, why = "hold", "", why + f"; pool at its {cfg['pool_max_admitted']} cap"
            else:
                admitted += 1
        out.append((host, action, runner, v, why))
    # Slots the other hosts already have for the waiting jobs. Admitted slots can sit idle
    # for minutes with jobs queued, so one idle slot must not hold the overflow back.
    covered = sum(1 for _, action, *_ in out if action == "admit")
    covered += sum(1 for r in runners.values() if not r["withheld"] and not r["busy"] and r["host"] in cfg["hosts"])
    for host in sorted(cfg["standby"]):
        mine = {n: r for n, r in runners.items() if r["host"] == host}
        on = sorted((n for n, r in mine.items() if not r["withheld"]),
                    key=lambda n: (mine[n]["busy"], -mine[n]["elapsed"], n))
        off = sorted(n for n, r in mine.items() if r["withheld"])
        cap = cfg["standby"][host]["max_admitted"]
        inst = (instances or {}).get(host)
        p = {**POWER_DEFAULTS, **cfg["standby"][host].get("power", {})}
        action, runner, why = "hold", "", "overflow"
        over = inst.get("override") if inst else None
        drain, cause, wanting, want = "", "", False, 0
        if inst and inst["state"] == "running":
            # Before the job queue: a host told off, past its day, or idle this long cannot
            # take what waits (an idle one: a lan job).
            if over == "off":
                drain, cause = "override off", "override"
            elif over != "on" and inst.get("hours_today", 0) >= p["max_hours_day"]:
                drain, cause = f"ran {inst['hours_today']:.1f}h today, its {p['max_hours_day']}h cap", "cap"
            elif over != "on" and now - inst["busy_at"] >= p["idle_off_seconds"]:
                drain, cause = f"no slot busy for {int(now - inst['busy_at'])}s", "idle"
        if drain:
            # One slot per cycle, busy ones too (a withheld slot finishes its job), and the
            # stop only once no slot is busy, so a stop never kills a job.
            busy = sum(1 for r in mine.values() if r["busy"])
            if on:
                action, runner, why = "withhold", on[0], drain
            elif busy:
                why = f"{drain}; {busy} jobs finishing"
            else:
                action, why = "stop", drain
        elif len(on) > cap:
            action, runner, why = "withhold", on[0], f"over its {cap} cap"
        elif over == "on" and inst and inst["state"] == "running" and off:
            if admitted < cfg["pool_max_admitted"]:
                action, runner, why = "admit", off[0], "override on"
                admitted += 1
                covered += 1
            else:
                why = f"override on; pool at its {cfg['pool_max_admitted']} cap"
        elif queue == 0 and on and not mine[on[0]]["busy"] and over != "on":
            action, runner, why = "withhold", on[0], "nothing waits"
        elif queue > covered and off and len(on) < cap:
            if admitted < cfg["pool_max_admitted"]:
                action, runner, why = "admit", off[0], f"{queue} jobs wait, the other hosts cover {covered}"
                admitted += 1
                covered += 1
            else:
                why = f"pool at its {cfg['pool_max_admitted']} cap"
        elif inst and inst["state"] == "stopped" and over == "on":
            action, why, cause = "start", "override on", "override"
        elif inst and inst["state"] == "stopped" and over == "off":
            why = "override off"
        elif inst and inst["state"] == "stopped" and queue > covered:
            # Not room yet: its slots count once their runners are online and admitted. A
            # start costs money, so only a queue this much deeper than the other hosts can
            # take, for start_after_seconds in a row, starts it: a short burst waits instead.
            takes = sum(1 for need in waiting if need <= inst["labels"])
            want = max(0, min(takes, queue - covered))
            if not inst["labels"]:
                why = "no runner labels read"
            elif not takes:
                why = f"{queue} jobs wait, none it can take"
            elif inst.get("hours_today", 0) >= p["max_hours_day"]:
                why = f"ran {inst['hours_today']:.1f}h today, its {p['max_hours_day']}h cap"
            elif want < p["start_min_excess"]:
                why = f"{want} jobs it can take past the other hosts' {covered}, starts at {p['start_min_excess']}"
            elif admitted >= cfg["pool_max_admitted"]:
                why = f"pool at its {cfg['pool_max_admitted']} cap"
            else:
                wanting = True
                since = inst["want_since"] = inst.get("want_since") or now
                why = f"{queue} jobs wait, {takes} it can take, the other hosts cover {covered}"
                if now - since >= p["start_after_seconds"]:
                    action, cause = "start", "queue"
                else:
                    why += f"; held {int(now - since)}s of {p['start_after_seconds']}s"
        elif inst and inst["state"] != "running":
            why = f"instance {inst['state']}"
        if inst is not None:
            inst["draining"] = bool(drain)
            if not wanting:
                inst["want_since"] = None
            # How far along each rule is, for the Infra page: the reasons above are free text.
            inst["idle_seconds"] = now - inst["busy_at"] if inst["state"] == "running" else 0
            inst["want"] = want if inst["state"] == "stopped" else 0
            inst["held_seconds"] = now - inst["want_since"] if wanting else 0
            # cycle() keeps it as the power state's reason once the call succeeds.
            inst["next_cause"] = cause if action in ("start", "stop") else ""
        out.append((host, action, runner, "STANDBY", why))
    return out


def gh(method, path, body=None):
    req = urllib.request.Request(f"{API}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r) if r.status != 204 else None


def runner_id(org, name):
    page = 1
    while True:
        data = gh("GET", f"orgs/{org}/actions/runners?per_page=100&page={page}")
        for r in data.get("runners", []):
            if r["name"] == name:
                return r["id"], {l["name"] for l in r["labels"]}
        if not data.get("runners"):
            raise KeyError(f"no runner {name} in {org}")
        page += 1


def apply(cfg, action, name):
    """Swap the pool labels for the withheld one, or back. Adds before it removes, so a
    runner is never left with neither."""
    org, pool = cfg["org"], [cfg["pool_label"], cfg["full_label"]]
    rid, have = runner_id(org, name)
    add, drop = (pool, [cfg["withheld_label"]]) if action == "admit" else ([cfg["withheld_label"]], pool)
    if set(add) - have:
        gh("POST", f"orgs/{org}/actions/runners/{rid}/labels", {"labels": sorted(set(add) - have)})
    for label in drop:
        if label in have:
            gh("DELETE", f"orgs/{org}/actions/runners/{rid}/labels/{urllib.parse.quote(label)}")


def sigv4(service, region, key, secret, host, body, t, extra=None,
          content_type="application/x-www-form-urlencoded; charset=utf-8"):
    """The headers for a POST to / on host, SigV4-signed by hand: the pod is python:alpine
    with no boto3. extra holds more x-amz-* headers to sign (lower case)."""
    amz_date, day = time.strftime("%Y%m%dT%H%M%SZ", t), time.strftime("%Y%m%d", t)
    headers = {"content-type": content_type, "host": host, "x-amz-date": amz_date, **(extra or {})}
    signed = ";".join(sorted(headers))
    canonical = "\n".join(["POST", "/", "", *(f"{k}:{headers[k]}" for k in sorted(headers)), "",
                           signed, hashlib.sha256(body.encode()).hexdigest()])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (day, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={key}/{scope}, "
                                f"SignedHeaders={signed}, Signature={sig}")
    return headers


class Ec2:
    """One instance's state, start and stop through the EC2 Query API."""
    NS = "{http://ec2.amazonaws.com/doc/2016-11-15/}"

    def __init__(self, region, key, secret):
        self.region, self.key, self.secret = region, key, secret

    def sign(self, host, body, t):
        return sigv4("ec2", self.region, self.key, self.secret, host, body, t)

    def call(self, action, instance_id):
        host = f"ec2.{self.region}.amazonaws.com"
        body = urllib.parse.urlencode({"Action": action, "Version": "2016-11-15", "InstanceId.1": instance_id})
        req = urllib.request.Request(f"https://{host}/", data=body.encode(), method="POST",
                                     headers=self.sign(host, body, time.gmtime()))
        with urllib.request.urlopen(req, timeout=10) as r:
            return ET.fromstring(r.read())

    def state(self, instance_id):
        name = self.call("DescribeInstances", instance_id).find(f".//{self.NS}instanceState/{self.NS}name")
        if name is None:
            raise ValueError(f"no instance {instance_id}")
        return name.text

    def start(self, instance_id):
        self.call("StartInstances", instance_id)

    def stop(self, instance_id):
        self.call("StopInstances", instance_id)


class Budgets:
    """One AWS budget's month through the Budgets JSON API, whose one endpoint signs as
    us-east-1."""
    HOST = "budgets.amazonaws.com"

    def __init__(self, key, secret, account_id, name):
        self.key, self.secret, self.account_id, self.name = key, secret, account_id, name

    def call(self, op):
        body = json.dumps({"AccountId": self.account_id, "BudgetName": self.name})
        headers = sigv4("budgets", "us-east-1", self.key, self.secret, self.HOST, body, time.gmtime(),
                        {"x-amz-target": f"AWSBudgetServiceGateway.{op}"}, "application/x-amz-json-1.1")
        req = urllib.request.Request(f"https://{self.HOST}/", data=body.encode(), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def read(self):
        return budget_reading(self.call("DescribeBudget")["Budget"],
                              self.call("DescribeNotificationsForBudget").get("Notifications", []),
                              self.call("DescribeBudgetActionsForBudget").get("Actions", []))


def budget_reading(budget, notifications, actions):
    """Dollars from the three Budgets answers: a threshold given as a percentage becomes
    dollars of the limit. forecast is None early in a month, before AWS has one."""
    limit = float(budget["BudgetLimit"]["Amount"])
    spend = budget.get("CalculatedSpend", {})

    def usd(value, kind):
        return float(value) if kind == "ABSOLUTE_VALUE" else limit * float(value) / 100

    forecast = spend.get("ForecastedSpend")
    return {
        "limit": limit,
        "actual": float(spend.get("ActualSpend", {}).get("Amount", 0)),
        "forecast": float(forecast["Amount"]) if forecast else None,
        "updated": epoch(budget.get("LastUpdatedTime", 0)),
        "alerts": sorted({(n["NotificationType"].lower(), usd(n["Threshold"], n.get("ThresholdType", "PERCENTAGE")))
                          for n in notifications}),
        "actions": sorted((a["ActionType"], a["Status"], usd(a["ActionThreshold"]["ActionThresholdValue"],
                                                             a["ActionThreshold"]["ActionThresholdType"]))
                          for a in actions),
    }


def epoch(t):
    """The API sends epoch seconds; an ISO string (the CLI's rendering) is read too."""
    return float(t) if isinstance(t, (int, float)) else datetime.fromisoformat(t).timestamp()


def read_budget(cfg, b, now):
    """Refresh b from the config's budget at most once per BUDGET_POLL_SECONDS. A failed read
    keeps the last numbers with ok False."""
    spec = cfg.get("budget")
    if not spec or (b.get("tried_at") is not None and now - b["tried_at"] < BUDGET_POLL_SECONDS):
        return
    b["tried_at"] = now
    if not (AWS_KEY and AWS_SECRET):
        b["ok"] = False
        return
    try:
        b.update(Budgets(AWS_KEY, AWS_SECRET, spec["account_id"], spec["name"]).read(), ok=True)
    except BUDGET_ERRORS as e:
        b["ok"] = False
        print(f"budget {spec['name']} unread, keeping the last numbers: {e}", flush=True)


def power_provider(power):
    """The client for a power block, or None and why there is none."""
    if power.get("provider") != "aws":
        return None, f"unknown power provider {power.get('provider')}"
    if not (AWS_KEY and AWS_SECRET):
        return None, "no AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (the secret's aws-* keys)"
    return Ec2(power["region"], AWS_KEY, AWS_SECRET), ""


def read_override(host, now):
    """(action, until) from the ci-runner-governor-override ConfigMap's key for host, mounted
    optional at OVERRIDE_DIR: {"action": "on"|"off", "until": <epoch>}. ("", 0) when there is
    none, it has run out, or it does not parse (logged), so a bad edit never powers anything."""
    try:
        with open(os.path.join(OVERRIDE_DIR, host)) as f:
            raw = f.read().strip()
    except OSError:
        return "", 0
    try:
        o = json.loads(raw) if raw else {}
        action, until = o.get("action", ""), float(o.get("until", 0))
    except (ValueError, TypeError, AttributeError) as e:
        print(f"{host}: override unreadable, ignored: {e}", flush=True)
        return "", 0
    return (action, until) if action in ("on", "off") and until > now else ("", 0)


def count_run_time(t, state, now):
    """Seconds the instance has run this UTC day, in t. A cycle counts at most two cycles'
    worth, so a pod restart or a blind spell never books hours nobody saw. In memory: a pod
    restart forgets the day, and the AWS budget is the cap that cannot forget."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if t.get("day") != day:
        t["day"], t["run_seconds"] = day, 0.0
    if state in ("pending", "running", "stopping") and "seen_at" in t:
        t["run_seconds"] += max(0.0, min(now - t["seen_at"], 2 * CYCLE_SECONDS, now % 86_400))
    t["seen_at"] = now
    t["hours_today"] = t["run_seconds"] / 3600


def power_readings(cfg, runners, tracked, now):
    """{host: tracked[host]} for each standby host with a power block: state, busy_at,
    labels, client, hours_today, override and override_until, plus what decide() keeps
    there across cycles (want_since, draining, next_cause). busy_at is the last cycle a slot
    on it was busy or the instance was not running. A host whose state cannot be read is
    left out, so nothing powers it."""
    out = {}
    for host, s in sorted(cfg["standby"].items()):
        if "power" not in s:
            continue
        t = tracked.setdefault(host, {})
        client, why = power_provider(s["power"])
        if not client:
            if t.get("off") != why:
                print(f"power hook off for {host}: {why}", flush=True)
            t["off"] = why
            continue
        t.pop("off", None)
        try:
            state = client.state(s["power"]["instance_id"])
            labels = host_labels(cfg, host)
        except POWER_ERRORS as e:
            print(f"{host}: instance state or runner labels unread, no power action: {e}", flush=True)
            continue
        if not labels and not t.get("unlabelled"):
            print(f"{host}: no runner labels in github_runner_state, so it is never started", flush=True)
        t["unlabelled"] = not labels
        if state != "running" or "busy_at" not in t or any(
                r["busy"] for r in runners.values() if r["host"] == host):
            t["busy_at"] = now
        count_run_time(t, state, now)
        t["override"], t["override_until"] = read_override(host, now)
        t.update(state=state, labels=labels, client=client)
        out[host] = t
    return out


def power_state(inst):
    """What the Infra page and the start/stop notification show for an instance."""
    if inst["state"] == "running" and inst.get("draining"):
        return "draining"
    return {"pending": "starting"}.get(inst["state"], inst["state"])


def live(cfg, host):
    return not DRY_RUN or cfg["standby"].get(host, {}).get("power", {}).get("live", False)


class State:
    lock = Lock()
    metrics = "ci_governor_up 0\n"


def render(cfg, hosts, runners, queue, decisions, ok, instances=None, budget=None):
    L = ["# HELP ci_governor_ok 1 when every source answered this cycle", "# TYPE ci_governor_ok gauge",
         f"ci_governor_ok {1 if ok else 0}", f"ci_governor_dry_run {1 if DRY_RUN else 0}",
         f"ci_governor_queue {queue}",
         "# HELP ci_governor_admitted Pool runners on a host that carry the pool label (may take jobs)",
         "# TYPE ci_governor_admitted gauge"]
    for host in sorted({*cfg["hosts"], *cfg["standby"]}):
        mine = [r for r in runners.values() if r["host"] == host]
        L.append(f'ci_governor_admitted{{host="{host}"}} {sum(1 for r in mine if not r["withheld"])}')
        L.append(f'ci_governor_registered{{host="{host}"}} {len(mine)}')
        L.append(f'ci_governor_live{{host="{host}"}} {1 if live(cfg, host) else 0}')
    # Between the withhold and admit lines the verdict is STEADY, so nothing else shows this.
    L += ["# HELP ci_governor_disk_held 1 when a host has withheld runners and too little disk to re-admit them",
          "# TYPE ci_governor_disk_held gauge"]
    for host in sorted(cfg["hosts"]):
        disk = hosts["disk"].get(host)
        held = (disk is not None and disk < cfg["hosts"][host]["admit"]["disk"]
                and any(r["withheld"] for r in runners.values() if r["host"] == host))
        L.append(f'ci_governor_disk_held{{host="{host}"}} {1 if held else 0}')
    # The reason is in the log, not a label: it changes every cycle.
    L += ["# HELP ci_governor_decision This cycle's verdict and action per host", "# TYPE ci_governor_decision gauge"]
    for host, action, _, v, _ in decisions:
        L.append(f'ci_governor_decision{{host="{host}",verdict="{v}",action="{action}"}} 1')
    # A powered host, for the agent-web Infra page and Sentinel's start/stop notice. reason is
    # why the governor last started or stopped it (queue, idle, cap, override), "none" before
    # it has, so the series changes only when the power does.
    L += ["# HELP ci_governor_power_state A powered standby host: stopped, starting, running, draining or stopping",
          "# TYPE ci_governor_power_state gauge",
          "# HELP ci_governor_power_hours_today Hours its instance has run this UTC day (in memory)",
          "# TYPE ci_governor_power_hours_today gauge",
          "# HELP ci_governor_power_override End (epoch) of the override in force, 0 with action none",
          "# TYPE ci_governor_power_override gauge"]
    for host, inst in sorted((instances or {}).items()):
        p = {**POWER_DEFAULTS, **cfg["standby"][host]["power"]}
        L.append(f'ci_governor_power_state{{host="{host}",state="{power_state(inst)}",'
                 f'reason="{inst.get("cause") or "none"}"}} 1')
        L.append(f'ci_governor_power_hours_today{{host="{host}"}} {inst.get("hours_today", 0):.3f}')
        L.append(f'ci_governor_power_max_hours_day{{host="{host}"}} {p["max_hours_day"]}')
        L.append(f'ci_governor_power_override{{host="{host}",action="{inst.get("override") or "none"}"}} '
                 f'{int(inst.get("override_until", 0))}')
        # Rule progress, each beside its line: idle against idle-off, jobs it could take past
        # the other hosts against the start excess, the start hold against its length.
        for name, value in (("idle_seconds", int(inst.get("idle_seconds", 0))),
                            ("idle_off_seconds", p["idle_off_seconds"]),
                            ("want", inst.get("want", 0)), ("start_min_excess", p["start_min_excess"]),
                            ("held_seconds", int(inst.get("held_seconds", 0))),
                            ("start_after_seconds", p["start_after_seconds"])):
            L.append(f'ci_governor_power_{name}{{host="{host}"}} {value}')
        if "usd_per_hour" in p:
            L.append(f'ci_governor_power_usd_per_hour{{host="{host}"}} {p["usd_per_hour"]}')
        L.append(f'ci_governor_power_last_change{{host="{host}",action="{inst.get("changed") or "none"}"}} '
                 f'{int(inst.get("changed_at", 0))}')
    if budget and cfg.get("budget"):
        L += render_budget(budget)
    return "\n".join(L) + "\n"


def render_budget(b):
    """The month as AWS Budgets last reported it. Alert lines are labelled by their dollars,
    which change only with the Terraform."""
    L = ["# HELP ci_governor_budget_ok 1 when the last AWS Budgets read worked", "# TYPE ci_governor_budget_ok gauge",
         f"ci_governor_budget_ok {1 if b.get('ok') else 0}"]
    if "limit" not in b:
        return L
    L += ["# HELP ci_governor_budget_usd This month's spend, forecast and limit in USD (AWS Budgets)",
          "# TYPE ci_governor_budget_usd gauge",
          f'ci_governor_budget_usd{{kind="actual"}} {b["actual"]:.2f}',
          f'ci_governor_budget_usd{{kind="limit"}} {b["limit"]:.2f}']
    if b["forecast"] is not None:
        L.append(f'ci_governor_budget_usd{{kind="forecast"}} {b["forecast"]:.2f}')
    L.append(f"ci_governor_budget_updated {int(b['updated'])}")
    for basis, usd in b["alerts"]:
        L.append(f'ci_governor_budget_alert{{basis="{basis}",usd="{usd:g}"}} 1')
    for kind, status, usd in b["actions"]:
        L.append(f'ci_governor_budget_action_usd{{type="{kind}",status="{status}"}} {usd:g}')
    return L


def cycle(cfg, last_action, tracked=None, budget=None):
    now = time.time()
    try:
        hosts, runners, waiting = observe(cfg["pool_label"])
    except API_ERRORS as e:
        print(f"blind cycle, deciding nothing: {e}", flush=True)
        with State.lock:
            State.metrics = "ci_governor_up 1\nci_governor_ok 0\n"
        return
    queue = len(waiting)
    instances = power_readings(cfg, runners, {} if tracked is None else tracked, now)
    decisions = decide(cfg, hosts, runners, waiting, last_action, now, instances)
    for host, action, runner, v, why in decisions:
        if action == "hold":
            continue
        power = action in ("start", "stop")
        target = cfg["standby"][host]["power"]["instance_id"] if power else runner
        dry = not live(cfg, host)
        print(f"{'would ' if dry else ''}{action} {target} on {host}: {v}, {why}; queue {queue}", flush=True)
        if dry:
            last_action[host] = now
            continue
        try:
            if power:
                getattr(instances[host]["client"], action)(target)
                instances[host].update(cause=instances[host].get("next_cause", ""), changed=action, changed_at=now)
            else:
                apply(cfg, action, runner)
            last_action[host] = now
        except POWER_ERRORS as e:
            print(f"{action} {target} failed: {e}", flush=True)
    if budget is not None:
        read_budget(cfg, budget, now)
    with State.lock:
        State.metrics = "ci_governor_up 1\n" + render(cfg, hosts, runners, queue, decisions, True, instances, budget)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b"ok\n"
        elif self.path == "/metrics":
            with State.lock:
                body = State.metrics.encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    cfg = load_config(CONFIG)
    if not DRY_RUN and not TOKEN:
        sys.exit("live mode needs GITHUB_TOKEN (Organization > Self-hosted runners: write)")
    Thread(target=HTTPServer(("", PORT), Handler).serve_forever, daemon=True).start()
    live_hosts = sorted(h for h in cfg["standby"] if DRY_RUN and live(cfg, h))
    print(f"ci-runner-governor: {'dry run' if DRY_RUN else 'live'}, hosts {', '.join(sorted(cfg['hosts']))}"
          + (f", live anyway: {', '.join(live_hosts)}" if live_hosts else ""), flush=True)
    last_action, tracked, budget = {}, {}, {}
    while True:
        cycle(cfg, last_action, tracked, budget)
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
