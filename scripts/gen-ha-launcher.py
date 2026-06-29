#!/usr/bin/env python3
"""Regenerate the Home Assistant Launcher dashboard + site status from ingresses.

Walks every Ingress and Traefik IngressRoute under apps/, extracts the hostnames
and namespace, and walks ansible/inventory.yml for bare-metal hosts. Emits THREE
generated artifacts from that single source of truth:

  1. apps/home-assistant/config/dashboards/launcher.yaml
     A Lovelace `type: sections` view. Public domains get their own "Public"
     section (sorted first); private *.kblab.me services keep the namespace->group
     mapping. Every web tile is bound to a `binary_sensor.<slug>` status entity so
     the tile colours by up/down. Bare-metal tiles stay status-less.

  2. apps/home-assistant/config/packages/site_status.yaml
     One HA REST sensor that queries Prometheus `probe_success` once, exposing a
     `binary_sensor` per host (filtered by the blackbox target `instance` label).

  3. apps/monitoring/probe-sites.yaml (BEGIN/END_GENERATED_TARGETS region)
     The blackbox-exporter Probe target list (every https://<host>).

Generating all three together guarantees the probe target `instance`, the HA
sensor filter, and the tile's bound entity agree byte-for-byte.

Run after adding, renaming, or removing an Ingress/IngressRoute or inventory host.
The output is committed to git; Flux reconciles, the sync-managed-config init
container copies the HA files to the PVC on pod restart, and HA picks them up.

Opt out per Ingress/IngressRoute with:
    metadata:
      annotations:
        homepage.kblab.me/launcher: "false"

Classification: a host ending in `.kblab.me` (or the apex `kblab.me`) is private;
everything else is treated as a public/internet domain. `www.` hosts are skipped
as duplicates of their apex.
"""

import glob
import os
import re
import sys

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAUNCHER = os.path.join(REPO, "apps/home-assistant/config/dashboards/launcher.yaml")
STATUS_PKG = os.path.join(REPO, "apps/home-assistant/config/packages/site_status.yaml")
PROBE = os.path.join(REPO, "apps/monitoring/probe-sites.yaml")
INGRESS_GLOB = os.path.join(REPO, "apps/*/ingress*.yaml")
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")

BEGIN_MARK = "# BEGIN_GENERATED_LAUNCHER"
END_MARK = "# END_GENERATED_LAUNCHER"
PROBE_BEGIN = "# BEGIN_GENERATED_TARGETS"
PROBE_END = "# END_GENERATED_TARGETS"
PROBE_INDENT = " " * 6

# In-cluster Prometheus (same endpoint the cluster_metrics.yaml REST sensors use).
PROM = "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"

PUBLIC_GROUP = "Public"

# Host(`example.com`) inside a Traefik IngressRoute route match string.
HOST_RE = re.compile(r"Host\(`([^`]+)`\)")

