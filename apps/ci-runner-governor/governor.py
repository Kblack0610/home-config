#!/usr/bin/env python3
"""ci-runner-governor - how many Unity pool runners may take new jobs, per host.

WHY THIS EXISTS:

The Unity pool is a fixed set of registered runners (ansible/inventory.yml,
unity_ci_runners). How many of them a host can carry depends on what else it is
doing: razer also runs two platform slots, asus hosts Forgejo and the databases,
hp-victus reserves cores for the game server. A fixed count is either too high
on a busy night (asus at 94 C, razer stalling 28% of the time on CPU) or leaves
an idle host idle. Registering a runner is slow (ansible, a cold Library/ of
15-60 minutes), so capacity moves the other way: every slot stays registered and
warm, and the governor decides which of them are ADMITTED, meaning they carry
the `unity` label that gate jobs ask for.

A withheld runner has `unity` and `unity-full` swapped for `unity-withheld`; a host marked
"full": false gets only `unity` back, so admitting it never opens it to full runs.
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

A standby host (every cloud host with a Unity slot in the topology, apps/ci-topology) is
overflow: GitHub has no way to prefer a runner, so its
slots stay withheld until more jobs wait than the other hosts' idle slots and this
cycle's admits can take. Then one slot per cycle comes in, up to the host's
max_admitted, and goes back once no job it could take has waited for dwell_seconds (from
the governor's start, after a restart). Only a host with a "power" block keeps that clock, so
one without goes back the first cycle nothing waits. A job pinned to its host label needs no
pool label, so a withheld slot takes it.

With two or more powered burst hosts running and no job they can take waiting for
dwell_seconds, every live one but the busiest is PACKED in the same cycle: each one's slots
are withheld one per cycle and its idle rule stops it. Never the last unpacked one, never the only running host
offering a pack_keep_labels label (unity-wide), and never one a waiting job needs. It
unpacks at once when a waiting job has no unpacked taker or its jobs outnumber the unpacked
hosts' free slots, otherwise once its jobs have waited dwell_seconds. A new pack also waits
out dwell_seconds since the last pack or unpack.

A standby host with a "power" block (aws-spot-1) is also switched on and off. When a
standby admit would want it, its instance is stopped and a waiting job asks only for labels
its runners carry, the instance is started. Those labels are read from the exporter's
github_runner_state rows for the host, offline ones included, plus the pool labels a
withheld runner gets back on admit; with none read it is never started. So a job pinned
to the LAN (`lan`) does not start an off-LAN host that could never take it. A start costs
money, so it waits for start_min_excess jobs it can take past what the other hosts cover,
held for start_after_seconds (start_now_excess or more starts it at once); a short burst
never starts it. Its slots count as room only once their runners report online, so a start,
or a failed one, is never room. Once no slot on it has been busy for idle_off_seconds, or it
has run max_hours_day this UTC day, its slots are withheld one per cycle and the instance is
stopped once none is busy, so a slot never loses a job to the stop and comes back withheld.
The running burst host that was busy last, unpacked, is the warm one: it waits
warm_idle_off_seconds instead, so the next burst does not wait on a cold start (2026-11-14:
all four stopped by 08:59 and 14 gates came at 09:02).

A dedicated standby host (one with build_label slots: aws-build-1, the player-build
host) carries its own label (unity-build) instead of the pool's and is its
own exporter pool. It takes only the waiting jobs that ask for that label, which no other
host covers, so one waiting build starts it; it stays out of the pool's queue, floor,
max_admitted cap and the standby order. Its idle-off, daily cap, override and drain are
the same as any powered host's.

A paired host (aws-build-1: build slots with a slot group in inventory unity_ci_slot_groups,
read through the topology) runs both kinds at once. Its build slots carry the dedicated
label and the test slots of their groups the pool's, each
by its own static labels; each build slot shares its cores and memory with its test slots,
so a build and a test never run in one pair. A busy build slot keeps its pair, and each
waiting build claims a free pair whose build slot can take it, the most constrained job first
(a release build needs slot 1 or 2). In a claimed pair every admitted test slot steps aside
in one cycle, and the build slot is admitted once none is busy or admitted. In every other
pair the build slot is withheld, and once it is its test slots are the pool's, the last
standby pool host's, under its max_admitted and any pin. A stopped paired host starts on
its own thresholds for a waiting build and on the default pool ones for tests.
A group's lead is a build slot (unity-build) or a wide slot (config "wide_label", unity-wide:
Multi, Soak, Playloop); the topology says which, and lead_labels() is what it carries admitted
(a wide slot: unity-wide, unity and, on a full host, unity-full). A wide job claims a free
group with a wide lead, a build one with a build lead, the same way, so a wide slot is
admitted only while a wide job waits, and otherwise its test slots serve the pool.
A slot withheld while its pair uses the cores is exported as ci_governor_lent (to tests, or
to a build), so the dashboards can tell it from a slot held back for load.

The standby hosts go in start order, cheapest usd_per_hour first (config.json "rates"),
so Spot before on-demand. A host further down starts only for the jobs the hosts ahead of it cannot take: a stopped
one ahead counts its max_admitted, one booting or running counts the slots it has not
admitted yet plus its idle admitted ones, so a deep queue still starts both and a short one
starts only the first. A stopped host ahead drops out of that count for fallback_seconds
when EC2 refuses its start (InsufficientInstanceCapacity is "capacity", any other EC2 code
such as a Spot request state or a vCPU limit is "refused") or AWS stops it (a Spot reclaim,
or a start that found no capacity after all: a Server.* state reason, "reclaim"), so the
next host takes the queue at once. ci_governor_fallback_total{host,reason} counts each
pass-over once. Two failures are no fallback: the budget's deny policy (UnauthorizedOperation),
which every host shares, and a network error, which says nothing about the host. The ConfigMap
ci-runner-governor-override (the agent-web Infra page's button) beats all of that until its
end time: "on" starts it and keeps it from idle-off and the day cap, for at most
override_on_max_hours (config.json) from when the governor first sees it; "off" drains and
stops it and keeps it stopped. Only a pass-over beats "on": a passed-over host waits out its
fallback_seconds before an "on" starts it again. The month's cap is an AWS Budgets action, outside the governor.

A host's key in that ConfigMap can instead hold {"slots": N}, written by the workstation's
machine-mode command: how many of its slots the human wants admitted. It has no end time,
since it says what the machine is for and not how loaded it is. A release reservation is a
key of its own, "<host>.reserve" holding {"until": <epoch>} (machine-mode release-lane), so the
power override's until and agent-web's whole-key write never touch it: until then it pins
the host to 0, so gates go to other hosts while release.yml jobs, which ask for the host
label alone, take its withheld slots. Past its end it reads as no reservation. On a standby host a
pin is a lower cap (one slot withheld per cycle, a busy one after its job; no admit past it, an
"on" override's included), so a slot-restarting apply can drain it. A pinned host moves one
slot per cycle toward N, in either direction, past its cooldown and instead of its verdict,
and these moves happen even in a dry run, because nothing else re-admits a slot then. Only
the pool floor and cap hold, and a FULL host keeps its own verdict unless the pin withholds
(both want fewer slots, and only the pin's move applies in a dry run). In a dry run a pin is the
only thing that withholds a host's slot, so a host that is not live and that no pin holds
gets its withheld slots back one per cycle (RESTORE), past its cooldown and whatever its
verdict but FULL: an ended reservation or a dropped pin leaves nothing withheld.
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

A "workers" entry is a powered host with no runners at all: a cloud agent box that
fleet-dispatch places queue items on (fleet-worker-1). Its one input is the demand key
"<host>.demand" in the same ConfigMap, {"demand": N, "until": <epoch>}, which fleet-dispatch
writes on each pass: the items it has for the box, live sessions on it plus the ones waiting
while every home host is at its gate. A key of its own, so neither agent-web's whole-key
"on"/"off" write nor machine-mode's merge drops it. A stopped worker starts once demand
reaches start_min_excess; a running one stops once it has had no demand for
idle_off_seconds, or at max_hours_day; a demand key past its end reads as none, so a
dispatcher that stops writing lets the box go idle and stop. The override, the pass-over
after a refused start or a reclaim, run time and the power metrics work as for a standby
host; start_after_seconds does not apply, since each pass already waits for home's gate.

LENDING. The Forgejo runners and the personal-repo GitHub runners (.dotfiles-private,
gungan-speak) have no slots of their own: each is a seat that borrows one LAN Unity slot
(topology "borrows": its cores, slice and disk), and runs only while the governor lends it
that slot, so one authority admits every job a slot runs. A personal repo's runner cannot
join the org pool (GitHub lists a repo runner only to its repo), which is why it is a seat.
A seat's demand is its waiting jobs (github-runner-exporter: forge="forgejo", or the repo).
The kind whose job has waited longest goes first, and a slot is lent only while no GitHub
job it could take has waited longer, only on a live, unpinned host that is not HOT or FULL,
one per host per cycle past its cooldown, and never past the pool floor; on a ROOM host every
idle withheld slot is lent in the same cycle, cooldown or not, since it costs the pool nothing.
An idle slot goes first; with none, the busy one nearest its end is lent and drains into the
seat. A lend has three steps, each read back from GitHub before the next, so a slot never runs two jobs:
  lend    the slot's own labels go (withheld, as a withhold does) and it is marked lent
          (lent_label), so it takes no new GitHub job;
  grant   once it shows withheld, lent and idle: a Forgejo seat's lease turns to forgejo,
          a repo seat gets its caps back for its seat_withheld_label;
  return  when its kind has no job left for it, an older GitHub job wants the slot, the
          host turns unfit, or a Forgejo seat's supervisor stopped asking: the lease turns
          to github, or the repo seat is withheld again; the mark goes only once the seat
          has given it back (its supervisor acknowledged github, between jobs, or the repo
          seat shows withheld and idle), and the slot is the pool's again: admitted when its
          host is ROOM and the pool is under pool_max_admitted (readmit), withheld otherwise.
The lends live in memory. After a restart a slot that still carries the mark stays lent: granted
when its seat is busy (its Forgejo lease set back to forgejo) or admitted, held for its Forgejo
seat otherwise, and returning only when no seat borrows it. Every other lease starts at github.
A repo seat admitted with no lend is withheld. ci_governor_withheld{runner,reason} says why
each withheld slot is withheld.
A Forgejo seat's supervisor (ansible role forgejo-runner-linux) long-polls
/lease?slot=<slot>&have=<lease>&wait=<seconds> on the lease NodePort, which answers as soon as
the lease differs from have, and runs `forgejo-runner one-job` only on a forgejo lease.
A governor that is down lends nothing: Forgejo CI stops (fail closed), GitHub CI does not.

DRY_RUN=1 (the default) logs and exports every decision and changes nothing.
Live, it needs GITHUB_TOKEN with Organization > Self-hosted runners: write, and
Administration: write on each seat's personal repo.

The loop is a poll: neither host load nor the GitHub queue has an event this
can await (workflow_job webhooks could replace the queue half later). /lease is not:
it waits on the lease itself.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Condition, Lock, Thread

PROM = os.environ.get("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090").rstrip("/")
API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
PORT = int(os.environ.get("PORT", "9321"))
CONFIG = os.environ.get("GOVERNOR_CONFIG", "/app/config.json")
# Every CI host's site and slots with their caps and groups, rendered from inventory.
TOPOLOGY = os.environ.get("TOPOLOGY", "/topology/topology.json")
AWS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
CYCLE_SECONDS = 60
# The causes decide() and decide_workers() give a start (stops: idle, cap, reclaim, override).
START_CAUSES = ("queue", "demand", "override")
# A power block's settings when config.json leaves one out. A start costs money (about
# $1.85/h for on-demand aws-build-1), so only a deep queue held this long starts one, and a day of
# running time is capped; the month's cap is an AWS Budgets action (platform infra/aws-ci-burst).
POWER_DEFAULTS = {"idle_off_seconds": 900, "start_min_excess": 4, "start_after_seconds": 300, "max_hours_day": 12,
                  # A queue this much past the other hosts starts it at once, with no hold:
                  # the hold is for short bursts, and this many jobs is not one.
                  "start_now_excess": 8,
                  # How long a host whose start EC2 refused, or that AWS stopped, is passed
                  # over for the next host in the standby order.
                  "fallback_seconds": 3600}
# StartInstances error codes that mean "not here, not now"; any other EC2 code but the deny
# is a "refused" fallback.
CAPACITY_CODES = {"InsufficientInstanceCapacity", "InsufficientCapacity"}
# The budget's deny policy refuses every host alike, so passing one over helps nothing.
DENY_CODES = {"UnauthorizedOperation"}
# The state reason prefix of a stop AWS made (Spot reclaim, no capacity after a start, an
# internal or scheduled stop), as against Client.* for one the governor or a person asked for.
AWS_STOP_PREFIX = "Server."
FALLBACK_REASONS = ("capacity", "refused", "reclaim")
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


def load_config(path, topology_path=None):
    """config.json plus the slot layout from topology.json (apps/ci-topology, rendered from
    ansible/inventory.yml), in the shape decide() and the tests use:
    - "standby": every cloud host with a Unity slot, by host in start order (cheapest
      usd_per_hour first), each {max_admitted: its Unity slot count, power: its config.json
      "power" block}
    - "hosts": every LAN host with a Unity slot, config.json "hosts" thresholds on top of
      the defaults, "full" from the topology
    - on a host of either list with build_label slots: labels, withheld_label and "groups"
      (build slot -> the test slots sharing its cores, from the slot groups)
    - "workers" by host (none when left out)
    - "rates": config.json's, plus {0, owned} for every LAN topology host
    - "shared": by runner, the caps its slot takes besides the governed ones (a LAN Unity test
      slot's light and heavy), which a withhold drops and an admit gives back with the rest."""
    with open(path) as f:
        cfg = json.load(f)
    with open(topology_path or TOPOLOGY) as f:
        topo = json.load(f)["hosts"]
    cfg["workers"] = {s["host"]: {k: v for k, v in s.items() if k != "host"} for s in cfg.get("workers", [])}
    governed = {cfg["pool_label"], cfg["build_label"], cfg["wide_label"]}
    unity = {h: [s for s in e["slots"] if governed & set(s["caps"])] for h, e in topo.items()}
    unity = {h: slots for h, slots in unity.items() if slots}
    cfg["shared"] = {s["name"]: [c for c in s["caps"] if c not in governed]
                     for slots in unity.values() for s in slots if set(s["caps"]) - governed}
    lan = sorted(h for h, e in topo.items() if e["site"] != "cloud")
    cfg["rates"] = {**{h: {"usd_per_hour": 0, "basis": "owned"} for h in lan}, **cfg.get("rates", {})}
    cloud = [h for h in unity if topo[h]["site"] == "cloud"]
    unrated = [h for h in cloud if h not in cfg["rates"]]
    if unrated:
        raise ValueError(f"cloud CI hosts with no rates entry: {', '.join(sorted(unrated))}")
    def build_slots(host):
        """A host with build slots: their label, withheld label and slot groups, on LAN and cloud alike."""
        builds = [s for s in unity[host] if {cfg["build_label"], cfg["wide_label"]} & set(s["caps"])]
        if not builds:
            return {}
        groups = {slot_of(s["name"]): [slot_of(t) for t in s["group"]] for s in builds if s.get("group")}
        # What a lead slot carries once admitted: a build slot its one label, a wide slot the
        # label and the pool's (a wide job asks for unity too), unity-full on a full host.
        full = topo[host].get("full", True) is not False
        leads = {slot_of(s["name"]): [cfg["build_label"]] if cfg["build_label"] in s["caps"]
                 else [cfg["wide_label"], cfg["pool_label"]] + ([cfg["full_label"]] if full else []) for s in builds}
        return {"labels": sorted({cfg["build_label"], cfg["wide_label"]} & {c for s in builds for c in s["caps"]}),
                "leads": leads, "withheld_label": cfg["build_withheld_label"],
                **({"groups": groups} if groups else {})}

    cfg["standby"] = {}
    for host in sorted(cloud, key=lambda h: (cfg["rates"][h]["usd_per_hour"], h)):
        entry = {"max_admitted": len(unity[host])}
        if host in cfg.get("power", {}):
            entry["power"] = cfg["power"][host]
        cfg["standby"][host] = {**entry, **build_slots(host)}
    stray = set(cfg.get("power", {})) - set(cfg["standby"])
    if stray:
        raise ValueError(f"power blocks for no cloud Unity host: {', '.join(sorted(stray))}")
    local = sorted(h for h in unity if topo[h]["site"] != "cloud")
    stray = set(cfg["hosts"]) - set(local)
    if stray:
        raise ValueError(f"hosts entries for no LAN Unity host: {', '.join(sorted(stray))}")
    cfg["hosts"] = {h: {**cfg["hosts"].get(h, {}), "full": bool(topo[h].get("full", True)), **build_slots(h)}
                    for h in local}
    # A seat borrows one LAN Unity slot and runs only while the governor lends it (LENDING).
    cfg["seats"] = {s["name"]: {"host": h, "slot": s["borrows"], "forge": s.get("forge", "github"),
                                "repo": s.get("repo"), "caps": s["caps"]}
                    for h, e in topo.items() for s in e["slots"] if s.get("borrows")}
    slots = {s["name"] for h in local for s in unity[h]}
    stray = sorted(n for n, s in cfg["seats"].items() if s["slot"] not in slots)
    if stray:
        raise ValueError(f"seats that borrow no LAN Unity slot: {', '.join(stray)}")
    # A LAN build slot shares its tests' cores; ungrouped, serves_pool() would read every slot as dedicated.
    ungrouped = [h for h, e in cfg["hosts"].items() if e.get("labels") and not e.get("groups")]
    if ungrouped:
        raise ValueError(f"LAN hosts with a build slot in no slot group: {', '.join(sorted(ungrouped))}")
    both = cfg["standby"].keys() & cfg["workers"].keys()
    if both:
        raise ValueError(f"in both standby and workers: {', '.join(sorted(both))}")
    check_rates(cfg)
    return with_defaults(cfg)


# What a runner host costs an hour, one entry per host in "rates", and how that number was
# arrived at: "owned" (hardware already paid for, 0 until a power tariff is written here),
# "on-demand" (AWS's list price) or "spot-estimate" (Spot moves; the month is reconciled
# against the budget's actual). The power hook and the run cost view (hub-core, through the
# Infra series ci_governor_rate_usd_per_hour) both read it here; a host with no entry has
# no rate, which the view shows as "no rate", never as 0.
RATE_BASES = ("owned", "on-demand", "spot-estimate")


def check_rates(cfg):
    for host, r in cfg.get("rates", {}).items():
        if not isinstance(r.get("usd_per_hour"), (int, float)) or r.get("basis") not in RATE_BASES:
            raise ValueError(f"rates.{host}: needs a numeric usd_per_hour and a basis in {RATE_BASES}")
    for host, entry in {**cfg["standby"], **cfg["workers"]}.items():
        if "usd_per_hour" in entry.get("power", {}):
            raise ValueError(f"{host}: usd_per_hour belongs in rates, not in its power block")


def powered(cfg, host):
    """host's standby or workers entry, {} when it has neither."""
    return cfg["standby"].get(host) or cfg.get("workers", {}).get(host, {})


def power_of(cfg, host):
    """A powered host's power settings: POWER_DEFAULTS with its own on top."""
    return {**POWER_DEFAULTS, **powered(cfg, host).get("power", {})}


def with_defaults(cfg):
    """Each host's thresholds: the defaults with its own overrides on top."""
    for host, over in cfg["hosts"].items():
        merged = {k: dict(v) if isinstance(v, dict) else v for k, v in cfg["defaults"].items()}
        for k, v in over.items():
            merged[k] = {**merged.get(k, {}), **v} if isinstance(v, dict) else v
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


def pools(cfg):
    """The exporter pools the governor runs: the shared pool and each dedicated host's own."""
    return [cfg["pool_label"]] + sorted({cfg["wide_label"]} | {l for s in (*cfg["standby"].values(), *cfg["hosts"].values())
                                                                for l in s.get("labels", [])})


def entry_of(cfg, host):
    """A host's entry, standby or LAN."""
    return cfg["standby"].get(host) or cfg["hosts"].get(host) or {}


def dedicated(cfg, host):
    """A build host's labels (aws-build-1, ms-a2: ["unity-build"]), or None. Its build slots
    carry these instead of the pool's and take only the jobs that ask for one; an unpaired
    one stays out of the shared pool's queue, floor, cap and standby order."""
    return entry_of(cfg, host).get("labels")


def lead_labels(cfg, host, name):
    """The labels a group's lead slot carries while admitted, bar its shared caps: a build
    slot's unity-build, a wide slot's unity-wide, unity and unity-full on a full host."""
    return list(entry_of(cfg, host).get("leads", {}).get(slot_of(name)) or dedicated(cfg, host))


def build_marks(cfg):
    """Each build host's labels, lowercased: a job that asks for one is the build slots' alone.
    LAN hosts first, so their pairs claim a waiting build before a paid host starts for it."""
    return {h: frozenset(l.lower() for l in dedicated(cfg, h)) for h in (*sorted(cfg["hosts"]), *cfg["standby"])
            if dedicated(cfg, h)}


def pool_jobs(waiting, own):
    """The waiting jobs the shared pool serves: every one that asks for no dedicated host's label."""
    marks = frozenset().union(*own.values())
    return [need for need in waiting if not need & marks]


def pairs(cfg, host):
    """A paired host's build slots and the test slots that share each one's cores and memory
    (aws-build-1: {1: [4, 5], 2: [6, 7], 3: [8, 9]}), or {}: its entry's "groups", which
    load_config() reads from the topology's slot groups (inventory unity_ci_slot_groups)."""
    return {int(b): [int(t) for t in ts] for b, ts in (entry_of(cfg, host).get("groups") or {}).items()}


def slot_of(name):
    """A runner's slot number, the end of its name (aws-build-1-unity-pool-4: 4), or 0."""
    tail = name.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def serves_pool(cfg, host, name=""):
    """Whether a runner carries the pool's labels: every one off a dedicated host, and a
    paired host's test slots. With no name, whether the host's own kind is the pool's."""
    return not dedicated(cfg, host) or (bool(name) and bool(pairs(cfg, host)) and slot_of(name) not in pairs(cfg, host))


def lent(cfg, runners):
    """{runner: (host, "tests" or "build")}: the withheld slots of a paired host whose cores
    another slot of their pair is using (admitted, or busy finishing a job). A build slot is
    lent to tests while one of its test slots uses the cores; a test slot is lent to a build
    while its build slot does. It says where the cores are, so it wins over a pin or a drain
    that would also hold the slot. Read from this cycle's labels alone, so it holds across
    restarts. Any other withheld slot is held for load, a pin, a drain or no work."""
    out = {}
    for host in (*cfg["hosts"], *cfg["standby"]):
        P = pairs(cfg, host)
        if not P:
            continue
        mine = {slot_of(n): r for n, r in runners.items() if r["host"] == host}
        uses = lambda s, mine=mine: s in mine and (mine[s]["busy"] or not mine[s]["withheld"])
        held = lambda s, mine=mine: s in mine and mine[s]["withheld"]
        name = {slot_of(n): n for n, r in runners.items() if r["host"] == host}
        for b, tests in P.items():
            if held(b) and any(uses(t) for t in tests):
                out[name[b]] = (host, "tests")
            if uses(b):
                out |= {name[t]: (host, "build") for t in tests if held(t)}
    return out


def withheld_label(cfg, host, name=""):
    if serves_pool(cfg, host, name) and pairs(cfg, host):
        return cfg["withheld_label"]
    return entry_of(cfg, host).get("withheld_label", cfg["withheld_label"])


def observe(pool_labels):
    """One reading of every host and pool runner. Raises when any source fails, so a
    blind cycle decides nothing instead of reading as an idle machine."""
    pool_label = "|".join(pool_labels)
    hosts = {k: by_host(prom(q)) for k, q in QUERIES.items()}
    runners = {}
    for r in prom(f'github_runner_state{{state=~"running|idle"}} and on (name) github_runner_pool_slot{{pool=~"{pool_label}",state=~"running|idle"}}'):
        m = r["metric"]
        if m.get("scope") == "org":
            runners[m["name"]] = {"host": m["host"], "busy": m["state"] == "running",
                                  "elapsed": float(r["value"][1]), "withheld": False,
                                  "labels": label_set(m.get("labels", ""))}
    # The exporter drops these rows when it cannot list the org's runners, so none means
    # the list is gone, not that the pool is empty.
    if not runners:
        raise ValueError(f"no {pool_label} runners in the metrics")
    for r in prom("github_runner_withheld"):
        if r["metric"]["name"] in runners:
            runners[r["metric"]["name"]]["withheld"] = True
    waiting = [label_set(r["metric"].get("labels", ""))
               for r in prom(f'github_actions_job_waiting{{pool=~"{pool_label}",stuck!="true"}}')]
    return hosts, runners, waiting


def observe_shared(cfg):
    """One label set per job waiting on a shared cap (light, heavy) that a LAN Unity slot
    also takes. Kept out of observe()'s pool queue so the standby hosts never count them."""
    caps = sorted({c for shared in cfg.get("shared", {}).values() for c in shared})
    if not caps:
        return []
    # A personal repo's job waits for its own seat, which a lend serves, not an admit.
    repos = seat_repos(cfg)
    return [label_set(r["metric"].get("labels", ""))
            for r in prom(f'github_actions_job_waiting{{pool=~"{"|".join(caps)}",stuck!="true"}}')
            if r["metric"].get("repo") not in repos]


def shared_demand(cfg, host, runners, shared_waiting):
    """How many waiting jobs on a shared cap a slot of host takes: demand a withheld LAN
    Unity slot would serve once admitted, though no unity job waits."""
    caps = {c for n, r in runners.items() if r["host"] == host for c in cfg.get("shared", {}).get(n, [])}
    return sum(1 for need in shared_waiting if need & caps)


def label_set(joined):
    # GitHub matches labels without regard to case.
    return frozenset(l.lower() for l in joined.split(",") if l)


def admit_labels(cfg, host, name=""):
    """The labels a runner on host gets back on admit: its kind's, plus the shared caps its
    slot takes. "full": false in its hosts entry leaves out the full label: unity_ci_full
    false in inventory.yml, so no full run lands there."""
    shared = list(cfg.get("shared", {}).get(name, []))
    if not serves_pool(cfg, host, name):
        return lead_labels(cfg, host, name) + shared
    full = cfg["hosts"].get(host, {}).get("full", True)
    return [cfg["pool_label"]] + ([cfg["full_label"]] if full else []) + shared


def host_labels(cfg, host):
    """Every label a pool runner on host carries, in any state (a stopped instance's runners
    stay registered, offline), plus the pool labels a withheld one gets back on admit.
    Empty when no runner of it is listed."""
    have = set()
    for r in prom(f'github_runner_state{{host="{host}"}} and on (name) github_runner_pool_slot{{host="{host}",pool=~"{"|".join(pools(cfg))}"}}'):
        have |= label_set(r["metric"].get("labels", ""))
    back = admit_labels(cfg, host) + ([cfg["pool_label"], cfg["full_label"]] if pairs(cfg, host) else [])
    return frozenset(have | label_set(",".join(back))) if have else frozenset()


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
    short = [k for k, miss in (("idle", idle < a["idle"]), ("psi", psi > a["psi"]), ("mem", mem < a["mem"]),
                               ("tctl", tctl is not None and tctl >= a["tctl"]),
                               ("disk", disk is not None and disk < a["disk"]), ("disk reading", disk is None))
             if miss]
    if not short:
        return "ROOM", f"idle {idle:.2f}, cpu pressure {psi:.2f}"
    return "STEADY", f"idle {idle:.2f}, cpu pressure {psi:.2f}; short of admit on {'+'.join(short)}"


def decide(cfg, hosts, runners, waiting, last_action, now, instances=None, pins=None, restore=(), shared_waiting=()):
    """[(host, action, runner, verdict, why)]: at most one action per host, except that a
    paired host also withholds every slot stepping aside and admits each build slot whose
    pair is clear in the same cycle; withholds first so a re-admit elsewhere can use the room the pool keeps. waiting is one label
    set per waiting job, the pool's and the dedicated hosts'; instances is power_readings() for the standby hosts with a
    power block; pins is read_pins(); restore names the hosts whose verdicts are not
    applied (a dry run), which get back what a pin withheld once no pin holds them.
    shared_waiting is observe_shared(): a LAN host admits for the jobs on its shared caps
    too, not only the unity queue."""
    # Jobs that ask for a dedicated host's label are that host's alone; the rest are the pool's.
    own = build_marks(cfg)
    waiting_all, waiting = waiting, pool_jobs(waiting, own)
    queue = len(waiting)
    # A paired host's test slots are pool slots like any other.
    admitted = sum(1 for n, r in runners.items() if not r["withheld"] and serves_pool(cfg, r["host"], n))
    # A standby host can go to zero, so only the other hosts hold the pool's floor.
    floor_held = sum(1 for n, r in runners.items() if not r["withheld"] and r["host"] in cfg["hosts"]
                     and serves_pool(cfg, r["host"], n))
    # The waiting builds no pair has claimed yet: a LAN pair claims first, so a paid build
    # host starts only for the builds the LAN build slots cannot take.
    builds_left = [need for need in waiting_all if any(need & m for m in own.values())]
    plans, pair_moves = [], []
    for host in sorted(cfg["hosts"]):
        t = cfg["hosts"][host]
        mine = {n: r for n, r in runners.items() if r["host"] == host}
        v, why = verdict({k: hosts[k].get(host) for k in QUERIES}, t)
        cooling = v != "FULL" and now - last_action.get(host, 0) < cfg["cooldown_seconds"]
        on = sorted((n for n, r in mine.items() if not r["withheld"]),
                    # Idle first, then the busy one nearest its end (longest running).
                    key=lambda n: (mine[n]["busy"], -mine[n]["elapsed"], n))
        off = sorted(n for n, r in mine.items() if r["withheld"])
        if pairs(cfg, host):
            # A LAN build slot uses its test slots' cores, so it adds no load, but a HOT or
            # FULL host, or one pinned to 0 (a release reservation), claims no build: a
            # running one keeps its pair, an idle one steps aside, and a paid build host
            # takes the rest. A claim whose tests are still busy stays in the paid host's
            # queue too, so a build never waits on test jobs while aws-build-1 could take it.
            # The pool rules below move only the test slots the pairs leave free.
            claims = v not in ("HOT", "FULL") and (pins or {}).get(host) != 0
            mine_builds = [need for need in builds_left if need & own[host]] if claims else []
            builds_in, aside, on, off, stepped, taken = step_pairs(cfg, host, mine, mine_builds, on, off)
            for need, clear in taken:
                if clear:
                    builds_left.remove(need)
            admitted -= stepped
            floor_held -= stepped
            pair_moves += [(host, "withhold", n, v, w) for n, w in aside]
            pair_moves += [(host, "admit", n, v, w) for n, w in builds_in]
        action, runner = "hold", ""
        pin = (pins or {}).get(host)
        # A full host gets no admit from a pin, but a pin's withhold still applies: FULL asks
        # for the same, and on a host that is not live only the pin's move happens.
        if pin is not None and (v != "FULL" or len(on) > pin):
            v, why = "PINNED", f"pinned to {pin}, {len(on)} admitted"
            if len(on) > pin and floor_held > cfg["pool_min_admitted"]:
                action, runner = "withhold", on[0]
                admitted -= 1
                floor_held -= 1
            elif len(on) > pin:
                why += "; at the pool floor"
            elif len(on) < pin and off:
                action, runner = "admit", off[0]
        elif host in restore and off and v != "FULL":
            v, why = "RESTORE", f"dry run and no pin, {len(off)} withheld"
            action, runner = "admit", off[0]
        elif cooling:
            why = f"cooldown ({why})"
        elif ((v == "FULL" and on) or (v == "HOT" and len(on) > t["min_admitted"])) \
                and floor_held > cfg["pool_min_admitted"]:
            action, runner = "withhold", on[0]
            admitted -= 1
            floor_held -= 1
        elif v == "ROOM" and off and queue + shared_demand(cfg, host, runners, shared_waiting) > 0:
            action, runner = "admit", off[0]
        elif v in ("HOT", "FULL") and on:
            why += "; at the floor"
        plans.append((host, action, runner, v, why))
    out = [m for m in pair_moves if m[1] == "withhold"]
    for host, action, runner, v, why in sorted(plans, key=lambda p: p[1] != "withhold"):
        if action == "admit":
            if admitted >= cfg["pool_max_admitted"]:
                action, runner, why = "hold", "", why + f"; pool at its {cfg['pool_max_admitted']} cap"
            else:
                admitted += 1
        if action != "hold" or not any(m[0] == host for m in pair_moves):
            out.append((host, action, runner, v, why))
    # A build slot's admit is no pool slot, so the pool's cap does not hold it.
    out += [m for m in pair_moves if m[1] == "admit"]
    # Slots the other hosts already have for the waiting jobs. Admitted slots can sit idle
    # for minutes with jobs queued, so one idle slot must not hold the overflow back.
    # A LAN build slot takes no pool job, so it covers none.
    covered = sum(1 for h, action, r, *_ in out if action == "admit" and serves_pool(cfg, h, r))
    covered += sum(1 for n, r in runners.items() if not r["withheld"] and not r["busy"] and r["host"] in cfg["hosts"]
                   and serves_pool(cfg, r["host"], n))
    flips = pack_plan(cfg, runners, waiting, instances or {}, covered, pins or {}, now)
    # A packed host withholds a slot below, maybe after a host ahead of it admits: count it
    # now, as a home host's withhold is counted before any admit.
    admitted -= sum(1 for h, i in (instances or {}).items() if i.get("packed")
                    and any(r["host"] == h and not r["withheld"] for r in runners.values()))
    warm = warm_host(cfg, instances or {})
    # Jobs the standby hosts ahead in the list will take once they are up: a host further
    # down starts only for what is left past these: host -> jobs.
    ahead = {}
    for host in cfg["standby"]:
        mine = {n: r for n, r in runners.items() if r["host"] == host}
        on = sorted((n for n, r in mine.items() if not r["withheld"]),
                    key=lambda n: (mine[n]["busy"], -mine[n]["elapsed"], n))
        off = sorted(n for n, r in mine.items() if r["withheld"])
        # A pin is a lower cap: the cap branch below withholds one slot per cycle (a busy one
        # finishes its job) and no admit passes it.
        cap, pinned = standby_cap(cfg, host, pins)
        inst = (instances or {}).get(host)
        p = power_of(cfg, host)
        action, runner, why = "hold", "", "overflow"
        over = inst.get("override") if inst else None
        drain, cause, wanting, want = "", "", False, 0
        # A dedicated host answers only its own jobs: no other host covers them, none is
        # ahead of it, and the shared pool's cap does not count it.
        ded = host in own
        # Every slot not withheld, whatever kind it serves: what a drain takes back.
        up = on
        aside, builds_in, stepped = [], [], 0
        if pairs(cfg, host):
            # Both kinds at once, a pair at a time: the pairs move by pair_plan() (every
            # step-aside in one cycle), and the pool's rules below move its test slots.
            builds = [need for need in builds_left if need & own[host]]
            builds_in, aside, on, off, stepped, _ = step_pairs(cfg, host, mine, builds, on, off)
            admitted -= stepped
            # Stopped, it starts for a waiting build on its own thresholds and for tests on
            # the pool's (a burst host's), since one waiting build is worth a start and one
            # test is not.
            ded = bool(builds) and bool(inst) and inst["state"] == "stopped"
            if not ded:
                p = {**p, "start_min_excess": POWER_DEFAULTS["start_min_excess"],
                     "start_after_seconds": POWER_DEFAULTS["start_after_seconds"]}
        jobs = [need for need in builds_left if need & own[host]] if ded else waiting
        q, cov = (len(jobs), 0) if ded else (queue, covered)
        packed = bool(inst and inst.get("packed"))
        takes = sum(1 for need in jobs if need <= inst["labels"]) if inst else 0
        # When a job it could take last waited (pack_plan()'s test); first seen, now, so a
        # restart starts a full dwell.
        if inst is not None and (takes or "waited_at" not in inst):
            inst["waited_at"] = now
        # Only a stopped host is passed over: AWS restarts a persistent Spot one by itself
        # when capacity returns, and then it counts like any running host.
        passed = bool(inst) and inst["state"] == "stopped" and inst.get("fallback_until", 0) > now
        if inst is not None:
            inst["idle_off_seconds"] = (max(p["idle_off_seconds"], cfg.get("warm_idle_off_seconds", 0))
                                        if host == warm else p["idle_off_seconds"])
        if inst and inst["state"] == "running":
            # Before the job queue: a host told off, past its day, or idle this long cannot
            # take what waits (an idle one: a lan job).
            if over == "off":
                drain, cause = "override off", "override"
            elif over != "on" and inst.get("hours_today", 0) >= p["max_hours_day"]:
                drain, cause = f"ran {inst['hours_today']:.1f}h today, its {p['max_hours_day']}h cap", "cap"
            elif over != "on" and now - inst["busy_at"] >= inst["idle_off_seconds"]:
                drain, cause = f"no slot busy for {int(now - inst['busy_at'])}s", "idle"
        if drain:
            # One slot per cycle, busy ones too (a withheld slot finishes its job), and the
            # stop only once no slot is busy, so a stop never kills a job.
            busy = sum(1 for r in mine.values() if r["busy"])
            if up:
                action, runner, why = "withhold", up[0], drain
            elif busy:
                why = f"{drain}; {busy} jobs finishing"
            else:
                action, why = "stop", drain
        elif packed:
            # Its slots go one per cycle, busy ones too (a withheld slot finishes its job), and
            # none comes back, so new jobs go to the other hosts and the idle rule stops it.
            if up:
                action, runner, why = "withhold", up[0], "packed"
            else:
                why = f"packed, {sum(1 for r in mine.values() if r['busy'])} jobs finishing"
        elif len(on) > cap:
            action, runner, why = "withhold", on[0], f"pinned to {cap}" if pinned else f"over its {cap} cap"
        elif over == "on" and inst and inst["state"] == "running" and off and len(on) < cap:
            if ded or admitted < cfg["pool_max_admitted"]:
                action, runner, why = "admit", off[0], "override on"
                if not ded:
                    admitted += 1
                    covered += 1
            else:
                why = f"override on; pool at its {cfg['pool_max_admitted']} cap"
        elif q == 0 and on and not mine[on[0]]["busy"] and over != "on":
            # Only once no job it could take has waited for dwell_seconds: the queue reads 0
            # for a minute between bursts, and a slot withheld then comes back minutes later.
            # An admit needs jobs waiting past what the other hosts cover, so it is never early.
            quiet = now - inst["waited_at"] if inst is not None else cfg["dwell_seconds"]
            if quiet >= cfg["dwell_seconds"]:
                action, runner, why = "withhold", on[0], "nothing waits"
            else:
                why = f"nothing waits for {int(quiet)}s of {cfg['dwell_seconds']}s"
        elif q > cov and off and len(on) < cap:
            if ded or admitted < cfg["pool_max_admitted"]:
                action, runner, why = "admit", off[0], f"{q} jobs wait, the other hosts cover {cov}"
                if not ded:
                    admitted += 1
                    covered += 1
            else:
                why = f"pool at its {cfg['pool_max_admitted']} cap"
        elif inst and inst["state"] == "stopped" and over == "off":
            why = "override off"
        elif inst and inst["state"] == "stopped" and passed:
            # Before an override on: EC2 just refused it or AWS just stopped it, so a start
            # every cycle would only fail or be reclaimed again.
            why = (f"passed over after a {inst['fallback_why']} for "
                   f"{int(inst['fallback_until'] - now)}s more")
        elif inst and inst["state"] == "stopped" and over == "on":
            action, why, cause = "start", "override on", "override"
        elif inst and inst["state"] == "stopped" and q > cov:
            # Not room yet: its slots count once their runners are online and admitted. A
            # start costs money, so only a queue this much deeper than the other hosts can
            # take, for start_after_seconds in a row, starts it: a short burst waits instead.
            # The standby hosts ahead in the list take theirs first.
            coming = 0 if ded else sum(ahead.values())
            want = max(0, min(takes, q - cov - coming))
            if not inst["labels"]:
                why = "no runner labels read"
            elif not takes:
                why = f"{q} jobs wait, none it can take"
            elif inst.get("hours_today", 0) >= p["max_hours_day"]:
                why = f"ran {inst['hours_today']:.1f}h today, its {p['max_hours_day']}h cap"
            elif want < p["start_min_excess"]:
                why = f"{want} jobs it can take past the other hosts' {cov}, starts at {p['start_min_excess']}"
                if coming:
                    why += f"; {', '.join(ahead)} ahead of it take {coming}"
            elif not ded and admitted >= cfg["pool_max_admitted"]:
                why = f"pool at its {cfg['pool_max_admitted']} cap"
            else:
                wanting = True
                since = inst["want_since"] = inst.get("want_since") or now
                why = f"{q} jobs wait, {takes} it can take, the other hosts cover {cov}"
                if now - since >= p["start_after_seconds"] or want >= p["start_now_excess"]:
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
            # What it will take, for the hosts behind it: nothing while draining, packed,
            # passed over, told off, past its day or unable to take what waits.
            if not (ded or drain or packed or passed or over == "off" or not takes):
                if inst["state"] == "stopped" and inst.get("hours_today", 0) < p["max_hours_day"]:
                    ahead[host] = cap
                elif inst["state"] in ("pending", "running"):
                    ahead[host] = (max(0, cap - len(on) - (action == "admit"))
                                   + sum(1 for n in on if not mine[n]["busy"]))
        v = "PINNED" if pinned else "PACKED" if packed else "STANDBY"
        if host in flips:
            out.append((host, flips[host][0], "", v, flips[host][1]))
        if drain:
            aside, builds_in, admitted = [], [], admitted + stepped
        # A pair's moves all go in this cycle, step-asides first; a hold beside them says nothing.
        out += [(host, "withhold", n, v, w) for n, w in aside]
        if action != "hold" or not (aside or builds_in):
            out.append((host, action, runner, v, why))
        out += [(host, "admit", n, v, w) for n, w in builds_in]
    return out


def standby_cap(cfg, host, pins):
    """(cap, pinned): how many of a standby host's pool slots may be admitted. max_admitted
    counts every slot; on a paired host the pool's rules (and a pin) move only the test slots."""
    cap = cfg["standby"][host]["max_admitted"] - len(pairs(cfg, host))
    pin = (pins or {}).get(host)
    pinned = pin is not None and pin < cap
    return (min(cap, pin) if pinned else cap), pinned


def warm_host(cfg, instances):
    """The running, unpacked burst host whose slots were busy last (the standby order breaks
    a tie), or None: the one host the idle rule keeps for warm_idle_off_seconds."""
    order = list(cfg["standby"])
    up = [h for h in order if h in instances and not dedicated(cfg, h) and not pairs(cfg, h)
          and instances[h]["state"] == "running" and not instances[h].get("packed")]
    return max(up, key=lambda h: (instances[h].get("busy_at", 0), -order.index(h)), default=None)


def pack_plan(cfg, runners, waiting, instances, covered, pins, now):
    """{host: ("pack" or "unpack", why)} this cycle, keeping "packed" and "pack_at" in each
    burst host's instances entry (a powered standby host serving the pool, not paired).

    A running burst host bills by the hour however few of its slots are busy, and a job lands
    on any admitted slot, so with a trickle every host stays half busy and none goes idle. So
    once no job a running burst host could take has waited for dwell_seconds, every such live one
    (a dry-run host would never stop) but the busiest is packed in the same cycle, fewest busy
    slots first (a tie: the one started last): decide() withholds its slots and admits none, and
    its idle rule stops it once its jobs end. Never the last unpacked running one, and never the only host
    whose runners carry a label in pack_keep_labels (a wide job could arrive at any time). A
    packed host is no taker and no free room in another's tests. It is unpacked by an override,
    by becoming that only host, or once a waiting job is one it could take, and is then back in
    the admit order; packed hosts are weighed busiest first, each unpack counted before the next.
    A pack or unpack holds for dwell_seconds, so a queue that flickers between empty and not
    moves nothing, except that a packed host is unpacked at once when a waiting job has no
    unpacked host that could take it or more of its jobs wait than the unpacked hosts have free
    slots (covered: the home hosts' idle slots and this cycle's admits)."""
    dwell, keep = cfg["dwell_seconds"], frozenset(l.lower() for l in cfg["pack_keep_labels"])
    burst = {h: i for h, i in instances.items() if h in cfg["standby"] and not dedicated(cfg, h) and not pairs(cfg, h)}
    for i in burst.values():
        if i["state"] != "running":
            i["packed"] = False
    up = {h: i for h, i in burst.items() if i["state"] == "running" and not i.get("draining")}
    mine = {h: [r for r in runners.values() if r["host"] == h] for h in [*cfg["hosts"], *up]}
    busy = {h: sum(1 for r in rs if r["busy"]) for h, rs in mine.items()}
    # What each other host could take: a home host's online runners' labels, a burst host's
    # labels with the pool's put back (host_labels()).
    offers = {h: frozenset().union(*(r.get("labels", ()) for r in mine[h])) for h in cfg["hosts"]}
    offers |= {h: i["labels"] for h, i in up.items()}

    def room(h):
        on = [r for r in mine[h] if not r["withheld"]]
        cap = min(standby_cap(cfg, h, pins)[0], len(mine[h]))
        return max(0, cap - len(on)) + sum(1 for r in on if not r["busy"])

    serving = {h for h, i in up.items() if not i.get("packed")}

    def others(h):
        return [o for g, o in offers.items() if g != h and (g in cfg["hosts"] or g in serving)]

    def scarce(h):
        return sorted(keep & up[h]["labels"] - frozenset().union(*others(h)))

    flips = {}
    for h in sorted(up.keys() - serving, key=lambda h: (-busy[h], h)):
        i = up[h]
        jobs = [need for need in waiting if need <= i["labels"]]
        free = covered + sum(room(g) for g in serving)
        alone = [need for need in jobs if not any(need <= o for o in others(h))]
        if i.get("override"):
            why = f"override {i['override']}"
        elif scarce(h):
            why = f"the only host with {', '.join(scarce(h))}"
        elif alone:
            why = f"{len(alone)} waiting jobs no other host can take"
        elif len(jobs) > free:
            why = f"{len(jobs)} jobs it can take wait, the other hosts have {free} free slots"
        elif jobs and now - i.get("pack_at", 0) >= dwell:
            why = f"{len(jobs)} jobs it can take wait"
        else:
            continue
        i["packed"], i["pack_at"] = False, now
        serving.add(h)
        flips[h] = ("unpack", why)
    last = max((i.get("pack_at", 0) for i in burst.values()), default=0)
    if now - last < dwell:
        return flips
    # Quiet for a dwell (waited_at, kept by decide()), not just this cycle: the queue reads 0
    # for a minute between bursts, and a batch pack on that would drain the pool.
    fit = [h for h in serving if live(cfg, h) and not up[h].get("override")
           and now - up[h].get("waited_at", 0) >= dwell
           and not any(need <= up[h]["labels"] for need in waiting)]
    for h in sorted(fit, key=lambda h: (busy[h], -up[h].get("launched", 0), h)):
        if len(serving) < 2 or scarce(h):
            continue
        serving.discard(h)
        up[h]["packed"], up[h]["pack_at"] = True, now
        flips[h] = ("pack", f"no job it can take waited for {dwell}s, {busy[h]} busy slots, "
                            f"{len(serving)} burst hosts left unpacked; its slots go and its idle rule stops it")
    return flips


def step_pairs(cfg, host, mine, builds, on, off):
    """pair_plan() applied to a host's on and off lists: (build slots to admit, slots that step
    aside, the pool slots still admitted, the withheld slots the pool may admit, how many pool
    slots step aside, the claims)."""
    builds_in, aside, eligible, taken = pair_plan(cfg, host, mine, builds)
    stepping = {n for n, _ in aside}
    on = [n for n in on if serves_pool(cfg, host, n) and n not in stepping]
    off = [n for n in off if n in eligible]
    return builds_in, aside, on, off, sum(1 for n in stepping if serves_pool(cfg, host, n)), taken


def pair_plan(cfg, host, mine, builds):
    """A paired host's moves this cycle: (build slots to admit, slots that step aside, each
    with its reason; the test slots the pool may admit; (build, its tests are clear) for each
    waiting build it claimed). mine
    is its online runners, builds the waiting jobs that ask for its label. A runner not
    registered yet is quiet and takes no job."""
    P = pairs(cfg, host)
    by = {slot_of(n): n for n in mine}
    busy = lambda s: s in by and mine[by[s]]["busy"]
    up = lambda s: s in by and not mine[by[s]]["withheld"]
    quiet = lambda s: not busy(s) and not up(s)
    held = {withheld_label(cfg, host).lower(), cfg["withheld_label"].lower()}
    # A withheld lead slot can take what its labels ask once admitted.
    can = lambda b, need: b in by and need <= (frozenset(mine[by[b]].get("labels", ())) - held) | frozenset(
        l.lower() for l in lead_labels(cfg, host, by[b]))
    # A busy build slot keeps its pair. Each waiting build claims a free pair whose slot can
    # take it, the most constrained first; an admitted build slot, then one whose test slots
    # already stepped aside, then the fewest busy tests, so a claim holds across cycles.
    claimed, wanted, taken = {b for b in P if busy(b)}, set(), []
    for need in sorted(builds, key=lambda need: sum(can(b, need) for b in P)):
        free = [b for b in P if b not in claimed and can(b, need)]
        if free:
            b = min(free, key=lambda b: (not up(b), any(up(t) for t in P[b]), sum(busy(t) for t in P[b]), b))
            claimed.add(b)
            wanted.add(b)
            taken.append((need, not any(busy(t) for t in P[b])))
    admit, aside, eligible = [], [], set()
    for b, tests in sorted(P.items()):
        if b in claimed:
            reason = f"a build {'runs' if busy(b) else 'waits'}, pair {b}'s test slots step aside"
            aside += [(by[t], reason) for t in tests if up(t)]
            if b in wanted and not up(b) and all(quiet(t) for t in tests):
                admit.append((by[b], f"a build waits and pair {b}'s test slots are clear"))
            elif up(b) and not busy(b) and not all(quiet(t) for t in tests):
                aside.append((by[b], f"pair {b}'s test slots are not clear yet"))
        elif up(b) and not busy(b):
            aside.append((by[b], f"no build waits for pair {b}, its test slots serve the pool"))
        elif quiet(b):
            eligible |= {by[t] for t in tests if t in by}
    paired = set(P).union(*P.values())
    eligible |= {n for n in mine if slot_of(n) not in paired and serves_pool(cfg, host, n)}
    return admit, aside, eligible, taken


def gh(method, path, body=None):
    req = urllib.request.Request(f"{API}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r) if r.status != 204 else None


def find_runner(scope, name):
    """(id, labels, busy) of the runner named name under scope ("orgs/<org>" or
    "repos/<owner>/<repo>"), read live from GitHub."""
    page = 1
    while True:
        data = gh("GET", f"{scope}/actions/runners?per_page=100&page={page}")
        for r in data.get("runners", []):
            if r["name"] == name:
                return r["id"], {l["name"] for l in r["labels"]}, bool(r.get("busy"))
        if not data.get("runners"):
            raise KeyError(f"no runner {name} in {scope}")
        page += 1


def runner_id(org, name):
    rid, labels, _ = find_runner(f"orgs/{org}", name)
    return rid, labels


def relabel(scope, rid, have, add, drop):
    """Add before drop, so a runner is never left with neither."""
    if set(add) - have:
        gh("POST", f"{scope}/actions/runners/{rid}/labels", {"labels": sorted(set(add) - have)})
    for label in drop:
        if label in have:
            gh("DELETE", f"{scope}/actions/runners/{rid}/labels/{urllib.parse.quote(label)}")


def apply(cfg, action, name, host):
    """Swap the runner's own labels (the pool's, or a build slot's dedicated ones, and the
    shared caps its slot takes) for its withheld one, or back. Adds before it removes, so a
    runner is never left with neither. A withheld slot takes no job of any kind."""
    org, held = cfg["org"], withheld_label(cfg, host, name)
    rid, have = runner_id(org, name)
    if action == "withhold":
        add, drop = [held], own_labels(cfg, host, name)
    else:
        add, drop = admit_labels(cfg, host, name), [held]
    relabel(f"orgs/{org}", rid, have, add, drop)


def own_labels(cfg, host, name):
    """Every label a withhold drops from a slot: its kind's and its shared caps."""
    return ([cfg["pool_label"], cfg["full_label"]] if serves_pool(cfg, host, name) else lead_labels(cfg, host, name)) \
        + list(cfg.get("shared", {}).get(name, []))


# LENDING (the module docstring says why). A seat borrows one Unity slot (topology "borrows")
# and runs only while that slot is lent to it.

# The longest a /lease long-poll is held: a supervisor asks again after it.
LEASE_WAIT_MAX = 300
# A granted Forgejo lend whose supervisor has not asked for its lease this long, and whose
# seat is not busy, goes back, and a returning one is unlent without its ack: a dead
# supervisor must not keep a slot from GitHub.
LEASE_STALE_SECONDS = 600
LEASES = ("github", "forgejo")


class Leases:
    """Each Forgejo seat's lease, by the slot it borrows: "github" (the default, so a restart
    starts there) or "forgejo". The slot's supervisor long-polls wait(); set() wakes it."""

    def __init__(self, slots):
        self.cond = Condition()
        self.lease = dict.fromkeys(slots, "github")
        # The lease the supervisor last said it holds since the lease last changed; None
        # until it says. "github" means no Forgejo job of its runs.
        self.ack = dict.fromkeys(slots)
        # When the supervisor last asked.
        self.seen = dict.fromkeys(slots, 0.0)

    def set(self, slot, lease):
        with self.cond:
            if self.lease[slot] != lease:
                self.lease[slot], self.ack[slot] = lease, None
                self.cond.notify_all()

    def get(self, slot):
        with self.cond:
            return self.lease[slot], self.ack[slot], self.seen[slot]

    def wait(self, slot, have, timeout):
        """The slot's lease as soon as it differs from have, else have after timeout. A
        supervisor asks with the lease it holds and acts on what it gets back, so asking
        with the current lease acknowledges it."""
        with self.cond:
            self.seen[slot] = time.time()
            if self.lease[slot] == have:
                self.ack[slot] = have
            self.cond.wait_for(lambda: self.lease[slot] != have, timeout)
            if self.lease[slot] == have:
                self.ack[slot] = have
            self.seen[slot] = time.time()
            return self.lease[slot]


def observe_seats(cfg):
    """({seat: {busy, online, withheld}}, [(kind, labels, age)]) from github-runner-exporter.
    A seat with no runner registered has no entry. kind is "forgejo", a personal repo (its
    seats' repo) or "github": any other job waiting on a cap a Unity slot takes."""
    seats = cfg.get("seats", {})
    if not seats:
        return {}, []
    names = "|".join(sorted(seats))
    state = {}
    for r in prom(f'github_runner_state{{name=~"{names}"}}'):
        m = r["metric"]
        if m.get("state") != "missing":
            state[m["name"]] = {"busy": m.get("state") == "running", "online": m.get("state") in ("running", "idle"),
                                "withheld": False}
    for r in prom(f'github_runner_withheld{{name=~"{names}"}}'):
        if r["metric"]["name"] in state:
            state[r["metric"]["name"]]["withheld"] = True
    repos = seat_repos(cfg)
    rivals = {cfg["pool_label"], *(c for caps in cfg.get("shared", {}).values() for c in caps)}
    waits = []
    for r in prom('github_actions_job_waiting{stuck!="true"}'):
        m, age = r["metric"], float(r["value"][1])
        need = label_set(m.get("labels", ""))
        if m.get("forge") == "forgejo":
            waits.append(("forgejo", need, age))
        elif m.get("repo") in repos:
            waits.append((repos[m["repo"]], need, age))
        elif m.get("pool") in rivals:
            waits.append(("github", need, age))
    return state, waits


def seat_repos(cfg):
    """{short name: owner/repo} of the personal repos whose runners are seats. The exporter
    labels a wait with the short name only."""
    return {s["repo"].split("/", 1)[1]: s["repo"] for s in cfg.get("seats", {}).values() if s.get("repo")}


def seat_kind(cfg, seat):
    s = cfg["seats"][seat]
    return "forgejo" if s["forge"] == "forgejo" else s["repo"]


def slot_takes(cfg, runner, name, need):
    """Whether a Unity slot, admitted, could take a job that asks for need."""
    have = {l for l in runner.get("labels", ()) if not l.endswith("-withheld") and l != cfg["lent_label"]}
    return need <= have | label_set(",".join(admit_labels(cfg, runner["host"], name)))


def lend_plan(cfg, hosts, runners, seat_state, waits, lends, leases, pins, last_action, now):
    """This cycle's steps toward lending the Unity slots their seats borrow: [(step, slot,
    seat, why)], step one of
      lend      withhold the slot and mark it lent, so it takes no GitHub job from now on
      grant     once it shows withheld, lent and idle: the seat's lease, or its labels back
      return    the seat's lease or labels back from it
      unlend    once every seat of the slot gave it back: drop the mark (the slot stays withheld)
      readmit   an unlend on a ROOM host under the pool cap: drop the mark and admit the slot
      withhold  a repo seat admitted with no slot lent to it (slot "")
    lends: {slot: {"seat": seat, or None for a lend found by its label after a restart,
    "phase": "held", "granted" or "returning", "at": when}}; pins: read_pins(). It runs before decide(),
    which leaves a host a lend moved to its cooldown and never sees a lent slot."""
    seats, mark = cfg.get("seats", {}), cfg["lent_label"]
    if not seats:
        return []
    by_slot = {}
    for seat, s in sorted(seats.items()):
        by_slot.setdefault(s["slot"], []).append(seat)
    host_of = {slot: seats[mine[0]]["host"] for slot, mine in by_slot.items()}
    st = lambda seat: seat_state.get(seat, {})
    verdicts = {h: verdict({k: hosts[k].get(h) for k in QUERIES}, cfg["hosts"][h])[0] for h in cfg["hosts"]}

    def unfit(host):
        """Why host may not hold a lend, or ""."""
        if host not in cfg["hosts"] or not live(cfg, host):
            return f"{host} is not acted on live"
        if host in (pins or {}):
            return f"{host} is pinned"
        return f"{host} is {verdicts[host]}" if verdicts[host] in ("HOT", "FULL") else ""

    def given_back(slot, seat, at):
        if seats[seat]["forge"] == "forgejo":
            lease, ack, seen = leases.get(slot)
            if st(seat).get("busy"):
                return False  # an ack while its job runs is not the seat's
            # A supervisor that died before it acked never will.
            return (lease == "github" and ack == "github") or now - max(seen, at) >= LEASE_STALE_SECONDS
        return not st(seat) or (st(seat)["withheld"] and not st(seat)["busy"])

    jobs = {}
    for kind, need, age in waits:
        jobs.setdefault(kind, []).append((age, need))
    oldest = {k: max(a for a, _ in v) for k, v in jobs.items() if k != "github"}

    def rival(slot):
        """Age of the oldest GitHub job this slot could take, or -1."""
        r = runners.get(slot)
        return max((a for a, need in jobs.get("github", []) if r and slot_takes(cfg, r, slot, need)), default=-1)

    have, want = {}, {k: len(v) for k, v in jobs.items() if k != "github"}
    for slot, lend in sorted(lends.items()):
        if lend["seat"] and lend["phase"] != "returning":
            kind = seat_kind(cfg, lend["seat"])
            have.setdefault(kind, []).append(slot)
            if lend["phase"] == "granted" and st(lend["seat"]).get("busy"):
                want[kind] = want.get(kind, 0) + 1
    back = {}
    for slot, lend in sorted(lends.items()):
        seat = lend["seat"]
        if not seat or lend["phase"] == "returning":
            continue
        idle, kind = not st(seat).get("busy"), seat_kind(cfg, seat)
        _, _, seen = leases.get(slot) if seats[seat]["forge"] == "forgejo" else (None, None, now)
        if unfit(host_of[slot]):
            back[slot] = unfit(host_of[slot])
        elif lend["phase"] == "granted" and idle and rival(slot) > oldest.get(kind, -1):
            back[slot] = f"a GitHub job it takes has waited {int(rival(slot))}s, longer than any {kind} job"
        elif lend["phase"] == "granted" and idle and now - max(seen, lend["at"]) >= LEASE_STALE_SECONDS:
            back[slot] = f"its supervisor has not asked for its lease for {int(now - max(seen, lend['at']))}s"
    for kind, slots in have.items():
        keep = [s for s in slots if s not in back]
        # want counts every busy seat, so the extra ones are idle; a held lend gave nothing yet.
        extra = sorted(keep, key=lambda s: (lends[s]["phase"] == "granted", s))[:max(0, len(keep) - want.get(kind, 0))]
        back |= {s: f"no {kind} job needs it" for s in extra}

    admitted = sum(1 for n, r in runners.items() if not r["withheld"] and serves_pool(cfg, r["host"], n))

    def unlend(slot, seat, why):
        """readmit when its host has room for the slot, so an idle slot does not wait out
        decide()'s one admit per host per cooldown (hc-643); unlend otherwise."""
        nonlocal admitted
        host = host_of.get(slot) or (runners.get(slot) or {}).get("host", "")
        if host in cfg["hosts"] and not unfit(host) and verdicts[host] == "ROOM" \
                and admitted < cfg["pool_max_admitted"]:
            admitted += 1
            return ("readmit", slot, seat, why)
        return ("unlend", slot, seat, why)

    steps = []
    for slot, lend in sorted(lends.items()):
        seat, r = lend["seat"], runners.get(slot)
        if slot in back:
            steps.append(("return", slot, seat, back[slot]) if lend["phase"] == "granted"
                         else unlend(slot, seat, back[slot]))
        elif lend["phase"] == "returning":
            mine = [seat] if seat else by_slot.get(slot, [])
            if all(given_back(slot, s, lend["at"]) for s in mine):
                steps.append(unlend(slot, seat, "its seat gave it back"))
        # None of its own labels left either: a lend whose relabel failed half-way is not granted.
        elif lend["phase"] == "held" and r and r["withheld"] and mark in r["labels"] and not r["busy"] \
                and not set(own_labels(cfg, host_of[slot], slot)) & r["labels"]:
            steps.append(("grant", slot, seat, "the slot shows withheld, lent and idle"))

    floor = sum(1 for n, r in runners.items() if not r["withheld"] and r["host"] in cfg["hosts"]
                and serves_pool(cfg, r["host"], n))
    moved, paired = set(), lent(cfg, runners)
    for kind in sorted(oldest, key=lambda k: (-oldest[k], k)):
        need = want[kind] - len([s for s in have.get(kind, []) if s not in back])
        mine = sorted((s["slot"], seat) for seat, s in seats.items() if seat_kind(cfg, seat) == kind)
        # Idle capacity already (withheld, with room) first, then an idle slot serving GitHub,
        # then a busy one nearest its end: the lend withholds it, so it drains into the seat's
        # job. Lending only idle slots starved Forgejo while GitHub kept every slot busy.
        rank = lambda r: (r.get("busy", False), not r.get("withheld"), -r.get("elapsed", 0))
        mine.sort(key=lambda p: (*rank(runners.get(p[0], {})), p[0]))
        for slot, seat in mine:
            r, host = runners.get(slot), host_of[slot]
            if need <= 0:
                break
            # An idle withheld slot on a ROOM host adds only the load ROOM has room for, so a
            # burst gets every such slot in one cycle, cooldown or not (hc-643). A slot that is
            # admitted or busy moves one per host per cycle, as a withhold does.
            spare = bool(r) and r["withheld"] and not r["busy"] and verdicts.get(host) == "ROOM"
            if slot in lends or slot in paired or (host in moved and not spare) or unfit(host) or not r:
                continue
            if seat not in seat_state or (seats[seat]["forge"] != "forgejo" and not seat_state[seat]["online"]):
                continue
            if r["withheld"] and not spare:
                continue
            if not r["withheld"] and floor - 1 < cfg["pool_min_admitted"]:
                continue
            if rival(slot) >= oldest[kind]:
                continue
            floor -= 0 if r["withheld"] else 1
            if not spare:
                moved.add(host)
            need -= 1
            steps.append(("lend", slot, seat, f"a {kind} job has waited {int(oldest[kind])}s"))

    granted = {l["seat"] for l in lends.values() if l["phase"] == "granted"}
    steps += [("withhold", "", seat, "admitted with no slot lent to it") for seat, s in sorted(seats.items())
              if s["forge"] != "forgejo" and seat in seat_state and not seat_state[seat]["withheld"]
              and seat not in granted]
    return steps


def adopt(cfg, slot, seat_state, leases, now):
    """The lend a slot found by its mark after a restart was in: granted to a repo seat that is
    admitted or a Forgejo seat that is busy (its lease turned back to forgejo), held for the
    slot's Forgejo seat otherwise, so the next cycle grants it again or gives it back as any lend.
    A mark on a slot no seat borrows is returning."""
    mine = sorted(n for n, s in cfg.get("seats", {}).items() if s["slot"] == slot)
    st = lambda seat: seat_state.get(seat, {})
    for seat in mine:
        s = cfg["seats"][seat]
        if (s["forge"] == "forgejo" and st(seat).get("busy")) or \
                (s["forge"] != "forgejo" and st(seat) and not st(seat)["withheld"]):
            if s["forge"] == "forgejo":
                leases.set(slot, "forgejo")
            return {"seat": seat, "phase": "granted", "at": now}
    fj = [seat for seat in mine if cfg["seats"][seat]["forge"] == "forgejo"]
    if fj:
        return {"seat": fj[0], "phase": "held", "at": now}
    return {"seat": None, "phase": "returning", "at": now}


def seat_labels(cfg, seat, action):
    """Admit or withhold a repo seat: its caps, or the seat withheld label."""
    s = cfg["seats"][seat]
    scope = f"repos/{s['repo']}"
    rid, have, _ = find_runner(scope, seat)
    held = cfg["seat_withheld_label"]
    relabel(scope, rid, have, *((s["caps"], [held]) if action == "admit" else ([held], s["caps"])))


def lend_apply(cfg, steps, lends, leases, now, last_action):
    """Carry out lend_plan()'s steps. Each re-reads the slot from GitHub first: a lend or a
    grant waits while it is busy, an unlend while a repo seat of it is; a busy slot's lend
    withholds it, so it drains."""
    org, seats = f"orgs/{cfg['org']}", cfg["seats"]
    for step, slot, seat, why in steps:
        # A lend found by its mark may be on a slot no seat borrows any more: its host by name.
        host = seats[seat]["host"] if seat else next((s["host"] for s in seats.values() if s["slot"] == slot),
                                                     max((h for h in cfg["hosts"] if slot.startswith(h + "-")),
                                                         key=len, default=""))
        dry = not live(cfg, host)
        print(f"{'would ' if dry else ''}{step} {slot or seat}{f' for {seat}' if seat and slot else ''} on {host}: {why}",
              flush=True)
        if dry:
            continue
        try:
            forgejo = bool(seat) and seats[seat]["forge"] == "forgejo"
            if step == "withhold":
                seat_labels(cfg, seat, "withhold")
                continue
            if step in ("unlend", "readmit"):
                mine = [seat] if seat else [n for n, s in seats.items() if s["slot"] == slot]
                if any(seats[s]["forge"] != "forgejo" and seat_busy(s, seats[s]) for s in mine):
                    print(f"{step} {slot}: a seat of it is busy, next cycle", flush=True)
                    continue
                rid, have, _ = find_runner(org, slot)
                if step == "readmit":
                    relabel(org, rid, have, admit_labels(cfg, host, slot),
                            [cfg["lent_label"], withheld_label(cfg, host, slot)])
                    last_action[host] = now
                else:
                    relabel(org, rid, have, [], [cfg["lent_label"]])
                del lends[slot]
                continue
            if step == "return":
                if forgejo:
                    leases.set(slot, "github")
                else:
                    seat_labels(cfg, seat, "withhold")
                lends[slot].update(phase="returning", at=now)
                continue
            rid, have, busy = find_runner(org, slot)
            if busy and step == "grant":
                print(f"grant {slot}: it is busy, next cycle", flush=True)
                continue
            if step == "lend":
                # Recorded first, so a relabel that fails half-way is a held lend that goes back
                # when no job needs it, never a lent mark the governor does not know.
                lends[slot] = {"seat": seat, "phase": "held", "at": now}
                last_action[host] = now
                relabel(org, rid, have, [withheld_label(cfg, host, slot), cfg["lent_label"]],
                        own_labels(cfg, host, slot))
            elif step == "grant":
                if forgejo:
                    leases.set(slot, "forgejo")
                else:
                    seat_labels(cfg, seat, "admit")
                lends[slot].update(phase="granted", at=now)
        except API_ERRORS as e:
            print(f"{step} {slot or seat} failed: {e}", flush=True)


def seat_busy(name, seat):
    """Whether a repo seat runs a job, read live; a runner GitHub no longer lists runs none."""
    try:
        return find_runner(f"repos/{seat['repo']}", name)[2]
    except KeyError:
        return False


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


class Ec2Error(ValueError):
    """An EC2 API error, with AWS's code (InsufficientInstanceCapacity, UnauthorizedOperation).
    A deny's message ends in a kilobyte of encoded detail only `aws sts decode-authorization-message`
    reads; the text before it names the policy, so that is all a log line keeps."""
    def __init__(self, code, message):
        message = message.split(" Encoded authorization failure message:")[0]
        super().__init__(f"{code}: {message}")
        self.code = code


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
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return ET.fromstring(r.read())
        except urllib.error.HTTPError as e:
            # The body is <Response><Errors><Error><Code>; anything else stays an HTTPError.
            with e:  # closes the response it holds
                body = e.read()
            try:
                err = ET.fromstring(body).find(".//Error")
            except ET.ParseError:
                err = None
            if err is None or not err.findtext("Code"):
                raise
            raise Ec2Error(err.findtext("Code"), err.findtext("Message") or "") from e

    def state(self, instance_id):
        """(state name, epoch of its last start or 0 if AWS gave none, state reason code or
        "")."""
        item = self.call("DescribeInstances", instance_id).find(f".//{self.NS}instancesSet/{self.NS}item")
        name = None if item is None else item.find(f"{self.NS}instanceState/{self.NS}name")
        if name is None:
            raise ValueError(f"no instance {instance_id}")
        launched = item.findtext(f"{self.NS}launchTime")
        reason = item.findtext(f"{self.NS}stateReason/{self.NS}code") or ""
        return name.text, epoch(launched) if launched else 0.0, reason

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


def override_key(host):
    """The ci-runner-governor-override ConfigMap's key for host, mounted optional at
    OVERRIDE_DIR, as a dict. {} when there is none or it does not parse (logged)."""
    try:
        with open(os.path.join(OVERRIDE_DIR, host)) as f:
            raw = f.read().strip()
    except OSError:
        return {}
    try:
        o = json.loads(raw) if raw else {}
        if not isinstance(o, dict):
            raise ValueError(f"not an object: {raw}")
    except ValueError as e:
        print(f"{host}: override unreadable, ignored: {e}", flush=True)
        return {}
    return o


def read_override(host, now):
    """(action, until) from host's override key: {"action": "on"|"off", "until": <epoch>}.
    ("", 0) when there is none, it has run out, or it does not parse (logged), so a bad edit
    never powers anything."""
    o = override_key(host)
    try:
        action, until = o.get("action", ""), float(o.get("until", 0))
    except (ValueError, TypeError) as e:
        print(f"{host}: override unreadable, ignored: {e}", flush=True)
        return "", 0
    return (action, until) if action in ("on", "off") and until > now else ("", 0)


def cap_override(host, t, action, until, now, cap):
    """(action, until) with an "on" held to cap seconds from the cycle this governor first saw
    that end time (kept in t), the agent-web button's own limit. Uncapped, a hand-written "on"
    beats idle-off and the day cap for as long as it says: one set daily to 02:00 kept four
    AWS hosts up and idle all day (2026-11-12 to 11-15). "off" is never capped."""
    if action != "on":
        t.pop("override_seen", None)
        return action, until
    seen = t.get("override_seen")
    if not seen or seen[0] != until:
        seen = t["override_seen"] = (until, now)
        if until - now > cap:
            print(f"{host}: override on runs {(until - now) / 3600:.1f} h, held to {cap / 3600:g} h",
                  flush=True)
    end = min(until, seen[1] + cap)
    return ("on", end) if end > now else ("", 0)


def read_demand(host, now):
    """Items fleet-dispatch has for worker host, from its "<host>.demand" key:
    {"demand": N, "until": <epoch>}, N a whole number from 0. 0 when there is none, it has
    run out, or it does not parse (logged), so a bad edit never starts anything."""
    o = override_key(f"{host}.demand")
    n, until = o.get("demand"), o.get("until")
    if type(n) is int and n >= 0 and type(until) in (int, float):
        return n if until > now else 0
    if o:
        print(f"{host}: demand unreadable, ignored: {o!r}", flush=True)
    return 0


def read_pins(cfg, now):
    """{host: slots} for each host whose override key holds {"slots": N}, N a whole number
    from 0. Anything else is no pin (logged), so a bad edit leaves the host as it is. A live
    release reservation, "<host>.reserve" holding {"until": <epoch>}, pins the host to 0 over
    any pin of its own; past its end, or unreadable (logged), it is no reservation."""
    pins = {}
    for host in [*cfg["hosts"], *cfg["standby"]]:
        slots = override_key(host).get("slots")
        if type(slots) is int and slots >= 0:
            pins[host] = slots
        elif slots is not None:
            print(f"{host}: pin unreadable, ignored: {slots!r}", flush=True)
        reserve = override_key(f"{host}.reserve")
        until = reserve.get("until")
        if type(until) in (int, float):
            if until > now:
                pins[host] = 0
        elif reserve:
            print(f"{host}: reservation unreadable, ignored: {reserve!r}", flush=True)
    return pins


def count_run_time(t, state, now, launched=0.0):
    """Seconds the instance has run this UTC day, in t. A cycle counts at most two cycles'
    worth, so a blind spell never books hours nobody saw. On the first sight of a running
    instance (a pod restart) the day starts from AWS's launch time; power_readings() then
    adds back what earlier pods counted (restored())."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if t.get("day") != day:
        t["day"], t["run_seconds"] = day, 0.0
    running = state in ("pending", "running", "stopping")
    if running and "seen_at" in t:
        t["run_seconds"] += max(0.0, min(now - t["seen_at"], 2 * CYCLE_SECONDS, now % 86_400))
    elif running and launched:
        t["run_seconds"] = max(0.0, now - max(launched, now - now % 86_400))
    t["seen_at"] = now
    t["hours_today"] = t["run_seconds"] / 3600


def restored(host, now, launched=0.0):
    """What earlier governor pods exported for host, read back from Prometheus after a
    restart: (seconds run today, the reason of the newest power_state other than "none"
    since the instance's last launch, whether that state was up; power_readings() keeps the
    reason when that state matches, or when it is the start call's own down sample). Each pod starts from the
    one before it, so the highest hours_today exported today is the day so far; that window
    opens two cycles after midnight, past a pod's last value from yesterday. The cause looks
    back to the launch, not to midnight, and past "none": a pod that could not tell the
    cause exported "none", and a run that started yesterday started for yesterday's reason.
    The window opens two cycles before the launch, for the "starting" sample of the start
    call. launched is 0 for a host not up (power_readings), which reads today's stop reason
    instead of looking back to a start weeks ago. None when Prometheus cannot say (logged),
    or in the first two cycles after midnight with no launch, so the next cycle asks again."""
    today = now % 86_400 - 2 * CYCLE_SECONDS
    if today < CYCLE_SECONDS and not launched:
        return None
    run = now - launched + 2 * CYCLE_SECONDS if launched else today
    sel = f'host="{host}"'
    try:
        hours = 0.0 if today < CYCLE_SECONDS else max((float(r["value"][1]) for r in prom(
            f"max(max_over_time(ci_governor_power_hours_today{{{sel}}}[{int(today)}s]))")), default=0.0)
        last = prom(
            f'topk(1, max_over_time(timestamp(ci_governor_power_state{{{sel},reason!="none"}})[{int(run)}s:]))')
    except API_ERRORS as e:
        print(f"{host}: earlier run time and cause unread, counting from the launch time: {e}", flush=True)
        return None
    m = last[0]["metric"] if last else {}
    return hours * 3600, m.get("reason", ""), m.get("state") in ("starting", "running", "draining")


def power_readings(cfg, runners, tracked, now):
    """{host: tracked[host]} for each standby host with a power block and each worker host:
    state, busy_at, labels, client, hours_today, override and override_until, a worker's
    demand, plus what decide() keeps there across cycles (want_since, draining, next_cause).
    busy_at is the last cycle a slot on it was busy (a worker: it had demand) or the
    instance was not running. A host whose state cannot be read is
    left out, so nothing powers it. A stop AWS made, seen here (a host up last cycle and
    stopping or stopped now with a state reason under AWS_STOP_PREFIX; not on the first
    sight after a pod restart, which cannot tell an old one from a new one), passes the host
    over for fallback_seconds."""
    out = {}
    workers = cfg.get("workers", {})
    for host, s in [*cfg["standby"].items(), *workers.items()]:
        if "power" not in s:
            continue
        worker = host in workers
        t = tracked.setdefault(host, {})
        client, why = power_provider(s["power"])
        if not client:
            if t.get("off") != why:
                print(f"power hook off for {host}: {why}", flush=True)
            t["off"] = why
            continue
        t.pop("off", None)
        try:
            state, launched, reason = client.state(s["power"]["instance_id"])
            # A worker runs no CI job, so it has no runner labels to read.
            labels = set() if worker else host_labels(cfg, host)
        except POWER_ERRORS as e:
            print(f"{host}: instance state or runner labels unread, no power action: {e}", flush=True)
            continue
        if not labels and not worker and not t.get("unlabelled"):
            print(f"{host}: no runner labels in github_runner_state, so it is never started", flush=True)
        t["unlabelled"] = not labels and not worker
        if worker:
            t["demand"] = read_demand(host, now)
        if state != "running" or "busy_at" not in t or t.get("demand") or any(
                r["busy"] for r in runners.values() if r["host"] == host):
            t["busy_at"] = now
        # A reclaim is a host seen up last cycle and stopped by AWS now: one seen stopped
        # already (or first seen after a restart) was counted, or is not news.
        if (t.get("state") in ("pending", "running") and state in ("stopping", "stopped")
                and reason.startswith(AWS_STOP_PREFIX)):
            fall_back(t, power_of(cfg, host), "reclaim", now)
            t.update(cause="reclaim", changed="stop", changed_at=now)
            print(f"{host}: reclaimed by AWS ({reason}), passed over for {power_of(cfg, host)['fallback_seconds']}s",
                  flush=True)
        t.setdefault("restore", True)
        count_run_time(t, state, now, launched)
        up = state in ("pending", "running")
        back = restored(host, now, launched if up else 0.0) if t["restore"] else None
        if back:
            seconds, cause, was_up = back
            t["restore"] = False
            t["run_seconds"] = max(t["run_seconds"], seconds)
            t["hours_today"] = t["run_seconds"] / 3600
            # A sample's reason is this state's when its state agrees. The one exception is
            # the start call's own sample, which AWS still reported stopped: a host up since
            # a known launch takes a down sample's start cause too, never a stop's.
            if cause and (was_up == up or (up and launched and cause in START_CAUSES)):
                t.setdefault("cause", cause)
        t["override"], t["override_until"] = cap_override(
            host, t, *read_override(host, now), now, cfg.get("override_on_max_hours", 12) * 3600)
        t.update(state=state, labels=labels, client=client, launched=launched)
        out[host] = t
    return out


def fall_back(t, p, reason, now):
    """Pass a host over for p's fallback_seconds and count why (FALLBACK_REASONS), once per
    pass-over: a call while it is still passed over only extends it."""
    if t.get("fallback_until", 0) <= now:
        counts = t.setdefault("fallbacks", {})
        counts[reason] = counts.get(reason, 0) + 1
    t["fallback_until"], t["fallback_why"] = now + p["fallback_seconds"], reason


def power_state(inst):
    """What the Infra page and the start/stop notification show for an instance."""
    if inst["state"] == "running" and inst.get("draining"):
        return "draining"
    return {"pending": "starting"}.get(inst["state"], inst["state"])


def live(cfg, host):
    return not DRY_RUN or powered(cfg, host).get("power", {}).get("live", False)


def decide_workers(cfg, instances, now):
    """[(host, action, "", "WORKER", why)] for each worker host power_readings() could read:
    start on demand, stop after idle_off_seconds without any (see the module docstring)."""
    out = []
    for host in cfg.get("workers", {}):
        inst = instances.get(host)
        if inst is None:
            continue
        p = power_of(cfg, host)
        over, demand, state = inst.get("override"), inst.get("demand", 0), inst["state"]
        idle = now - inst["busy_at"] if state == "running" else 0
        capped = over != "on" and inst.get("hours_today", 0) >= p["max_hours_day"]
        cap_why = f"ran {inst.get('hours_today', 0):.1f}h today, its {p['max_hours_day']}h cap"
        action, cause, why = "hold", "", f"instance {state}"
        if state == "stopped":
            if over == "off":
                why = "override off"
            elif inst.get("fallback_until", 0) > now:
                why = f"passed over after a {inst['fallback_why']} for {int(inst['fallback_until'] - now)}s more"
            elif over == "on":
                action, cause, why = "start", "override", "override on"
            elif demand < max(1, p["start_min_excess"]):
                why = f"demand {demand}, starts at {max(1, p['start_min_excess'])}"
            elif capped:
                why = cap_why
            else:
                action, cause, why = "start", "demand", f"demand {demand}"
        elif state == "running":
            if over == "off":
                action, cause, why = "stop", "override", "override off"
            elif capped:
                action, cause, why = "stop", "cap", cap_why
            elif over != "on" and idle >= p["idle_off_seconds"]:
                action, cause, why = "stop", "idle", f"no demand for {int(idle)}s"
            elif over == "on":
                why = "override on"
            else:
                why = f"demand {demand}" if demand else f"no demand for {int(idle)}s of {p['idle_off_seconds']}s"
        inst.update(idle_seconds=idle, want=demand if state == "stopped" else 0, held_seconds=0,
                    want_since=None, draining=False, next_cause=cause if action != "hold" else "")
        out.append((host, action, "", "WORKER", why))
    return out


class State:
    lock = Lock()
    metrics = "ci_governor_up 0\n"


WITHHELD_REASONS = ("lent", "pin", "disk", "hot", "cooldown", "no-work")


def withheld_reasons(cfg, hosts, runners, lends, pins, last_action, now):
    """{runner: reason} for each withheld pool slot, the first of WITHHELD_REASONS that holds:
    lent (to its pair or a seat), pin, disk (FULL, or short of the admit disk), hot (HOT, or short
    of admit on load, pressure, memory or heat), cooldown (a ROOM host that acted within
    cooldown_seconds), no-work (room, and nothing waiting it could take)."""
    paired, out = lent(cfg, runners), {}
    verdicts = {h: verdict({k: hosts[k].get(h) for k in QUERIES}, cfg["hosts"][h]) for h in cfg["hosts"]}
    for name, r in sorted(runners.items()):
        if not r["withheld"]:
            continue
        host = r["host"]
        v, why = verdicts.get(host, ("STANDBY", ""))
        short = why.rsplit("short of admit on ", 1)[1].split("+") if "short of admit on " in why else []
        if name in (lends or {}) or name in paired:
            out[name] = "lent"
        elif host in (pins or {}):
            out[name] = "pin"
        elif v == "FULL" or "disk" in short:
            out[name] = "disk"
        elif v == "HOT" or short:
            out[name] = "hot"
        elif host in cfg["hosts"] and now - last_action.get(host, 0) < cfg["cooldown_seconds"]:
            out[name] = "cooldown"
        else:
            out[name] = "no-work"
    return out


def render(cfg, hosts, runners, queue, decisions, ok, instances=None, budget=None, lends=None, withheld=None):
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
    # A worker host has no runners, so only whether it is acted on live.
    for host in sorted(cfg.get("workers", {})):
        L.append(f'ci_governor_live{{host="{host}"}} {1 if live(cfg, host) else 0}')
    L += ["# HELP ci_governor_rate_usd_per_hour What a runner host costs an hour, and on what basis (config.json rates)",
          "# TYPE ci_governor_rate_usd_per_hour gauge"]
    for host, r in sorted(cfg.get("rates", {}).items()):
        L.append(f'ci_governor_rate_usd_per_hour{{host="{host}",basis="{r["basis"]}"}} {r["usd_per_hour"]}')
    # Between the withhold and admit lines the verdict is STEADY, so nothing else shows this.
    L += ["# HELP ci_governor_disk_held 1 when a host has withheld runners and too little disk to re-admit them",
          "# TYPE ci_governor_disk_held gauge"]
    # A slot lent to its pair, or a build slot with no build, is not held for disk.
    pair_held = lent(cfg, runners)
    for host in sorted(cfg["hosts"]):
        disk = hosts["disk"].get(host)
        held = (disk is not None and disk < cfg["hosts"][host]["admit"]["disk"]
                and any(r["withheld"] and n not in pair_held and serves_pool(cfg, host, n)
                        for n, r in runners.items() if r["host"] == host))
        L.append(f'ci_governor_disk_held{{host="{host}"}} {1 if held else 0}')
    # A paired slot held back while its pair uses the cores is normal work, not a hold for
    # load: the dashboards say "lent" for these and "withheld" for the rest.
    L += ["# HELP ci_governor_lent 1 for a withheld slot whose cores another runner uses: its pair (to: tests or build) "
          "or a seat it is lent to (to: forgejo or repo, seat: the seat)",
          "# TYPE ci_governor_lent gauge"]
    for name, (host, to) in sorted(lent(cfg, runners).items()):
        L.append(f'ci_governor_lent{{host="{host}",name="{name}",to="{to}"}} 1')
    for name, lend in sorted((lends or {}).items()):
        seat = cfg["seats"].get(lend["seat"] or "", {})
        host = runners.get(name, {}).get("host") or seat.get("host", "")
        to = "forgejo" if seat.get("forge") == "forgejo" else "repo" if seat else "seat"
        L.append(f'ci_governor_lent{{host="{host}",name="{name}",to="{to}",seat="{lend["seat"] or ""}"}} 1')
    # Why each withheld slot is held, for the CI chip's "N held: <reason>" (withheld_reasons()).
    L += ["# HELP ci_governor_withheld 1 for each withheld pool slot, by why: " + ", ".join(WITHHELD_REASONS),
          "# TYPE ci_governor_withheld gauge"]
    for name, reason in sorted((withheld or {}).items()):
        L.append(f'ci_governor_withheld{{runner="{name}",reason="{reason}"}} 1')
    # The reason is in the log, not a label: it changes every cycle.
    L += ["# HELP ci_governor_decision This cycle's verdict and actions per host (a paired host can show several)", "# TYPE ci_governor_decision gauge"]
    # A paired host moves several slots in one cycle: one row per host, verdict and action.
    for host, action, v in dict.fromkeys((h, a, v) for h, a, _, v, _ in decisions):
        L.append(f'ci_governor_decision{{host="{host}",verdict="{v}",action="{action}"}} 1')
    # A powered host, for the agent-web Infra page and Sentinel's start/stop notice. reason is
    # why the governor last started or stopped it (queue, idle, cap, override), "none" before
    # it has, so the series changes only when the power does.
    L += ["# HELP ci_governor_power_state A powered standby host: stopped, starting, running, draining or stopping",
          "# TYPE ci_governor_power_state gauge",
          "# HELP ci_governor_power_hours_today Hours its instance has run this UTC day (carried across restarts)",
          "# TYPE ci_governor_power_hours_today gauge",
          "# HELP ci_governor_power_override End (epoch) of the override in force, 0 with action none",
          "# TYPE ci_governor_power_override gauge",
          "# HELP ci_governor_fallback_total Times a host was passed over (a standby host for the next one), by why",
          "# TYPE ci_governor_fallback_total counter",
          "# HELP ci_governor_power_fallback_until End (epoch) of the host's pass-over, 0 when none",
          "# TYPE ci_governor_power_fallback_until gauge",
          "# HELP ci_governor_power_demand A worker host's items from fleet-dispatch (live plus waiting)",
          "# TYPE ci_governor_power_demand gauge"]
    for host, inst in sorted((instances or {}).items()):
        p = power_of(cfg, host)
        L.append(f'ci_governor_power_state{{host="{host}",state="{power_state(inst)}",'
                 f'reason="{inst.get("cause") or "none"}"}} 1')
        L.append(f'ci_governor_power_hours_today{{host="{host}"}} {inst.get("hours_today", 0):.3f}')
        L.append(f'ci_governor_power_max_hours_day{{host="{host}"}} {p["max_hours_day"]}')
        L.append(f'ci_governor_power_override{{host="{host}",action="{inst.get("override") or "none"}"}} '
                 f'{int(inst.get("override_until", 0))}')
        # Rule progress, each beside its line: idle against idle-off, jobs it could take past
        # the other hosts against the start excess, the start hold against its length. A worker
        # has no start hold, so it has no hold lines.
        rules = [("idle_seconds", int(inst.get("idle_seconds", 0))),
                 ("idle_off_seconds", inst.get("idle_off_seconds", p["idle_off_seconds"])),
                 ("want", inst.get("want", 0)), ("start_min_excess", p["start_min_excess"])]
        if host in cfg["standby"]:
            rules += [("held_seconds", int(inst.get("held_seconds", 0))),
                      ("start_after_seconds", p["start_after_seconds"])]
        for name, value in rules:
            L.append(f'ci_governor_power_{name}{{host="{host}"}} {value}')
        if host in cfg.get("rates", {}):
            L.append(f'ci_governor_power_usd_per_hour{{host="{host}"}} {cfg["rates"][host]["usd_per_hour"]}')
        L.append(f'ci_governor_power_last_change{{host="{host}",action="{inst.get("changed") or "none"}"}} '
                 f'{int(inst.get("changed_at", 0))}')
        for reason in FALLBACK_REASONS:
            L.append(f'ci_governor_fallback_total{{host="{host}",reason="{reason}"}} '
                     f'{inst.get("fallbacks", {}).get(reason, 0)}')
        L.append(f'ci_governor_power_fallback_until{{host="{host}"}} {int(inst.get("fallback_until", 0))}')
        if host in cfg.get("workers", {}):
            L.append(f'ci_governor_power_demand{{host="{host}"}} {inst.get("demand", 0)}')
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


def cycle(cfg, last_action, tracked=None, budget=None, lending=None):
    """One reading and its actions. lending is (Leases, lends) to lend slots to seats, None not to."""
    now = time.time()
    try:
        hosts, runners, waiting = observe(pools(cfg))
        shared_waiting = observe_shared(cfg)
        seat_state, seat_waits = observe_seats(cfg) if lending else ({}, [])
    except API_ERRORS as e:
        print(f"blind cycle, deciding nothing: {e}", flush=True)
        with State.lock:
            State.metrics = "ci_governor_up 1\nci_governor_ok 0\n"
        return
    # ci_governor_queue is the shared pool's queue: a build waiting on its own host is not one
    # a disk-held pool slot could take (CiHostHeldOutByDisk).
    queue = len(pool_jobs(waiting, build_marks(cfg)))
    instances = power_readings(cfg, runners, {} if tracked is None else tracked, now)
    restore = {h for h in cfg["hosts"] if not live(cfg, h)}
    pins = read_pins(cfg, now)
    leases, lends = lending or (None, {})
    # A slot that carries the lent mark from before a restart stays lent to its seat (hc-643).
    for name, r in sorted(runners.items()):
        if lending and cfg["lent_label"] in r["labels"] and name not in lends:
            lends[name] = adopt(cfg, name, seat_state, leases, now)
            print(f"{name} was lent before this start; {lends[name]['phase']} for {lends[name]['seat']}", flush=True)
    if lending:
        # First, so the oldest wait wins a slot: a host a lend moved is in its cooldown for decide().
        steps = lend_plan(cfg, hosts, runners, seat_state, seat_waits, lends, leases, pins, last_action, now)
        lend_apply(cfg, steps, lends, leases, now, last_action)
    # A lent slot is the lending's alone: decide() never admits or counts it.
    governed = {n: r for n, r in runners.items() if n not in lends}
    decisions = decide(cfg, hosts, governed, waiting, last_action, now, instances, pins, restore, shared_waiting)
    decisions += decide_workers(cfg, instances, now)
    for host, action, runner, v, why in decisions:
        if action != "start" and host in instances:
            instances[host].pop("denied", None)  # a deny is logged again once the host asks anew
        if action == "hold":
            continue
        if action in ("pack", "unpack"):
            # A decision of the governor's own (pack_plan()); the withholds it brings log apart.
            print(f"{action} {host}: {why}; queue {queue}", flush=True)
            continue
        power = action in ("start", "stop")
        target = powered(cfg, host)["power"]["instance_id"] if power else runner
        dry = not live(cfg, host) and v not in ("PINNED", "RESTORE")
        # The deny is retried each cycle (that is how the governor sees it lift), but logged once.
        denied = power and not dry and instances[host].get("denied")
        if not (action == "start" and denied):
            print(f"{'would ' if dry else ''}{action} {target} on {host}: {v}, {why}; queue {queue}", flush=True)
        if dry:
            last_action[host] = now
            continue
        try:
            if power:
                getattr(instances[host]["client"], action)(target)
                instances[host].update(cause=instances[host].get("next_cause", ""), changed=action, changed_at=now)
                if instances[host].pop("denied", None):
                    print(f"start {target} on {host}: the deny lifted; queue {queue}", flush=True)
            else:
                apply(cfg, action, runner, host)
            last_action[host] = now
        except POWER_ERRORS as e:
            code = getattr(e, "code", "")
            deny = action == "start" and code in DENY_CODES
            if not (deny and denied):
                print(f"{action} {target} failed: {e}" + ("; quiet while it holds" if deny else ""), flush=True)
            if deny:
                instances[host]["denied"] = True
            if action == "start" and isinstance(e, Ec2Error) and code not in DENY_CODES:
                why = "capacity" if code in CAPACITY_CODES else "refused"
                fall_back(instances[host], power_of(cfg, host), why, now)
                print(f"{host}: start {why} ({code}), passed over"
                      + ("" if host in cfg.get("workers", {}) else " for the next standby host"), flush=True)
    if budget is not None:
        read_budget(cfg, budget, now)
    with State.lock:
        State.metrics = "ci_governor_up 1\n" + render(
            cfg, hosts, runners, queue, decisions, True, instances, budget, lends,
            withheld_reasons(cfg, hosts, runners, lends, pins, last_action, now))


class Handler(BaseHTTPRequestHandler):
    # main() sets it; None answers every /lease 404, so a supervisor stays on github.
    leases = None

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/health":
            body = b"ok\n"
        elif url.path == "/metrics":
            with State.lock:
                body = State.metrics.encode()
        elif url.path == "/lease":
            # /lease?slot=<unity slot>&have=<github|forgejo>[&wait=<s>]: the slot's lease once
            # it differs from have, or have after wait (at most LEASE_WAIT_MAX).
            q = urllib.parse.parse_qs(url.query)
            slot, have = q.get("slot", [""])[0], q.get("have", ["github"])[0]
            if self.leases is None or slot not in self.leases.lease:
                return self.answer(404, b"no such slot\n")
            try:
                wait = min(max(float(q.get("wait", [LEASE_WAIT_MAX])[0]), 0), LEASE_WAIT_MAX)
            except ValueError:
                wait = -1
            if have not in LEASES or wait < 0:
                return self.answer(400, b"have is github or forgejo, wait a number of seconds\n")
            body = (self.leases.wait(slot, have, wait) + "\n").encode()
        else:
            return self.answer(404, b"")
        self.answer(200, body)

    def answer(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    cfg = load_config(CONFIG)
    if not DRY_RUN and not TOKEN:
        sys.exit("live mode needs GITHUB_TOKEN (Organization > Self-hosted runners: write)")
    # Before the server: a /lease asked before this answers 404, which a supervisor reads as github.
    leases = Leases(sorted({s["slot"] for s in cfg["seats"].values() if s["forge"] == "forgejo"}))
    Handler.leases = leases
    # A thread per request: every Forgejo seat holds a /lease long-poll open.
    server = ThreadingHTTPServer(("", PORT), Handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, daemon=True).start()
    live_hosts = sorted(h for h in [*cfg["standby"], *cfg["workers"]] if DRY_RUN and live(cfg, h))
    print(f"ci-runner-governor: {'dry run' if DRY_RUN else 'live'}, hosts {', '.join(sorted(cfg['hosts']))}"
          + (f", live anyway: {', '.join(live_hosts)}" if live_hosts else "")
          + f", seats {', '.join(sorted(cfg['seats']))}", flush=True)
    last_action, tracked, budget, lends = {}, {}, {}, {}
    while True:
        cycle(cfg, last_action, tracked, budget, (leases, lends))
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
