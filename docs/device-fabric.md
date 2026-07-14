# Device Fabric

How every device in the homelab - Linux/Mac/Windows machines, Android wall/kiosk tablets, IoT/Pis - is **kept alive-visible and managed**, as one minimal, self-hosted, Google-free system.

> Scope: this is the **management + liveness** layer. What actually renders *on* a wall tablet (the HA dashboard + photo screensaver) is [`wall-panels.md`](./wall-panels.md). The two are complementary: device-fabric manages the tablet; wall-panels is its content.

## Philosophy

- **One shared liveness surface** for everything: the gatus `fleet` group. Every device answers "am I alive?" in the same place, and every machine's status bar renders one health glyph from it.
- **Management is per-platform and minimal** - only as much as each device type needs. No single heavyweight agent everywhere.
- **Self-hosted + Google-free.** No SaaS, no Android Enterprise / GMS. Runs on-LAN/Tailnet.
- **Ideas from FleetDM, not FleetDM.** We wanted a central registry + agents-report-in, not osquery live-query/compliance. FleetDM was trialed and is being decommissioned (see Status).

## The planes

| Plane | What it does | Tool | Where |
|-------|--------------|------|-------|
| **Liveness (all devices)** | "is it up?" - one shared health view | gatus `fleet` group + fleet-pulse | `apps/gatus/`, dotfiles `fleet-pulse` |
| **Computers** | liveness only (push heartbeats) | fleet-pulse pushers | dotfiles `~/.local/src/fleet-pulse/` |
| **Android** | MDM: enroll, deploy apps, kiosk | Headwind MDM (Community) | `apps/headwind-mdm/` |
| **Android -> liveness** | make tablets appear in `fleet` | headwind-fleet-bridge | `apps/headwind-fleet-bridge/` |
| **IoT / Pis** | metrics + liveness | mqtt2prom + Prometheus, gatus `iot-fleet` | `apps/mqtt2prom/`, `apps/iot-fleet/` |

## 1. Liveness: fleet-pulse + gatus

The spine. Each machine POSTs a token-authed heartbeat every 60s to a gatus **external-endpoint** in the `fleet` group:

```
POST https://status.kblab.me/api/v1/endpoints/fleet_<device>/external?success=true
Authorization: Bearer <FLEET_TOKEN>
```

Each machine's status bar (waybar/sketchybar) polls `/api/v1/endpoints/statuses`, filters the `fleet` group, and judges freshness **itself** - green if every device in `FLEET_ROSTER` reported success within 180s, amber if one is stale/never-reported, red if the API is unreachable. gatus does no server-side expiry; the reader's roster is the denominator (a configured-but-never-enrolled host shows amber, not silently-green).

- **Pushers:** `push.sh` (Linux systemd timer), launchd (Mac), Scheduled Task (Windows).
- **Per-machine config:** `~/.config/fleet-pulse/env` (`GATUS_BASE`, `FLEET_ROSTER`) + the token, from the `~/.dotfiles-private` overlay - never in the public repo.
- **Token:** one shared bearer, SOPS-encrypted in `apps/gatus/fleet-token-secret.sops.yaml`.
- Full detail: `~/.dotfiles/.local/src/fleet-pulse/README.md`.

## 2. Android management: Headwind MDM

Self-hosted [Headwind MDM](https://h-mdm.com) Community at `https://mdm.kblab.me` (LAN/Tailnet-only). **Google-free**: its own MQTT command channel + Android **device-owner** provisioning - no Android Enterprise, no Google account on the device.

- Enroll: factory reset -> tap 6-7x -> scan the Headwind QR (from **Configurations -> QR icon**) -> agent installs as Device Owner. No root.
- **Kiosk lockdown comes from a kiosk app, not Headwind** (Community has no built-in COSU kiosk - that's a paid tier). Use **[FreeKiosk](https://freekiosk.app/)** (free, open-source, uses the Android Device Owner API, HA REST API) - it pairs with Headwind's device-owner enrollment. (Fully Kiosk works too but paywalls hard lockdown at ~EUR 8/device.)
- DB: shared cluster Postgres (`hmdm` db), bootstrapped by `apps/postgres/hmdm-db-bootstrap-job.yaml`; the app waits on an initContainer so apply-order can't crash-loop it.
- Full detail + enrollment runbook: `apps/headwind-mdm/README.md`.

## 3. Bridge: Android -> the fleet liveness view

Android devices don't push heartbeats themselves, so `headwind-fleet-bridge` (a long-running pod in the `headwind-mdm` namespace) reads Headwind's `devices` table every 60s and pushes `fleet_<device>` heartbeats to gatus - putting tablets in the SAME `fleet` health view as the computers. Read-only; tolerates a gatus 404 for not-yet-declared devices.

Adding an enrolled tablet to the view = two edits:
1. `apps/gatus/configmap.yaml` - an `external-endpoints` entry (`group: fleet`, `name: <device>`).
2. `FLEET_ROSTER` in `~/.config/fleet-pulse/env` on each reader machine.

Full detail: `apps/headwind-fleet-bridge/README.md`.

## Decisions (the why)

- **Google-free Android** - device-owner + Headwind's own MQTT, never Android Enterprise. Keeps tablets on our infra; the cost is no managed Play Store (apps come as APKs).
- **Not FleetDM** - its value is osquery inventory/live-query/compliance; we only wanted liveness + light Android MDM. Running it empty wasn't worth the MySQL+Redis. Decommission tracked below.
- **Kiosk via app, not MDM** - Headwind Community has no COSU; FreeKiosk gives free device-owner lockdown, so no paid tier needed.
- **LAN/Tailnet-only** - wall tablets are on-LAN; `monitoring-local-network-only` middleware. A public toggle is documented (crowdsec bouncer) but off; a roaming personal phone would need it or Headscale.
- **One shared token, per-device endpoints, roster is the denominator** - simple, low-blast-radius, and a never-enrolled device is visible as amber instead of hiding.

## Registry conventions

- **Network identity:** `infrastructure/dhcp/devices.yaml` (Android tablets = `mobile` range `.50-.79`).
- **Liveness:** a gatus `fleet` external-endpoint (push) or an `iot-fleet`/`mac-machines` probe (pull), plus `FLEET_ROSTER` on readers.
- **Meta-index:** `docs/homelab-catalog.md` ("where does X run, who reconciles it").

## Status (2026-07-14)

| Piece | State |
|-------|-------|
| fleet-pulse liveness | **live** (Linux pushing; Mac/Windows pushers authored, apply per-host) |
| gatus `fleet` group | **live** (`linux-cachyos`/`mac`/`windows` declared) |
| Headwind MDM | **deployed**, LAN-only, awaiting first tablet enrollment |
| headwind-fleet-bridge | **deployed**, idle until a device syncs |
| FleetDM | **being decommissioned** (was deployed, empty) - see Phase 4 in the plan |

## Component map

```
apps/gatus/                    liveness surface (fleet group + token)
apps/headwind-mdm/             Android MDM server (+ hmdm DB bootstrap in apps/postgres/)
apps/headwind-fleet-bridge/    Android -> gatus liveness bridge
~/.dotfiles/.local/src/fleet-pulse/   the pushers + per-machine config
docs/wall-panels.md            what renders ON a wall tablet (HA dashboard + screensaver)
```
