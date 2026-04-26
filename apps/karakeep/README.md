# Karakeep

Self-hosted bookmark / read-later manager. Replaces the local-first Firefox `Stash` extension with a cross-device service (web + native iOS + native Android + browser extensions).

- Upstream: <https://github.com/karakeep-app/karakeep>
- Docs: <https://docs.karakeep.app>
- Mobile apps: iOS App Store, Google Play
- Pinned to **`v0.31.0`** (Feb 2026). Bump in `web-deployment.yaml`.

## Architecture

| Component | Image | Storage | Purpose |
|---|---|---|---|
| `karakeep-web` | `ghcr.io/karakeep-app/karakeep:0.31.0` | PVC `karakeep-data` (20Gi, `local-path`) at `/data` | Next.js + API + worker; SQLite at `/data/db.db` |
| `karakeep-meilisearch` | `getmeili/meilisearch:v1.41.0` | PVC `karakeep-meilisearch` (5Gi, `local-path`) at `/meili_data` | Full-text search index |
| `karakeep-chrome` | `gcr.io/zenika-hub/alpine-chrome:124` | none (stateless) | Headless browser for crawl + screenshots |

PVCs use the k3s `local-path` provisioner — Kubernetes provisions the directory on whichever node first schedules the pod (no `nodeSelector`, no manual `mkdir`). Same pattern as `apps/forgejo/` (20Gi) and `apps/qdrant/` (5Gi). See `docs/architecture.md` Storage section.

## Access

`https://karakeep.kblab.me` — LAN-only via Traefik with cert-manager `letsencrypt-dns`. The `*.kblab.me → 192.168.1.124` AdGuard wildcard rewrite (documented in `apps/pi3-adguard-home/README.md:54-59`) automatically resolves the new host. Reach off-LAN through Headscale (`apps/headscale/`).

## Deploy

Push to `master` and Flux reconciles automatically — no pre-deployment steps.

```sh
git add apps/karakeep apps/kustomization.yaml
git commit -m "feat(apps): add karakeep bookmark/read-later service"
git push

flux reconcile kustomization apps --with-source
kubectl -n karakeep get pvc -w  # both should reach Bound
kubectl -n karakeep get pods -w
```

## First-run

1. Open `https://karakeep.kblab.me` — sign up creates the **first** account (no admin gate).
2. **Immediately** edit `configmap.yaml`: set `DISABLE_SIGNUPS: "true"`, commit, push. Flux re-reconciles.
3. In the UI, generate an API key (Settings → API Keys). Save it — you'll plug it into the iOS/Android apps and browser extensions.

## Enable AI auto-tagging (optional)

The configmap ships with `INFERENCE_ENABLE_AUTO_TAGGING: "false"` because no `OPENAI_API_KEY` is populated yet. To wire it through the in-cluster LiteLLM gateway:

```sh
LITELLM_KEY=$(kubectl -n ai-gateway get secret litellm-secrets \
  -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)

sops apps/karakeep/secret.yaml
# Add a new line under stringData:
#   OPENAI_API_KEY: <paste $LITELLM_KEY here>
```

Then flip `INFERENCE_ENABLE_AUTO_TAGGING: "true"` in `configmap.yaml`. Commit, push, Flux applies.

`OPENAI_BASE_URL` already points at LiteLLM (`http://litellm.ai-gateway.svc.cluster.local:4000/v1`), so any model in `apps/litellm/configmap.yaml` is reachable. Update `INFERENCE_TEXT_MODEL` / `INFERENCE_IMAGE_MODEL` to match a configured `model_name`.

## Stash migration (optional)

The legacy Stash Rust backend at `~/dev/home/stash/backend` exposes an export endpoint. To pull bookmarks into Karakeep:

1. Export from Stash as Netscape HTML (or dump SQLite and convert).
2. In Karakeep: Settings → Imports → Import from Netscape HTML.
3. Karakeep recrawls + reextracts — original snapshots are not preserved, but URLs/titles/tags transfer.

Keep Stash installed in Firefox for ~1 week as fallback. Don't delete `~/dev/home/stash` — keep as archive.

## Backup

Daily CronJob at 03:00 mounts both PVCs read-only and tars `/data` (SQLite + assets) + `/meilisearch` to `/var/backups/karakeep/karakeep-<timestamp>.tar.gz` on whichever node holds the PVs. Keeps last 30. Same shape as `apps/forgejo/backup-cronjob.yaml`.

To run a manual backup:
```sh
kubectl -n karakeep create job karakeep-backup-manual --from=cronjob/karakeep-backup
kubectl -n karakeep logs job/karakeep-backup-manual
```

To restore: scale `karakeep-web` and `karakeep-meilisearch` to 0, untar into the PVC mount points (or recreate PVCs and untar), scale back up.

## Verification

- `flux get kustomization apps` — `karakeep` applied, no errors
- `kubectl -n karakeep get pvc` — both PVCs `Bound`, sizes 20Gi + 5Gi
- `kubectl -n karakeep get pods` — 3 deployments Running
- `kubectl -n karakeep logs deploy/karakeep-web` — no Meilisearch / Chrome connection errors
- `dig +short karakeep.kblab.me @192.168.1.193` → `192.168.1.124`
- `curl -I https://karakeep.kblab.me` — 200 / 307, valid Let's Encrypt cert
- Save a URL from the web UI; confirm screenshot + reader-mode extraction
- Open the same URL in iOS + Android apps to prove cross-device sync
