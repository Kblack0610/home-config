# fleet (FleetDM)

Self-hosted [FleetDM](https://fleetdm.com) - the osquery-based fleet manager. This is the single inventory / live-query / compliance pane for every **computer** in the homelab (macOS + x86 Linux + Raspberry Pi k3s nodes). Android tablets are managed separately by `apps/headwind-mdm`; this app does not touch them.

- **URL:** https://fleet.kblab.me (LAN/Tailscale-only - see `ingress.yaml`)
- **Image:** `fleetdm/fleet:v4.88.0` (bump the tag in `deployment.yaml` to upgrade; run `fleet prepare db` migrations happen automatically via the init container)
- **Agents:** hosts enroll via the `osquery-agent` Ansible role (`ansible/roles/osquery-agent/`), not by hand.

## Architecture

Three workloads in the `fleet` namespace:

| Component | Image | State | Placement |
|-----------|-------|-------|-----------|
| `fleet` server | `fleetdm/fleet` | stateless | anywhere (scheduler's choice) |
| `fleet-mysql` | `mysql:8.0` | `fleet-mysql-data` PVC (8Gi, local-path) | **pinned off asus-laptop** |
| `fleet-redis` | `redis:7-alpine` | ephemeral (live-query pub/sub + cache) | **pinned off asus-laptop** |

FleetDM requires MySQL 8 + Redis; neither existed in-cluster, so both are deployed here (the shared Postgres does not apply). The server is stateless - all data lives in MySQL/Redis.

> **Storage is interim.** This cluster has no replicated block storage yet (local-path only), so MySQL is node-pinned like every other stateful app - here onto a labelled non-asus node. That trades the asus SPOF for an hp-victus one. The durable fix is Longhorn: `docs/distributed-storage-roadmap.md` makes Fleet its first tenant (Phase 3), after which this pin becomes `storageClassName: longhorn`.

## Required one-time setup

### 1. Pick a datastore node (REQUIRED before first deploy)

The MySQL + Redis pods carry `nodeSelector: fleet.storage/node: "true"` so they can **never** land on `asus-laptop` (the storage SPOF that hit DiskPressure on 2026-07-09 and took the cluster down). Label exactly one stable, non-asus node with spare disk:

```bash
kubectl --context home-k3s label node <node-name> fleet.storage/node=true
```

Until that label exists, `fleet-mysql`/`fleet-redis` stay `Pending` by design (fail-safe, never on asus).

### 2. Deploy

```bash
git push forgejo <branch>            # canonical remote; github is auto-mirrored
flux reconcile kustomization apps --with-source
kubectl --context home-k3s -n fleet get pods -o wide   # confirm datastores are NOT on asus-laptop
```

### 3. Bootstrap the admin + enroll secret

Browse to https://fleet.kblab.me and create the initial admin (email `hughlio912@gmail.com`). Then copy the **osquery enroll secret** (Settings -> generated on setup) and store it as `vault_fleet_enroll_secret` in the relevant `ansible/group_vars/<group>/vault.yml` so the `osquery-agent` role can enroll hosts. The enroll secret lives in Fleet's DB, not in this app's Secret.

## Secrets (`secret.sops.yaml`, SOPS+age)

| Key | Purpose |
|-----|---------|
| `mysql-password` | Fleet's MySQL app-user password (`FLEET_MYSQL_PASSWORD` + `MYSQL_PASSWORD`) |
| `mysql-root-password` | MySQL root (image init only) |
| `fleet-server-private-key` | Fleet at-rest encryption key (`FLEET_SERVER_PRIVATE_KEY`, >=32 bytes). Durable - rotating it invalidates already-encrypted stored secrets. |

Edit with: `sops apps/fleet/secret.sops.yaml` (needs `~/.config/sops/age/keys.txt`). Flux decrypts via the `sops-age` secretRef on the `apps` Kustomization.

## Exposure

Defaults to LAN/Tailnet-only (`monitoring-local-network-only@kubernetescrd`). All managed hosts are on the LAN or Headscale, so this is sufficient. To enroll hosts that leave the network, switch `ingress.yaml` to the crowdsec public bouncer and add a Cloudflare tunnel route (see the comment in `ingress.yaml`).

## Verify

```bash
kubectl --context home-k3s -n fleet get pods            # fleet + fleet-mysql + fleet-redis Running
curl -sk https://fleet.kblab.me/healthz                 # 200
# after enrolling hosts, run a live query in the UI:  SELECT * FROM system_info;
```
