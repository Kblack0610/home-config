# headwind-mdm (Headwind MDM, Community)

Self-hosted [Headwind MDM](https://h-mdm.com) (HMDM) - open-source Android device management. This is the management plane for **Android** devices in the homelab (wall/kiosk tablets first, personal phones later). Computers are covered by fleet-pulse heartbeats (gatus `fleet` group), not here.

- **URL:** https://mdm.kblab.me (**LAN/Tailscale-only** - see `ingress.yaml`)
- **Image:** `headwindmdm/hmdm:0.1.8` (Tomcat wrapper); server WAR pinned via `HMDM_URL` (`hmdm-5.39.2-os.war`). `HMDM_VARIANT=os` = the free Community edition.
- **No Google dependency:** Community uses its own MQTT command channel + device-owner provisioning. No Android Enterprise / GMS / managed Play Store.

## Architecture

One `headwind-mdm` Deployment (Tomcat, `replicas: 1`, `Recreate`) + the shared cluster Postgres:

| Component | Detail |
|-----------|--------|
| server | `headwindmdm/hmdm`, port 8080, work dir on PVC `headwind-mdm-work` (2Gi, local-path) |
| database | shared Postgres (`postgres.databases.svc`), db `hmdm`, role `hmdm` |

DB provisioning: `apps/postgres/hmdm-db-bootstrap-job.yaml` (namespace `databases`) creates the `hmdm` role + database idempotently. Its password comes from `hmdm-db-secret` (SOPS) - the SAME value as `headwind-mdm-secret/sql-pass`. The app Deployment has an initContainer (`wait-for-hmdm-db`) that blocks until the role+db exist, so there's no cold-apply crash loop regardless of Flux apply order.

## Enrolling a device (kiosk tablet)

Headwind Community has **no built-in kiosk/COSU mode** (that's a paid tier). Kiosk lockdown comes from a **kiosk app** deployed *through* Headwind:

1. In the admin panel, generate an enrollment QR.
2. Factory-reset the tablet, tap 7x on the welcome screen, scan the QR - the Headwind agent installs as **device owner** (no Google account needed).
3. Push a kiosk app to the device (open-source **WallPanel**, or Fully Kiosk free) and point it at the Home Assistant dashboard URL; set launch-on-boot + keep-awake.

## Liveness -> gatus

Android devices don't push heartbeats themselves. A server-side bridge (`apps/headwind-fleet-bridge/`) polls Headwind's device API and pushes a `fleet_<device>` heartbeat to gatus, so tablets appear in the same `fleet` health group as the computers.

## Secrets (SOPS + age)

| Secret (ns) | Keys | Purpose |
|-------------|------|---------|
| `headwind-mdm-secret` (headwind-mdm) | `sql-pass`, `shared-secret` | app DB password + HMDM API shared secret |
| `hmdm-db-secret` (databases) | `hmdm-db-pass` | same DB password, for the bootstrap Job |

Both are SOPS/age encrypted. When rotating the DB password, update BOTH (they must match). Edit with `sops <file>` (needs `~/.config/sops/age/keys.txt`).

## Exposure

LAN/Tailnet-only via `monitoring-local-network-only@kubernetescrd` (RFC1918). Wall tablets are on the LAN, so they enroll + sync fine. To allow off-net enrollment (a roaming phone), swap to the crowdsec public bouncer + a Cloudflare tunnel route (see the comment in `ingress.yaml`). Headscale tailnet clients are `100.64.0.0/10` - extend the allowlist CIDRs if you want tailnet admin access.

## Verify

```bash
kubectl --context home-k3s -n headwind-mdm get pods           # Running (after initContainer clears)
kubectl --context home-k3s -n databases get job hmdm-db-bootstrap
curl -sk https://mdm.kblab.me/ -o /dev/null -w '%{http_code}\n'   # on-LAN; blocked off-LAN
```
