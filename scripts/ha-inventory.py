#!/usr/bin/env python3
"""Show which Home Assistant integrations and entities are enabled and which are disabled. Read-only.

Usage:
  scripts/ha-inventory.py                 every integration (enabled or disabled, its load state,
                                          entity counts), then the enabled entities that are
                                          unavailable, grouped by device: the retire candidates
  scripts/ha-inventory.py --entities      also list every entity with enabled/disabled and state
  scripts/ha-inventory.py --grep TEXT     only integrations, devices and entities matching TEXT
  scripts/ha-inventory.py --json          the joined rows as JSON, for jq

A device that was retired but never deleted keeps its config entry and entities enabled, so it
reads `unavailable` forever and pages through every health check that counts unavailable
entities (Athom plug 613888, 2026-11). This view finds it: it shows up under "Unavailable".
Delete it in HA (Settings -> Devices -> the device -> Delete, or the integration entry), then
remove it from the seeds in apps/home-assistant/deployment.yaml and config/pyscript/room_layout.yaml.

It sends only the websocket reads config_entries/get, config/entity_registry/list,
config/device_registry/list and get_states; it changes nothing.

Needs the `websockets` package and rbw unlocked (entry "HASS token"), or HA_TOKEN set; exits 2
without a token. HA_URL defaults to https://hass.kblab.me.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections import defaultdict

HA_URL = os.environ.get("HA_URL", "https://hass.kblab.me")
READS = ("config_entries/get", "config/entity_registry/list", "config/device_registry/list", "get_states")


def token():
    if os.environ.get("HA_TOKEN"):
        return os.environ["HA_TOKEN"]
    try:
        out = subprocess.run(["rbw", "get", "HASS token"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        out = None
    if out is None or out.returncode != 0 or not out.stdout.strip():
        print('ha-inventory: no token: set HA_TOKEN, or unlock rbw (entry "HASS token")', file=sys.stderr)
        sys.exit(2)
    return out.stdout.strip()


async def fetch(url, tok):
    """Return {read: result} for every read in READS, over one websocket."""
    import websockets

    ws_url = url.replace("https://", "wss://", 1).replace("http://", "ws://", 1).rstrip("/") + "/api/websocket"
    async with websockets.connect(ws_url, max_size=None, open_timeout=20) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": tok}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            raise SystemExit(f"ha-inventory: Home Assistant refused the token: {auth.get('message', auth)}")
        out = {}
        for i, read in enumerate(READS, start=1):
            await ws.send(json.dumps({"id": i, "type": read}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                if msg.get("id") == i and msg.get("type") == "result":
                    break
            if not msg.get("success"):
                raise SystemExit(f"ha-inventory: {read} failed: {msg.get('error')}")
            out[read] = msg["result"]
        return out


def join(data):
    """Join the four reads into integration rows, each with its devices and entities."""
    states = {s["entity_id"]: s for s in data["get_states"]}
    devices = {d["id"]: d for d in data["config/device_registry/list"]}
    integrations = {}
    for e in data["config_entries/get"]:
        integrations[e["entry_id"]] = {
            "entry_id": e["entry_id"],
            "domain": e["domain"],
            "title": e.get("title") or e["domain"],
            "disabled_by": e.get("disabled_by"),
            "state": e.get("state"),
            "entities": [],
        }
    for r in data["config/entity_registry/list"]:
        key = r.get("config_entry_id") or f"yaml:{r['platform']}"
        row = integrations.setdefault(key, {
            "entry_id": None, "domain": r["platform"], "title": f"{r['platform']} (YAML, no config entry)",
            "disabled_by": None, "state": None, "entities": [],
        })
        dev = devices.get(r.get("device_id")) or {}
        st = states.get(r["entity_id"]) or {}
        row["entities"].append({
            "entity_id": r["entity_id"],
            "disabled_by": r.get("disabled_by"),
            "hidden_by": r.get("hidden_by"),
            "state": st.get("state"),
            # HA restores a registry entry that no integration provides any more as unavailable,
            # with restored: true. That is an orphan: delete it from the entity registry.
            "orphan": bool((st.get("attributes") or {}).get("restored")),
            "device": dev.get("name_by_user") or dev.get("name"),
            "device_disabled_by": dev.get("disabled_by"),
        })
    for row in integrations.values():
        row["entities"].sort(key=lambda x: x["entity_id"])
    return sorted(integrations.values(), key=lambda r: (r["domain"], r["title"].lower()))


def unavailable(row):
    """Enabled entities of an enabled integration that read unavailable: what the health checks count."""
    if row["disabled_by"]:
        return []
    return [e for e in row["entities"] if not e["disabled_by"] and e["state"] == "unavailable"]


def matches(text, row, ent=None):
    hay = [row["domain"], row["title"]] + ([ent["entity_id"], ent["device"] or ""] if ent else [])
    return any(text.lower() in h.lower() for h in hay)


def filtered(rows, text):
    if not text:
        return rows
    out = []
    for r in rows:
        if matches(text, r):
            out.append(r)
        else:
            ents = [e for e in r["entities"] if matches(text, r, e)]
            if ents:
                out.append({**r, "entities": ents})
    return out


def enabled_word(disabled_by):
    return "enabled" if not disabled_by else f"disabled ({disabled_by})"


LIST_MAX = 6


def short(ents):
    if len(ents) <= LIST_MAX:
        return ", ".join(ents)
    return f"{', '.join(ents[:3])}, and {len(ents) - 3} more (--grep to list)"


def render(rows, show_entities=False):
    lines = []
    n_on = sum(1 for r in rows if not r["disabled_by"])
    lines.append(f"Integrations: {len(rows)} ({n_on} enabled, {len(rows) - n_on} disabled)")
    lines.append(f"  {'integration':<44} {'entry':<20} {'loaded':<14} {'entities on/off/unavail'}")
    for r in rows:
        on = sum(1 for e in r["entities"] if not e["disabled_by"])
        name = f"{r['domain']}: {r['title']}"
        lines.append(f"  {name[:44]:<44} {enabled_word(r['disabled_by']):<20} {(r['state'] or '-'):<14} "
                     f"{on}/{len(r['entities']) - on}/{len(unavailable(r))}")
    bad = [r for r in rows if not r["disabled_by"] and r["state"] not in (None, "loaded")]
    if bad:
        lines.append("")
        lines.append(f"Enabled but not loaded: {len(bad)}")
        for r in bad:
            lines.append(f"  {r['domain']}: {r['title']}  ({r['state']})")
    by_device = defaultdict(list)
    for r in rows:
        for e in unavailable(r):
            dev = e["device"] or ("(orphaned: nothing provides it)" if e["orphan"] else "(no device)")
            by_device[(r["domain"], dev)].append(e["entity_id"])
    lines.append("")
    lines.append(f"Unavailable (enabled, so they read red): {sum(len(v) for v in by_device.values())} entities "
                 f"on {len(by_device)} devices")
    for (domain, dev), ents in sorted(by_device.items()):
        lines.append(f"  {domain} / {dev}: {short(ents)}")
    if show_entities:
        lines.append("")
        lines.append("Entities")
        for r in rows:
            lines.append(f"  {r['domain']}: {r['title']}")
            for e in r["entities"]:
                hidden = f", hidden ({e['hidden_by']})" if e["hidden_by"] else ""
                orphan = ", orphaned" if e["orphan"] else ""
                lines.append(f"    {e['entity_id']:<60} {enabled_word(e['disabled_by'])}{hidden}{orphan}  "
                             f"{e['state'] or '-'}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--entities", action="store_true", help="list every entity")
    ap.add_argument("--grep", metavar="TEXT", help="only rows whose integration, device or entity contains TEXT")
    ap.add_argument("--json", action="store_true", help="print the joined rows as JSON")
    args = ap.parse_args(argv)
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("ha-inventory: needs the websockets package (pacman -S python-websockets, or pip install websockets)",
              file=sys.stderr)
        return 2
    rows = filtered(join(asyncio.run(fetch(HA_URL, token()))), args.grep)
    print(json.dumps(rows, indent=2) if args.json else render(rows, args.entities))
    return 0


if __name__ == "__main__":
    sys.exit(main())