# Namespace -> launcher group (private services only). Add as new namespaces land.
NAMESPACE_GROUP = {
    "actual-budget": "Finance",
    "ai-gateway": "AI",
    "ai-services": "AI",
    "apps": "Apps",
    "binks-api": "AI",
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

# Host subdomain -> icon. Fallback is mdi:web (public) or mdi:application (private).
HOST_ICON = {
    "actual-budget": "mdi:cash-multiple",
    "alertmanager": "mdi:bell-alert",
    "binks": "mdi:chat",
    "blacknbrownstudios": "mdi:palette",
    "cal": "mdi:calendar",
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
    "kblack": "mdi:code-tags",
    "kennethblack": "mdi:account-box",
    "llm": "mdi:robot",
    "neptune": "mdi:printer-3d",
    "openclaw": "mdi:robot-industrial",
    "photos": "mdi:image",
    "placemyparents": "mdi:account-group",
    "placemyparents-api": "mdi:api",
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


def slugify(host: str) -> str:
    """Mirror HA's entity_id slugify: lowercase, non-alnum runs -> `_`."""
    return re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")


def is_private(host: str) -> bool:
    return host.endswith(".kblab.me") or host == "kblab.me"


def iter_ingress_hosts():
    """Yield (host, namespace) from Ingress + IngressRoute, honoring the opt-out."""
    for path in sorted(glob.glob(INGRESS_GLOB)):
        with open(path) as fp:
            for doc in yaml.safe_load_all(fp):
                if not doc:
                    continue
                kind = doc.get("kind")
                meta = doc.get("metadata") or {}
                annotations = meta.get("annotations") or {}
                if annotations.get("homepage.kblab.me/launcher") == "false":
                    continue
                namespace = meta.get("namespace") or ""
                spec = doc.get("spec") or {}
                if kind == "Ingress":
                    for rule in spec.get("rules") or []:
                        host = rule.get("host")
                        if host:
                            yield host, namespace
                elif kind == "IngressRoute":
                    for route in spec.get("routes") or []:
                        for host in HOST_RE.findall(route.get("match") or ""):
                            yield host, namespace


def extract_tiles() -> list[dict]:
    """Return [{host, group, icon, public, slug, url, display, sensor}, ...]."""
    tiles: dict[str, dict] = {}
    for host, namespace in iter_ingress_hosts():
        if host in tiles or host.startswith("www."):
            continue
        public = not is_private(host)
        group = PUBLIC_GROUP if public else NAMESPACE_GROUP.get(namespace, "Other")
        subdomain = host.split(".", 1)[0]
        icon = HOST_ICON.get(subdomain, "mdi:web" if public else "mdi:application")
        tiles[host] = {
            "host": host,
            "group": group,
            "icon": icon,
            "public": public,
            "slug": slugify(host),
            "url": f"https://{host}",
            "display": host if public else pretty_name(host),
            "sensor": f"binary_sensor.{slugify(host)}",
        }
    return sorted(tiles.values(), key=lambda t: (t["group"], t["host"]))


def render_tiles_by_group(tiles: list[dict]) -> list[str]:
    """Render one Lovelace section per group; Public first, then alphabetical."""
    lines: list[str] = []
    groups: dict[str, list[dict]] = {}
    for tile in tiles:
        groups.setdefault(tile["group"], []).append(tile)

    order = ([PUBLIC_GROUP] if PUBLIC_GROUP in groups else []) + sorted(
        g for g in groups if g != PUBLIC_GROUP
    )
    for group in order:
        lines += [
            "  - type: grid",
            "    cards:",
            "      - type: heading",
            f"        heading: {group}",
            f"        icon: {group_icon(group)}",
        ]
        for tile in sorted(groups[group], key=lambda t: t["host"]):
            lines += [
                "      - type: tile",
                f"        entity: {tile['sensor']}",
                f"        name: {tile['display']}",
                f"        icon: {tile['icon']}",
                "        tap_action:",
                "          action: url",
                f"          url_path: {tile['url']}",
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
            if hostname in INVENTORY_TILES and hostname not in present:
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


def render_status_package(tiles: list[dict]) -> str:
    """Render packages/site_status.yaml: one REST sensor, one binary_sensor/host."""
    lines = [
        "# Generated by scripts/gen-ha-launcher.py -- do not edit by hand.",
        "# Per-site up/down from Prometheus blackbox-exporter (probe_success).",
        "# One Prometheus query feeds a binary_sensor per host, filtered by the",
        "# blackbox target `instance` label (https://<host>).",
        "rest:",
        f'  - resource: "{PROM}/api/v1/query?query=probe_success"',
        "    scan_interval: 60",
        "    binary_sensor:",
    ]
    for tile in sorted(tiles, key=lambda t: t["host"]):
        lines += [
            f'      - name: "{tile["host"]}"',
            f"        unique_id: site_up_{tile['slug']}",
            "        device_class: connectivity",
            "        value_template: >-",
            "          {% set r = value_json.data.result"
            f" | selectattr('metric.instance', 'eq', '{tile['url']}') | list %}}",
            "          {{ r | length > 0 and r[0].value[1] == '1' }}",
        ]
    return "\n".join(lines) + "\n"


def render_probe_block(tiles: list[dict]) -> str:
    """Render the BEGIN/END_GENERATED_TARGETS region for probe-sites.yaml."""
    lines = [
        f"{PROBE_INDENT}{PROBE_BEGIN} (regenerate via scripts/gen-ha-launcher.py)",
        f"{PROBE_INDENT}static:",
    ]
    for tile in sorted(tiles, key=lambda t: t["host"]):
        lines.append(f"{PROBE_INDENT}  - {tile['url']}")
    lines.append(f"{PROBE_INDENT}{PROBE_END}")
    return "\n".join(lines)


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
        "Public": "mdi:earth",
    }.get(group, "mdi:application")


def render_launcher(tiles: list[dict]) -> str:
    block = [
        BEGIN_MARK + " (regenerate via scripts/gen-ha-launcher.py)",
        *render_tiles_by_group(tiles),
        *render_inventory_section(),
        END_MARK,
    ]
    return HEADER + "\n".join(block) + "\n"


HEADER = """title: Launcher
path: launcher
icon: mdi:rocket-launch
type: sections
max_columns: 4
sections:
"""


def write_if_changed(path: str, content: str) -> bool:
    if os.path.exists(path):
        with open(path) as fp:
            if fp.read() == content:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fp:
        fp.write(content)
    return True


def update_probe_targets(tiles: list[dict]) -> bool:
    with open(PROBE) as fp:
        content = fp.read()
    pattern = re.compile(
        re.escape(PROBE_INDENT + PROBE_BEGIN) + r".*?" + re.escape(PROBE_END),
        re.DOTALL,
    )
    if not pattern.search(content):
        raise SystemExit(
            f"ERROR: markers {PROBE_BEGIN!r}/{PROBE_END!r} not found in {PROBE}"
        )
    new_content = pattern.sub(render_probe_block(tiles), content)
    if new_content == content:
        return False
    with open(PROBE, "w") as fp:
        fp.write(new_content)
    return True


def main() -> int:
    tiles = extract_tiles()
    site_tiles = tiles  # all ingress hosts get HTTP status

    changed = []
    if write_if_changed(LAUNCHER, render_launcher(tiles)):
        changed.append(LAUNCHER)
    if write_if_changed(STATUS_PKG, render_status_package(site_tiles)):
        changed.append(STATUS_PKG)
    if update_probe_targets(site_tiles):
        changed.append(PROBE)

    n_public = sum(1 for t in tiles if t["public"])
    n_private = len(tiles) - n_public
    if changed:
        for path in changed:
            print(f"Updated {os.path.relpath(path, REPO)}")
    else:
        print("No changes.")
    print(f"({n_public} public, {n_private} private hosts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
