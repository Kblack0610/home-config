# Home Assistant on K3s

Home Assistant deployment for the home K3s cluster with Git-managed dashboards and homelab monitoring.

## Access

| Method | URL |
|--------|-----|
| Canonical | `https://hass.kblab.me` |
| Apex redirect | `https://kblab.me` → 307 → `https://hass.kblab.me/lovelace/launcher` (Traefik `kblab-apex-redirect` Middleware on the `home-assistant-apex` Ingress) |
| Port Forward | `kubectl port-forward -n home-assistant svc/home-assistant 8123:8123` then `http://localhost:8123` |

### DNS Setup

Add DNS rewrite in AdGuard Home (Pi 3) at **Filters → DNS Rewrites**:
- Domain: `hass.kblab.me`
- Answer: `192.168.1.124`

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Local Access                                                     │
│   Browser → AdGuard DNS → Traefik (192.168.1.124) → HA Pod       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Cluster Monitoring                                               │
│   HA Pod → Prometheus API (monitoring.svc:9090) → REST Sensors   │
└─────────────────────────────────────────────────────────────────┘
```

## Layout

Repo-managed Home Assistant files live under `apps/home-assistant/config/`:

- `configuration.yaml`: base HA config, proxies, Lovelace mode
- `packages/`: Prometheus-backed REST sensors and derived template sensors
- `ui-lovelace.yaml`: default dashboard definition
- `dashboards/`: views in tab order — `Launcher` (auto-generated tile grid, default landing), `Overview`, `Ops`, `Admin`, `3D Printing`
- `automations.yaml`, `scripts.yaml`, `scenes.yaml`: checked-in defaults so a fresh PVC can boot cleanly

These files are bundled into a generated ConfigMap and synced into the HA PVC on each rollout. Runtime state such as `.storage` stays on the PVC and is not repo-managed.

Custom components (e.g. `ha-bambulab`) are installed via init containers with pinned versions. Integration config entries are seeded from K8s Secrets into `.storage/core.config_entries` on first boot.

## Features

### Homelab Monitoring

| Use Case | Description |
|----------|-------------|
| Cluster status | CPU, RAM, and disk sensors for nodes 1-5 |
| Service health | Home Assistant, Traefik, and LiteLLM container metrics |
| Mac monitoring | Mac Studio and Mac Mini node exporter metrics |
| AI endpoints | MLX model endpoint model counts |

### Launcher (auto-generated)

The first tab, `Launcher`, is an auto-generated tile grid that aggregates every ingress under `apps/*/ingress*.yaml` plus every bare-metal host in `ansible/inventory.yml`.

- Generator: [`scripts/gen-ha-launcher.py`](../../scripts/gen-ha-launcher.py) (mirrors `scripts/gen-gatus-ingress-checks.py` — same source-of-truth + marker pattern).
- Output: `config/dashboards/launcher.yaml` between `# BEGIN_GENERATED_LAUNCHER` / `# END_GENERATED_LAUNCHER` markers. File header (`title`, `path`, `icon`, `max_columns`) is preserved across regenerations.
- Grouping: namespace → group via the `NAMESPACE_GROUP` dict at the top of the script. Unknown namespaces fall back to `Other`.
- Icons: host prefix → MDI icon via the `HOST_ICON` dict. Unknown hosts fall back to `mdi:application`.
- Bare-metal hosts: an `INVENTORY_TILES` dict maps each Ansible inventory host to a `(label, URL, icon)` tuple — edit to add a new bare-metal target.
- Opt out per Ingress: `metadata.annotations.homepage.kblab.me/launcher: "false"` (matches the `gatus.kblab.me/monitor: "false"` convention).

When to regenerate: after any `apps/*/ingress*.yaml` or `ansible/inventory.yml` change that affects tiles. The script is idempotent — a no-op run reports `No changes.`.

```bash
./scripts/gen-ha-launcher.py
git add apps/home-assistant/config/dashboards/launcher.yaml
```

Flux picks up the ConfigMap change → deployment hash rotates → pod rolls → the `sync-managed-config` init container copies `dashboard_launcher.yaml` to `/config/dashboards/launcher.yaml` on the PVC → HA serves the new view on next render.

### 3D Printer Monitoring

| Use Case | Description |
|----------|-------------|
| Neptune (Moonraker) | REST sensors polling Moonraker through the in-cluster `neptune` service — temps, progress, print state |
| Bambu A1 (ha-bambulab) | Custom component installed via init container, config seeded from K8s Secret |
| Dashboard | Dedicated `3D Printing` view with status, progress gauges, and temperature cards |

### Robot Vacuum (Tapo RV30)

| Use Case | Description |
|----------|-------------|
| Tapo RV30 (`tapo_rv30`) | Local-only (TPAP/SPAKE2+) reverse-engineered custom component ([`epg-pers/tapo-rv30-ha`](https://github.com/epg-pers/tapo-rv30-ha) pinned at `v0.3.0`), installed via the `install-tapo-rv30` init container, config seeded from K8s Secret by `seed-tapo-rv30`. Exposes a `vacuum.*` entity (start/pause/stop/dock), fan-speed / water-level / clean-passes selectors, battery + error + consumable sensors, a live map image, and the `tapo_rv30.clean_rooms` service for per-room cleaning. |
| Credentials | `tapo-rv30-secret.yaml` (SOPS) — `host` (RV30 LAN IP), `username` + `password` (TP-Link account). Runtime is LAN-only; the account creds are only used in the SPAKE2+ handshake. |
| ⚠️ Firmware | This integration speaks the **TPAP/SPAKE2+** protocol on port `4433` — the scheme firmware `1.3.x` *switched to*, so it targets current firmware (confirmed through `1.3.2`; this RV is on `1.3.3`). What `1.3.x` broke is the **official `tplink`** integration's older protocol, which we deliberately don't use. The real risk is a *future* firmware that changes the protocol again — disable auto-update to stay on a known-good build. Fallback: Matter (start/stop/dock only, no room cleaning). |

### Litter Box (Fumoi / LocalTuya)

| Use Case | Description |
|----------|-------------|
| Fumoi "Cat Litter Box M4" (`localtuya`) | Generic **Tuya** device controlled fully on-LAN via **LocalTuya** ([`xZetsubou/hass-localtuya`](https://github.com/xZetsubou/hass-localtuya) pinned `2025.11.0`), installed via the `install-localtuya` init container. Discovered: Tuya category `msp`, protocol **v3.5**, IP `192.168.1.215`. Exposes `switch` (power), `select` (work mode), `button` (start / manual-clean), `number` (clean delay), and status/counter sensors. |
| Credentials | `localtuya-secret.yaml` (SOPS) — `device-id`, `local-key`, `host`, `protocol-version`. Extracted via the reusable Tuya recipe in [`docs/smart-home-control.md`](../../docs/smart-home-control.md); keys stay LAN-only after discovery. |
| Config entry | Seeded by the `seed-localtuya` init container as a `no_cloud` hub entry (`localtuya` domain, `ENTRIES_VERSION=4`). Unlike Bambu/Tapo (flat 3-field entries) it's a **nested, versioned structure with a per-DP entity map**, authored against `config_flow.py` @ 2025.11.0 (per-platform required keys) + validated locally, not blind-seeded. **Live-verified 2026-07-01**: all 10 entities report real device values over LAN. |
| ⚠️ Gotcha | `localtuya/__init__.py` reads `region`/`client_id`/`client_secret`/`user_id` **unconditionally** (before the `no_cloud` branch), so the seed includes those keys empty — omit them and setup `KeyError`s. Read-only sensor labels (DP 6/7/8/9/110 → Counter A/B, Litter Level, Bin Full, Status) are **provisional** pending a live clean-cycle confirmation; `manual_clean`/`factory_reset` are omitted (unknown/destructive DP id). |

## Deployment

```bash
# Render locally
kubectl kustomize apps/home-assistant

