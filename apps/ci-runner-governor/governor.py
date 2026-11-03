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
standby admit would want it and its instance is stopped, the instance is started; its
slots count as room only once their runners report online, so a start, or a failed one,
is never room. Once no slot on it has been busy for idle_off_seconds, its slots are
withheld one per cycle and then the instance is stopped, so a slot never loses a job to
the stop and comes back withheld. A power block with "live": true acts on that host for
real (labels and power) even under DRY_RUN. Without AWS_ACCESS_KEY_ID and
AWS_SECRET_ACCESS_KEY the hook logs why once and does nothing.

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
IDLE_OFF_SECONDS = 1200
API_ERRORS = (urllib.error.URLError, OSError, ValueError, KeyError)
POWER_ERRORS = API_ERRORS + (ET.ParseError,)

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
    queue = sum(float(r["value"][1]) for r in prom(f'count(github_actions_job_waiting{{pool="{pool_label}",stuck!="true"}})'))
    return hosts, runners, int(queue)


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


def decide(cfg, hosts, runners, queue, last_action, now, instances=None):
    """[(host, action, runner, verdict, why)]: at most one action per host, withholds
    first so a re-admit elsewhere can use the room the pool keeps. instances is
    power_readings() for the standby hosts with a power block."""
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
        idle_off = cfg["standby"][host].get("power", {}).get("idle_off_seconds", IDLE_OFF_SECONDS)
        action, runner, why = "hold", "", "overflow"
        if inst and inst["state"] == "running" and now - inst["busy_at"] >= idle_off:
            # Before the job queue: a host idle this long cannot take what waits (a lan job).
            action, runner = ("withhold", on[0]) if on else ("stop", "")
            why = f"no slot busy for {int(now - inst['busy_at'])}s"
        elif len(on) > cap:
            action, runner, why = "withhold", on[0], f"over its {cap} cap"
        elif queue == 0 and on and not mine[on[0]]["busy"]:
            action, runner, why = "withhold", on[0], "nothing waits"
        elif queue > covered and off and len(on) < cap:
            if admitted < cfg["pool_max_admitted"]:
                action, runner, why = "admit", off[0], f"{queue} jobs wait, the other hosts cover {covered}"
                admitted += 1
                covered += 1
            else:
                why = f"pool at its {cfg['pool_max_admitted']} cap"
        elif inst and inst["state"] == "stopped" and queue > covered:
            # Not room yet: its slots count once their runners are online and admitted.
            if admitted < cfg["pool_max_admitted"]:
                action, why = "start", f"{queue} jobs wait, the other hosts cover {covered}"
            else:
                why = f"pool at its {cfg['pool_max_admitted']} cap"
        elif inst and inst["state"] != "running":
            why = f"instance {inst['state']}"
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


class Ec2:
    """One instance's state, start and stop through the EC2 Query API, signed with SigV4
    by hand: the pod is python:alpine with no boto3."""
    NS = "{http://ec2.amazonaws.com/doc/2016-11-15/}"

    def __init__(self, region, key, secret):
        self.region, self.key, self.secret = region, key, secret

    def sign(self, host, body, t):
        amz_date, day = time.strftime("%Y%m%dT%H%M%SZ", t), time.strftime("%Y%m%d", t)
        headers = {"content-type": "application/x-www-form-urlencoded; charset=utf-8",
                   "host": host, "x-amz-date": amz_date}
        signed = ";".join(sorted(headers))
        canonical = "\n".join(["POST", "/", "", *(f"{k}:{headers[k]}" for k in sorted(headers)), "",
                               signed, hashlib.sha256(body.encode()).hexdigest()])
        scope = f"{day}/{self.region}/ec2/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        key = ("AWS4" + self.secret).encode()
        for part in (day, self.region, "ec2", "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.key}/{scope}, "
                                    f"SignedHeaders={signed}, Signature={sig}")
        return headers

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


def power_provider(power):
    """The client for a power block, or None and why there is none."""
    if power.get("provider") != "aws":
        return None, f"unknown power provider {power.get('provider')}"
    if not (AWS_KEY and AWS_SECRET):
        return None, "no AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (the secret's aws-* keys)"
    return Ec2(power["region"], AWS_KEY, AWS_SECRET), ""


def power_readings(cfg, runners, tracked, now):
    """{host: {state, busy_at, client}} for each standby host with a power block. busy_at is
    the last cycle a slot on it was busy or the instance was not running, kept in tracked
    across cycles. A host whose state cannot be read is left out, so nothing powers it."""
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
        except POWER_ERRORS as e:
            print(f"{host}: instance state unread, no power action: {e}", flush=True)
            continue
        if state != "running" or "busy_at" not in t or any(
                r["busy"] for r in runners.values() if r["host"] == host):
            t["busy_at"] = now
        out[host] = {"state": state, "busy_at": t["busy_at"], "client": client}
    return out


def live(cfg, host):
    return not DRY_RUN or cfg["standby"].get(host, {}).get("power", {}).get("live", False)


class State:
    lock = Lock()
    metrics = "ci_governor_up 0\n"


def render(cfg, hosts, runners, queue, decisions, ok):
    L = ["# HELP ci_governor_ok 1 when every source answered this cycle", "# TYPE ci_governor_ok gauge",
         f"ci_governor_ok {1 if ok else 0}", f"ci_governor_dry_run {1 if DRY_RUN else 0}",
         f"ci_governor_queue {queue}",
         "# HELP ci_governor_admitted Pool runners on a host that carry the pool label (may take jobs)",
         "# TYPE ci_governor_admitted gauge"]
    for host in sorted({*cfg["hosts"], *cfg["standby"]}):
        mine = [r for r in runners.values() if r["host"] == host]
        L.append(f'ci_governor_admitted{{host="{host}"}} {sum(1 for r in mine if not r["withheld"])}')
        L.append(f'ci_governor_registered{{host="{host}"}} {len(mine)}')
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
    return "\n".join(L) + "\n"


def cycle(cfg, last_action, tracked=None):
    now = time.time()
    try:
        hosts, runners, queue = observe(cfg["pool_label"])
    except API_ERRORS as e:
        print(f"blind cycle, deciding nothing: {e}", flush=True)
        with State.lock:
            State.metrics = "ci_governor_up 1\nci_governor_ok 0\n"
        return
    instances = power_readings(cfg, runners, {} if tracked is None else tracked, now)
    decisions = decide(cfg, hosts, runners, queue, last_action, now, instances)
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
            else:
                apply(cfg, action, runner)
            last_action[host] = now
        except POWER_ERRORS as e:
            print(f"{action} {target} failed: {e}", flush=True)
    with State.lock:
        State.metrics = "ci_governor_up 1\n" + render(cfg, hosts, runners, queue, decisions, True)


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
    last_action, tracked = {}, {}
    while True:
        cycle(cfg, last_action, tracked)
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
