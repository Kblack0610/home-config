#!/usr/bin/env python3
"""Regenerate the Home Assistant Launcher dashboard from ingresses + Ansible inventory.

Walks every Ingress under apps/, extracts the hostnames and namespace, and walks
ansible/inventory.yml for bare-metal hosts. Emits a Lovelace `type: sections`
view to apps/home-assistant/config/dashboards/launcher.yaml between
BEGIN_GENERATED_LAUNCHER / END_GENERATED_LAUNCHER markers.

Run after adding, renaming, or removing an Ingress or inventory host. The output
is committed to git; Flux reconciles the ConfigMap, the sync-managed-config init
container copies the file to the HA PVC on pod restart, and HA picks it up.

Opt out per Ingress with:
    metadata:
      annotations:
        homepage.kblab.me/launcher: "false"

Namespace -> group mapping is a static dict below. Add entries as new namespaces
land. Unknown namespaces default to "Other".
"""

import glob
import os
import re
import sys

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAUNCHER = os.path.join(REPO, "apps/home-assistant/config/dashboards/launcher.yaml")
INGRESS_GLOB = os.path.join(REPO, "apps/*/ingress*.yaml")
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")

BEGIN_MARK = "# BEGIN_GENERATED_LAUNCHER"
END_MARK = "# END_GENERATED_LAUNCHER"

# Namespace -> launcher group. Keep sorted, add as new namespaces land.
NAMESPACE_GROUP = {
    "actual-budget": "Finance",
    "ai-gateway": "AI",
    "ai-services": "AI",
    "apps": "Apps",
    "comfyui": "AI",
    "forgejo": "Dev",
    "gatus": "Ops",
    "headscale": "Ops",
    "home-assistant": "Home",
    "immich": "Media",
    "jellyfin": "Media",
    "karakeep": "Apps",
    "litellm": "AI",
    "monitoring": "Ops",
    "mqtt2prom": "Ops",
    "neptune": "Printing",
    "openclaw": "AI",
    "orcaslicer": "Printing",
    "semaphore": "Ops",
}

# Host -> icon. Fallback is mdi:application.
HOST_ICON = {
    "actual-budget": "mdi:cash-multiple",
    "alertmanager": "mdi:bell-alert",
    "comfyui": "mdi:image-multiple",
    "finance": "mdi:cash-multiple",
    "finance-tools": "mdi:cash-multiple",
    "forgejo": "mdi:git",
    "gatus": "mdi:heart-pulse",
    "git": "mdi:git",
    "grafana": "mdi:chart-bar",
    "hass": "mdi:home-assistant",
    "headscale": "mdi:vpn",
    "headscale-ui": "mdi:vpn",
    "homepage": "mdi:view-dashboard",
    "immich": "mdi:image",
    "jellyfin": "mdi:jellyfin",
    "karakeep": "mdi:bookmark-multiple",
    "llm": "mdi:robot",
    "neptune": "mdi:printer-3d",
    "openclaw": "mdi:robot-industrial",
    "photos": "mdi:image",
    "prometheus": "mdi:chart-line",
    "slicer": "mdi:printer-3d-nozzle",
    "status": "mdi:heart-pulse",
}

# Inventory-host -> { label, url, icon }. URL points at the admin UI or
# node_exporter metrics. Macs and Pis are metrics-only today.
INVENTORY_TILES = {
    "thinkcentre": ("Thinkcentre", "http://192.168.1.100:9100/metrics", "mdi:server"),
    "hp-victus":   ("HP Victus",   "http://192.168.1.243:9100/metrics", "mdi:server"),
    "asus-laptop": ("Asus Laptop", "http://192.168.1.152:9100/metrics", "mdi:laptop"),
    "mac-studio":  ("Mac Studio",  "http://192.168.1.4:9100/metrics",   "mdi:apple"),
    "mac-mini":    ("Mac Mini",    "http://192.168.1.7:9100/metrics",   "mdi:apple"),
    "pi5-master":  ("Pi5 Master",  "http://192.168.1.20:9100/metrics",  "mdi:raspberry-pi"),
    "pi5-worker1": ("Pi5 Worker1", "http://192.168.1.21:9100/metrics",  "mdi:raspberry-pi"),
    "pi5-worker2": ("Pi5 Worker2", "http://192.168.1.22:9100/metrics",  "mdi:raspberry-pi"),
    "pi5-worker3": ("Pi5 Worker3", "http://192.168.1.23:9100/metrics",  "mdi:raspberry-pi"),
    "pi4-worker4": ("Pi4 Worker4", "http://192.168.1.24:9100/metrics",  "mdi:raspberry-pi"),
    "pi4-worker5": ("Pi4 Worker5", "http://192.168.1.124:9100/metrics", "mdi:raspberry-pi"),
    "openwrt-router": ("OpenWrt",  "http://192.168.1.1/",               "mdi:router-wireless"),
}


