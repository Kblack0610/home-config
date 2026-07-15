#!/usr/bin/env python3
"""fleet-exporter - turn the gatus fleet into Prometheus gauges, roster-first.

WHY THIS EXISTS (gatus's own /metrics is not enough):

1. Gatus exports NO metrics for external-endpoints. Verified live: 10 keys in
   /metrics vs 13 in the API, the missing three being exactly the pushed ones
   (gp-mac, linux-cachyos, h0001). Those are the machines nothing can poll - the
   work Mac, the VDI, the tablet - so on gatus's metrics alone, Grafana would be
   blind to precisely the machines this fleet exists for.

2. Gatus only knows about RESULTS. A machine that is declared but has never
   pushed does not exist to it - not down, ABSENT. That is the bug that started
   all this: the glyph read green while four of five machines had never reported
   once, because the denominator came from the same set as the numerator. No
   gatus version can fix that, because "who SHOULD be reporting" is not a
   question gatus is asking. $FLEET_ROSTER is.

3. Gatus's metrics are counters (gatus_results_total), so even for polled hosts
   you would reconstruct liveness from rate() windows. Liveness is a gauge.

So: iterate the ROSTER, not the API. A roster member missing from the API is
enrolled=0 - which finally makes "never reported" alertable in Prometheus,
instead of something only a status bar knows.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

GATUS = os.environ.get("GATUS_URL", "http://gatus-fleet.gatus-fleet.svc.cluster.local:8080").rstrip("/")
# Same freshness rule the status bars use, so the glyph and Grafana cannot
# disagree about what "up" means.
STALE_AFTER = int(os.environ.get("FLEET_STALE_AFTER", "180"))
ROSTER = [m for m in os.environ.get("FLEET_ROSTER", "").split() if m]
PORT = int(os.environ.get("PORT", "9300"))


def fetch():
    """name -> (group, success, age_seconds). Empty dict if gatus is unreachable."""
    req = urllib.request.Request(f"{GATUS}/api/v1/endpoints/statuses")
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    now = time.time()
    out = {}
    for e in data:
        results = e.get("results") or []
        if not results:
            continue
        last = results[-1]
        ts = last.get("timestamp", "")
        try:
            # RFC3339, always UTC from gatus. Trim fractional seconds.
            #
            # Parse as EXPLICITLY UTC. time.mktime() cannot be used here: it
            # interprets a struct_time as LOCAL time and silently ignores %z, so
            # every timestamp came out skewed by the UTC offset (7h here) - far
            # past any freshness window, and the whole fleet rendered DOWN while
            # being demonstrably up. A dashboard that plausibly lies is worse than
            # no dashboard.
            base = ts.split(".")[0].rstrip("Z")
            dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            age = now - dt.timestamp()
        except Exception:
            age = -1
        out[e["name"]] = (e.get("group", ""), bool(last.get("success")), age)
    return out


def render():
    try:
        live = fetch()
        reachable = True
    except Exception:
        live, reachable = {}, False

    L = [
        "# HELP fleet_exporter_gatus_reachable 1 if the gatus API answered this scrape",
        "# TYPE fleet_exporter_gatus_reachable gauge",
        f"fleet_exporter_gatus_reachable {1 if reachable else 0}",
        "# HELP fleet_machine_enrolled 1 if this rostered machine has ever reported to gatus",
        "# TYPE fleet_machine_enrolled gauge",
        "# HELP fleet_machine_up 1 if the machine reported success within the freshness window",
        "# TYPE fleet_machine_up gauge",
        "# HELP fleet_machine_last_seen_seconds Seconds since this machine's last result",
        "# TYPE fleet_machine_last_seen_seconds gauge",
    ]

    # Roster drives the loop. A machine absent from the API still gets a series -
    # that is the entire point: never-reported must be visible, not missing.
    for name in ROSTER:
        if name in live:
            group, success, age = live[name]
            # Tolerate clock skew in BOTH directions. A negative age means the
            # result is stamped in the future relative to THIS pod - which does
            # not mean stale, it means the two clocks disagree. Requiring age >= 0
            # made the entire fleet read DOWN the first time this landed on a node
            # whose clock was ~100s behind (hp-victus, NTP not syncing), while
            # every machine was demonstrably up. Liveness must not depend on two
            # machines agreeing to the second.
            #
            # last_seen_seconds is still emitted RAW, negatives and all, so the
            # skew stays visible instead of being quietly clamped away.
            up = 1 if (success and -STALE_AFTER < age < STALE_AFTER) else 0
            L.append(f'fleet_machine_enrolled{{name="{name}",group="{group}"}} 1')
            L.append(f'fleet_machine_up{{name="{name}",group="{group}"}} {up}')
            if age >= 0:
                L.append(f'fleet_machine_last_seen_seconds{{name="{name}",group="{group}"}} {age:.0f}')
        else:
            # group is unknown precisely because gatus has never heard of it.
            L.append(f'fleet_machine_enrolled{{name="{name}",group="unknown"}} 0')
            L.append(f'fleet_machine_up{{name="{name}",group="unknown"}} 0')

    # Anything gatus knows that the roster does not. Not counted for health, but
    # surfaced so a machine added server-side and forgotten in the roster is
    # visible rather than silent - the same class of bug, one layer up.
    for name, (group, success, age) in sorted(live.items()):
        if name not in ROSTER:
            up = 1 if (success and -STALE_AFTER < age < STALE_AFTER) else 0
            L.append(f'fleet_machine_unrostered{{name="{name}",group="{group}"}} {up}')

    L.append(f"# HELP fleet_roster_size Machines expected to report")
    L.append(f"# TYPE fleet_roster_size gauge")
    L.append(f"fleet_roster_size {len(ROSTER)}")
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
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass  # one line per scrape is just noise


if __name__ == "__main__":
    print(f"fleet-exporter: gatus={GATUS} roster={len(ROSTER)} stale_after={STALE_AFTER}s port={PORT}", flush=True)
    HTTPServer(("", PORT), H).serve_forever()