# Deploy all resources directly
kubectl apply -k apps/home-assistant/

# Render the Flux entrypoint used by the live cluster
kubectl kustomize infra/flux/apps/prod
```

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | Creates `home-assistant` namespace |
| `pvc.yaml` | Persistent volume for HA config |
| `deployment.yaml` | Main HA deployment with managed config sync init container |
| `service.yaml` | ClusterIP service on port 8123 |
| `ingress.yaml` | Traefik-routed Ingress for `hass.kblab.me` |
| `ingress-apex.yaml` | Second Ingress on `kblab.me` apex that routes to this same Service; paired with `middleware-apex-redirect.yaml` so hitting the apex rewrites to `/lovelace/launcher` |
| `middleware-apex-redirect.yaml` | Traefik `Middleware` (RedirectRegex) used by `ingress-apex.yaml` |
| `kustomization.yaml` | Generates the managed config ConfigMap from checked-in files |

## Configuration

### Trusted Proxies

Home Assistant requires `trusted_proxies` config to work behind Traefik on K3s:

```yaml
# In /config/configuration.yaml (inside the pod)
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 10.42.0.0/16    # K3s pod network
    - 10.43.0.0/16    # K3s service network
    - 192.168.1.0/24  # Local network
```

### Managed Config Sync

The deployment copies repo-managed files into `/config` on every rollout:

```yaml
# Base config
/config/configuration.yaml

# Package files
/config/packages/*.yaml

# Dashboard files
/config/ui-lovelace.yaml
/config/dashboards/*.yaml
```

### Storage

Uses the K3s `local-path` storage class for the Home Assistant PVC.

## Troubleshooting

### Can't access via hass.kblab.me

1. Check DNS rewrite exists in AdGuard
2. Verify your device uses AdGuard as DNS
3. Test with: `nslookup hass.kblab.me`

### 400 Bad Request

Add `trusted_proxies` to HA configuration (see above).

### Check pod status

```bash
kubectl get pods -n home-assistant
kubectl logs -n home-assistant -l app.kubernetes.io/name=home-assistant
kubectl logs -n home-assistant deploy/home-assistant -c sync-managed-config
```

### Restart Home Assistant

```bash
kubectl rollout restart deployment home-assistant -n home-assistant
```

## Code-First Policy

All Home Assistant changes are managed through this git repository. No manual UI configuration.

- **Dashboards**: YAML-mode Lovelace, defined in `config/dashboards/`
- **Sensors**: REST platform in `config/packages/` — no config-flow integrations for new sensors
- **Custom components**: Installed via init containers with pinned versions, not HACS
- **Integration config**: Seeded into `.storage/core.config_entries` from K8s Secrets on first boot
- **Credentials**: Stored in SOPS-encrypted K8s Secrets (age key)
- **Runtime state**: `.storage/` persists on PVC — the only non-git-managed state

When adding a new device or integration:
1. Prefer HA's built-in REST/MQTT/template platforms (100% YAML)
2. If a custom component is required, add an init container to download a pinned release
3. Seed the config entry from a K8s Secret if the integration is config-flow-only
4. Never install HACS or use the HA UI for configuration

## Related Services

| Service | Purpose |
|---------|---------|
| Prometheus | Metrics source (kube-prometheus-stack) |
| Traefik | Ingress controller |
| AdGuard Home | DNS server (on Pi 3) |
| Frigate | NVR for cameras |