def extract_ingress_tiles() -> list[dict]:
    """Return [{host, group, icon}, ...] from apps/*/ingress*.yaml, sorted by host."""
    tiles: dict[str, dict] = {}
    for path in sorted(glob.glob(INGRESS_GLOB)):
        with open(path) as fp:
            for doc in yaml.safe_load_all(fp):
                if not doc or doc.get("kind") != "Ingress":
                    continue
                meta = doc.get("metadata") or {}
                annotations = meta.get("annotations") or {}
                if annotations.get("homepage.kblab.me/launcher") == "false":
                    continue
                namespace = meta.get("namespace") or ""
                group = NAMESPACE_GROUP.get(namespace, "Other")
                for rule in (doc.get("spec") or {}).get("rules") or []:
                    host = rule.get("host")
                    if not host or host in tiles:
                        continue
                    # Only surface LAN *.kblab.me (public portfolio domains live elsewhere).
                    if not host.endswith(".kblab.me") and host != "kblab.me":
                        continue
                    subdomain = host.split(".", 1)[0]
                    icon = HOST_ICON.get(subdomain, "mdi:application")
                    tiles[host] = {"host": host, "group": group, "icon": icon}
    return sorted(tiles.values(), key=lambda t: (t["group"], t["host"]))


def render_tiles_by_group(tiles: list[dict]) -> list[str]:
    """Render one Lovelace section per group, each containing web-link cards."""
    lines: list[str] = []
    groups: dict[str, list[dict]] = {}
    for tile in tiles:
        groups.setdefault(tile["group"], []).append(tile)

    for group in sorted(groups):
        lines += [
            "  - type: grid",
            "    cards:",
            "      - type: heading",
            f"        heading: {group}",
            f"        icon: {group_icon(group)}",
        ]
        for tile in groups[group]:
            name = pretty_name(tile["host"])
            lines += [
                "      - type: tile",
                f"        entity: sun.sun",
                f"        name: {name}",
                f"        icon: {tile['icon']}",
                "        tap_action:",
                "          action: url",
                f"          url_path: https://{tile['host']}",
                "        hide_state: true",
            ]
    return lines


def render_inventory_section() -> list[str]:
    """Render the bare-metal hosts section from ansible/inventory.yml."""
    with open(INVENTORY) as fp:
        inv = yaml.safe_load(fp)

    present: list[str] = []
    children = (inv.get("all") or {}).get("children") or {}
    for group in children.values():
        for hostname in (group.get("hosts") or {}).keys():
            if hostname in INVENTORY_TILES:
                present.append(hostname)

    if not present:
        return []

    lines = [
        "  - type: grid",
        "    cards:",
        "      - type: heading",
        "        heading: Bare Metal",
        "        icon: mdi:server-network",
    ]
    for hostname in sorted(present):
        label, url, icon = INVENTORY_TILES[hostname]
        lines += [
            "      - type: tile",
            "        entity: sun.sun",
            f"        name: {label}",
            f"        icon: {icon}",
            "        tap_action:",
            "          action: url",
            f"          url_path: {url}",
            "        hide_state: true",
        ]
    return lines


def pretty_name(host: str) -> str:
    """Turn `jellyfin.kblab.me` -> `Jellyfin`, `headscale-ui.kblab.me` -> `Headscale Ui`."""
    sub = host.split(".", 1)[0]
    if sub == "":
        return host
    return " ".join(part.capitalize() for part in sub.replace("_", "-").split("-"))


def group_icon(group: str) -> str:
    return {
        "AI": "mdi:robot",
        "Apps": "mdi:apps",
        "Dev": "mdi:code-braces",
        "Finance": "mdi:cash",
        "Home": "mdi:home",
        "Media": "mdi:multimedia",
        "Ops": "mdi:tools",
        "Other": "mdi:application",
        "Printing": "mdi:printer-3d",
    }.get(group, "mdi:application")


def render_block() -> str:
    tiles = extract_ingress_tiles()
    lines = [
        BEGIN_MARK + " (regenerate via scripts/gen-ha-launcher.py)",
        *render_tiles_by_group(tiles),
        *render_inventory_section(),
        END_MARK,
    ]
    return "\n".join(lines) + "\n"


HEADER = """title: Launcher
path: launcher
icon: mdi:rocket-launch
type: sections
max_columns: 4
sections:
"""


def main() -> int:
    new_body = HEADER + render_block()

    if os.path.exists(LAUNCHER):
        with open(LAUNCHER) as fp:
            current = fp.read()
        if current == new_body:
            print("No changes.")
            return 0

    os.makedirs(os.path.dirname(LAUNCHER), exist_ok=True)
    with open(LAUNCHER, "w") as fp:
        fp.write(new_body)
    print(f"Updated {LAUNCHER}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
