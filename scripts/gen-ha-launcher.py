#!/usr/bin/env python3
"""Regenerate the kblab.me front door + site status from ingresses.

Walks every Ingress and Traefik IngressRoute under apps/, extracts the hostnames
and namespace, and walks ansible/inventory.yml for bare-metal hosts. Emits THREE
generated artifacts from that single source of truth:

  1. apps/front-door/index.html
     The page https://kblab.me serves: every site grouped by category on the
     web-kit shell (apps/front-door/kit.lock). Public domains get their own
     "Public" group (first); private *.kblab.me services are grouped by
     namespace (NAMESPACE_GROUP, which must name every namespace); the bare-metal
     hosts come last. Live up/down is status.kblab.me, so the page has none.

  2. apps/home-assistant/config/packages/site_status.yaml
     One HA REST sensor that queries Prometheus `probe_success` once, exposing a
     `binary_sensor` per host (filtered by the blackbox target `instance` label).

  3. apps/monitoring/probe-sites.yaml (BEGIN/END_GENERATED_TARGETS region)
     The blackbox-exporter Probe target list (every https://<host>).

Generating all three together guarantees the page, the probe target `instance`
and the HA sensor filter list the same hosts.

Run after adding, renaming, or removing an Ingress/IngressRoute or inventory host.
`--list-sites` writes nothing and prints the private sites as `name<TAB>group<TAB>url`,
for desktop launchers (dotfiles kblab-menu) to read this same source.
The output is committed to git; Flux reconciles, the front-door pod rolls on the
new ConfigMap, and HA's sync-managed-config init container copies the status
package to the PVC on pod restart.

Only apps listed in apps/kustomization.yaml are read: an ingress Flux never
applies has no site behind it.

Opt out per Ingress/IngressRoute with:
    metadata:
      annotations:
        homepage.kblab.me/launcher: "false"

Classification: a host ending in `.kblab.me` (or the apex `kblab.me`) is private;
everything else is treated as a public/internet domain. `www.` hosts are skipped
as duplicates of their apex.
"""

import glob
import html
import os
import re
import sys

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PAGE = os.path.join(REPO, "apps/front-door/index.html")
STATUS_PKG = os.path.join(REPO, "apps/home-assistant/config/packages/site_status.yaml")
PROBE = os.path.join(REPO, "apps/monitoring/probe-sites.yaml")
INGRESS_GLOB = os.path.join(REPO, "apps/*/ingress*.yaml")
APPS_KUSTOMIZATION = os.path.join(REPO, "apps/kustomization.yaml")
INVENTORY = os.path.join(REPO, "ansible/inventory.yml")

PROBE_BEGIN = "# BEGIN_GENERATED_TARGETS"
PROBE_END = "# END_GENERATED_TARGETS"
PROBE_INDENT = " " * 6

# In-cluster Prometheus (same endpoint the cluster_metrics.yaml REST sensors use).
PROM = "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"

PUBLIC_GROUP = "Public"

# Host(`example.com`) inside a Traefik IngressRoute route match string.
HOST_RE = re.compile(r"Host\(`([^`]+)`\)")

# Namespace -> group (private services only). Every namespace with a private
# host must be here: an unmapped one stops the generator, so nothing lands
# uncategorized.
NAMESPACE_GROUP = {
    "actual-budget": "Finance",
    "agents": "Agents",
    "ai-gateway": "AI",
    "apps": "Apps",
    "binks-api": "AI",
    "comfyui": "AI",
    "forgejo": "Dev",
    "frigate": "Home",
    "freqtrade": "Finance",
    "gatus": "Ops",
    "gatus-fleet": "Ops",
    "headscale": "Ops",
    "headwind-mdm": "Home",
    "home-assistant": "Home",
    "immich": "Media",
    "jellyfin": "Media",
    "karakeep": "Apps",
    "litellm": "AI",
    "memory": "Agents",
    "minio": "Ops",
    "monitoring": "Ops",
    "mqtt2prom": "Ops",
    "music-assistant": "Media",
    "neptune": "Printing",
    "notes-sync-bridge": "Apps",
    "ntfy": "Ops",
    "orcaslicer": "Printing",
    "placemyparents-preview": "Dev",
    "scratch": "Apps",
    "semaphore": "Ops",
    "snapcast": "Media",
    "vaultwarden": "Apps",
}

