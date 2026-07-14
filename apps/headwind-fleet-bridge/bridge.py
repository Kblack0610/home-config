#!/usr/bin/env python3
"""headwind-fleet-bridge - mirror Headwind device liveness into the gatus fleet.

Android devices managed by Headwind don't push fleet-pulse heartbeats themselves.
This long-running bridge reads the hmdm `devices` table every INTERVAL seconds and,
for each device, POSTs a heartbeat to gatus:
    POST {GATUS_URL}/api/v1/endpoints/{FLEET_GROUP}_<device>/external?success=<seen-recently>
so tablets land on the SAME fleet surface as the computers.

Design notes:
- Liveness is judged from Headwind's own `lastupdate` (epoch ms of the device's last
  sync). A device is "up" if Headwind saw it within DEVICE_STALE_AFTER seconds.
- The bridge pushes every INTERVAL (< the readers' 180s window), so the gatus result
  TIMESTAMP always stays fresh; the success BOOL carries the real device liveness.
- `lastupdate = 0` (never synced, e.g. HMDM's default template device) is skipped.
- A gatus 404 means the device isn't declared as an external-endpoint yet; that's
  logged and skipped, so enrollment and gatus-registration can happen in any order.
- Read-only: only SELECTs the devices table. Never writes to Headwind.
"""
import os
import re
import time
import traceback
import urllib.error
import urllib.request

import psycopg

INTERVAL = int(os.environ.get("INTERVAL", "60"))
DEVICE_STALE_AFTER = int(os.environ.get("DEVICE_STALE_AFTER", "900"))  # 15 min
GATUS = os.environ.get("GATUS_URL", "http://gatus-fleet.gatus-fleet.svc.cluster.local:8080").rstrip("/")
TOKEN = os.environ.get("FLEET_TOKEN", "")
# gatus keys are <group>_<name>, so this MUST match the group the tablets are
# declared under in apps/gatus-fleet/configmap.yaml. Configurable rather than
# hardcoded: the group moved from `fleet` to `android` when the fleet grew groups
# (workplace/homelab/k3s/android/iot), and a hardcoded prefix would have silently
# 404'd every push.
GROUP = os.environ.get("FLEET_GROUP", "android")
DRY = os.environ.get("DRY_RUN", "false").lower() == "true"
HEARTBEAT = "/tmp/alive"

DSN = "host={h} port={p} dbname=hmdm user=hmdm password={pw} connect_timeout=10".format(
    h=os.environ.get("SQL_HOST", "postgres.databases.svc.cluster.local"),
    p=os.environ.get("SQL_PORT", "5432"),
    pw=os.environ["SQL_PASS"],
)


def gatus_key(number):
    """gatus derives key = <group>_<name>, lowercasing and mapping / _ , . -> -."""
    slug = re.sub(r"[/_,.\s]", "-", number.strip().lower())
    return f"{GROUP}_{slug}"


def push(number, fresh):
    key = gatus_key(number)
    if DRY:
        print(f"[dry-run] {key} success={str(fresh).lower()}", flush=True)
        return
    url = f"{GATUS}/api/v1/endpoints/{key}/external?success={'true' if fresh else 'false'}"
    req = urllib.request.Request(url, method="POST", headers={"Authorization": "Bearer " + TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"pushed {key} success={str(fresh).lower()} (HTTP {r.status})", flush=True)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"skip {key}: not declared in gatus (add a fleet external-endpoint)", flush=True)
        else:
            print(f"push {key} failed: HTTP {e.code}", flush=True)
    except Exception as e:  # noqa: BLE001 - never let one device kill the loop
        print(f"push {key} error: {e}", flush=True)


def tick():
    now_ms = time.time() * 1000.0
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT number, lastupdate FROM devices WHERE lastupdate > 0")
            rows = cur.fetchall()
    if not rows:
        print("no synced devices yet", flush=True)
        return
    for number, lastupdate in rows:
        age_s = (now_ms - float(lastupdate)) / 1000.0
        push(number, age_s < DEVICE_STALE_AFTER)


def main():
    print(
        f"headwind-fleet-bridge: interval={INTERVAL}s device_stale={DEVICE_STALE_AFTER}s "
        f"gatus={GATUS} dry={DRY}",
        flush=True,
    )
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001 - keep looping through transient DB/gatus errors
            traceback.print_exc()
        try:
            with open(HEARTBEAT, "w") as f:
                f.write(str(int(time.time())))
        except OSError:
            pass
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