BARE_METAL_GROUP = "Bare metal"

# Inventory host -> (label, url). The URL is the admin UI or node_exporter
# metrics. Macs and Pis are metrics-only today.
INVENTORY_TILES = {
    "hp-victus":   ("HP Victus",   "http://192.168.1.243:9100/metrics"),
    "asus-laptop": ("Asus Laptop", "http://192.168.1.152:9100/metrics"),
    "mac-mini":    ("Mac Mini",    "http://192.168.1.7:9100/metrics"),
    "pi5-master":  ("Pi5 Master",  "http://192.168.1.20:9100/metrics"),
    "pi5-worker1": ("Pi5 Worker1", "http://192.168.1.21:9100/metrics"),
    "pi5-worker2": ("Pi5 Worker2", "http://192.168.1.22:9100/metrics"),
    "pi5-worker3": ("Pi5 Worker3", "http://192.168.1.23:9100/metrics"),
    "pi4-worker4": ("Pi4 Worker4", "http://192.168.1.24:9100/metrics"),
    "pi4-worker5": ("Pi4 Worker5", "http://192.168.1.124:9100/metrics"),
    "openwrt-router": ("OpenWrt",  "http://192.168.1.1/"),
}


def slugify(host: str) -> str:
    """Mirror HA's entity_id slugify: lowercase, non-alnum runs -> `_`."""
    return re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")


def is_private(host: str) -> bool:
    return host.endswith(".kblab.me") or host == "kblab.me"


def flux_apps() -> set[str]:
    """App dirs Flux deploys: the resources of apps/kustomization.yaml.

    An ingress file under an app that is not listed there is never applied, so
    probing its host only produces a permanent PublicSiteDown.
    """
    with open(APPS_KUSTOMIZATION) as fp:
        return set(yaml.safe_load(fp).get("resources") or [])


def iter_ingress_hosts():
    """Yield (host, namespace) from Ingress + IngressRoute, honoring the opt-out."""
    deployed = flux_apps()
    for path in sorted(glob.glob(INGRESS_GLOB)):
        if os.path.basename(os.path.dirname(path)) not in deployed:
            continue
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
    """Return [{host, group, public, slug, url, display}, ...]."""
    tiles: dict[str, dict] = {}
    for host, namespace in iter_ingress_hosts():
        if host in tiles or host.startswith("www."):
            continue
        public = not is_private(host)
        if not public and namespace not in NAMESPACE_GROUP:
            raise SystemExit(
                f"ERROR: {host} is in namespace {namespace!r}, which NAMESPACE_GROUP"
                " does not name; add it to a group"
            )
        tiles[host] = {
            "host": host,
            "group": PUBLIC_GROUP if public else NAMESPACE_GROUP[namespace],
            "public": public,
            "slug": slugify(host),
            "url": f"https://{host}",
            "display": host if public else pretty_name(host),
        }
    return sorted(tiles.values(), key=lambda t: (t["group"], t["host"]))


def inventory_tiles() -> list[dict]:
    """The bare-metal hosts in ansible/inventory.yml that INVENTORY_TILES names."""
    with open(INVENTORY) as fp:
        inv = yaml.safe_load(fp)
    present: list[str] = []
    children = (inv.get("all") or {}).get("children") or {}
    for group in children.values():
        for hostname in (group.get("hosts") or {}).keys():
            if hostname in INVENTORY_TILES and hostname not in present:
                present.append(hostname)
    return [
        {"display": INVENTORY_TILES[h][0], "url": INVENTORY_TILES[h][1],
         "host": INVENTORY_TILES[h][1].split("//", 1)[1].rstrip("/")}
        for h in sorted(present)
    ]


def grouped(tiles: list[dict]) -> list[tuple[str, list[dict]]]:
    """(group, tiles) in page order: Public, the rest alphabetical, bare metal last."""
    groups: dict[str, list[dict]] = {}
    for tile in tiles:
        groups.setdefault(tile["group"], []).append(tile)
    order = ([PUBLIC_GROUP] if PUBLIC_GROUP in groups else []) + sorted(
        g for g in groups if g != PUBLIC_GROUP
    )
    out = [(g, sorted(groups[g], key=lambda t: t["host"])) for g in order]
    metal = inventory_tiles()
    if metal:
        out.append((BARE_METAL_GROUP, metal))
    return out


def anchor(group: str) -> str:
    return slugify(group).replace("_", "-")


def render_page(tiles: list[dict]) -> str:
    """The front door: web-kit's <kb-shell> with one sidebar link per group, and
    one list per group: each name a link over its host (none when they match)."""
    sections = grouped(tiles)
    nav = "\n".join(
        f'    <a href="#{anchor(g)}">{esc(g)}</a>' for g, _ in sections
    )
    body = []
    for group, rows in sections:
        body.append(
            f'  <section id="{anchor(group)}">\n'
            f'    <h2>{esc(group)} <span class="n">{len(rows)}</span></h2>\n'
            "    <ul>"
        )
        for t in rows:
            host = "" if t["host"] == t["display"] else f"<span>{esc(t['host'])}</span>"
            body.append(f'      <li><a href="{esc(t["url"])}">{esc(t["display"])}</a>{host}</li>')
        body.append("    </ul>\n  </section>")
    return PAGE_TEMPLATE.format(
        count=sum(len(r) for _, r in sections), nav=nav, body="\n".join(body)
    )


def esc(text: str) -> str:
    return html.escape(text, quote=True)


# Only what web-kit lacks: the list rows. Colour, type and the shell come from
# the kit (tokens.css, shell.css); spacing is on the 4px grid (ui.md rule 10).
PAGE_TEMPLATE = """<!doctype html>
<!-- Generated by scripts/gen-ha-launcher.py; do not edit by hand. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kblab</title>
<link rel="stylesheet" href="/kit/tokens.css">
<link rel="stylesheet" href="/kit/shell.css">
<script type="module" src="/kit/kb-shell.js"></script>
<style>
  .kb-main {{ padding: 16px 24px 48px; }}
  .head {{ display: flex; align-items: baseline; justify-content: space-between; }}
  h1 {{ font-size: var(--fs-lg); margin: 0; }}
  h2 {{ font-size: var(--fs-md); font-weight: 600; margin: 24px 0 4px; }}
  .n {{ color: var(--muted); font-weight: 400; }}
  section {{ scroll-margin-top: calc(var(--head) + 8px); }}
  ul {{ list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); column-gap: 24px; }}
  li {{ padding: 6px 0; border-bottom: 1px solid var(--line); min-width: 0; }}
  li > * {{ display: block; overflow-wrap: anywhere; }}
  li span {{ font-family: var(--mono); font-size: var(--fs-sm); color: var(--muted); }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<kb-shell>
  <a class="kb-brand" href="/">kblab</a>
  <nav class="kb-nav" aria-label="Groups">
    <div class="kb-group">Systems</div>
{nav}
  </nav>
</kb-shell>
<main class="kb-main">
  <div class="head"><h1>Systems <span class="n">{count}</span></h1><a href="https://status.kblab.me">Status</a></div>
{body}
</main>
</body>
</html>
"""


def render_status_package(tiles: list[dict]) -> str:
    """Render packages/site_status.yaml: one REST sensor, one binary_sensor/host."""
    lines = [
        "# Generated by scripts/gen-ha-launcher.py -- do not edit by hand.",
        "# Per-site up/down from Prometheus blackbox-exporter.",
        "# One Prometheus query feeds a binary_sensor per host, filtered by the",
        "# blackbox target `instance` label (https://<host>). 'up' means the",
        "# endpoint answered with an HTTP status < 500 (0 = unreachable / DNS",
        "# failure = down), matching the gatus `[STATUS] < 500` convention so a",
        "# path-only service that 404s on `/` still reads up.",
        "rest:",
        f'  - resource: "{PROM}/api/v1/query?query=probe_http_status_code"',
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
            "          {% set c = (r[0].value[1] | int(0)) if r | length > 0 else 0 %}",
            "          {{ 0 < c < 500 }}",
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
    if "--list-sites" in sys.argv[1:]:
        for tile in tiles:
            if not tile["public"]:
                print(f'{tile["display"]}\t{tile["group"]}\t{tile["url"]}')
        return 0

    site_tiles = tiles  # all ingress hosts get HTTP status

    changed = []
    if write_if_changed(PAGE, render_page(tiles)):
        changed.append(PAGE)
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
