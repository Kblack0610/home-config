# Forgejo

Self-hosted Git forge running in the `forgejo` namespace at `git.kblab.me`.
Version 14.0.4 with Actions (CI/CD), Packages (container + language registries),
and repository mirroring enabled.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles namespace, app, services, ingress, backup, and runner |
| `configmap.yaml` | Forgejo `app.ini` — Actions, Packages, Mirroring enabled |
| `deployment.yaml` | Main Forgejo workload with config-seed init container |
| `service-http.yaml` | HTTP service on port `3000` |
| `service-ssh.yaml` | In-cluster SSH service on port `22` |
| `ingress.yaml` | TLS ingress for `git.kblab.me` |
| `pvc.yaml` | Persistent Forgejo data volume |
| `backup-cronjob.yaml` | Daily backup job for full data and SQLite snapshots |
| `runner-deployment.yaml` | Forgejo Runner + DinD sidecar (any amd64 workstation node) |
| `runner-config-cm.yaml` | Runner config with `docker` + `linux-amd64` labels |
| `runner-secret.yaml` | SOPS-encrypted instance-level runner registration token |

## Configuration

The configmap mounts to `/etc/gitea/app.ini`. An **init container** (`config-seed`)
copies it to `/data/gitea/conf/app.ini` on every pod start, because Forgejo reads
config from the PVC path, not the configmap mount.

Key config sections:

| Section | Purpose |
|---------|---------|
| `[actions]` | Enables Forgejo Actions (CI/CD), default actions from `data.forgejo.org` |
| `[packages]` | Enables container registry + language package registries |
| `[mirror]` | Enables push/pull repository mirroring |
| `[cron.update_mirrors]` | Auto-sync mirrors every 10 minutes |

**Important caveats:**

- `START_SSH_SERVER = false` — the container's OpenSSH handles port 22. Forgejo's
  built-in SSH server conflicts with it on the same port.
- **subPath configmap mounts don't auto-update.** After changing `configmap.yaml`,
  you must delete the pod to pick up changes:
  ```bash
  kubectl --context home-k3s delete pod -n forgejo -l app.kubernetes.io/name=forgejo
  ```

## Runner

Instance-level runners serve all repos on the Forgejo instance, each with a
Docker-in-Docker sidecar for container-based CI workflows.

It runs **2 concurrent replicas, not pinned to a single host**:
`runner-deployment.yaml` schedules pods on amd64 workstation nodes
(`node-role.kubernetes.io/workstation`, currently thinkcentre / hp-victus /
asus-laptop) and a soft `podAntiAffinity` spreads them across different nodes. If
one node dies (e.g. a power outage), the other replica is already live on another
box — instant failover instead of the ~5-min eviction gap a single replica would
suffer on a hard node death. One dead box can no longer block CI. Each pod
registers under its own pod name (`--name "$POD_NAME"` via the downward API),
which is mandatory with more than one replica — otherwise both would register as
`forgejo-runner` and collide.

> **Stale runners after a reschedule:** when a pod lands on a new node it
> re-registers under its new pod name, so the Forgejo admin runner list (Site
> Administration → Actions → Runners) may show offline `forgejo-runner-*` entries
> from previous pods. These are cosmetic — offline runners pick up no jobs —
> delete them from the admin UI when convenient.

Generate a new registration token (if needed):

```bash
kubectl --context home-k3s exec -n forgejo deploy/forgejo -- \
  su-exec git forgejo forgejo-cli actions generate-runner-token
```

Check runner status:

```bash
kubectl --context home-k3s logs -n forgejo deploy/forgejo-runner -c runner --tail=20
```

To use Actions in a repo, add `.forgejo/workflows/<name>.yaml` — same syntax as
GitHub Actions.

## Container Registry

Push images to Forgejo's built-in OCI registry:

```bash
docker login git.kblab.me
docker tag myimage:latest git.kblab.me/kenneth/myimage:latest
docker push git.kblab.me/kenneth/myimage:latest
```

## Mirroring

Configure per-repo in the Forgejo UI under repo Settings → Mirror Settings:

- **Push mirror to gitlab.com**: offsite DR for personal repos
- **Push mirror to github.com**: public-facing repos only
- **Pull mirror from github.com**: read-only replica of repos where GitHub is canonical (e.g., home-config for Flux)

## Deploy

All changes go through Flux. Edit manifests, commit, push, then reconcile:

```bash
flux reconcile kustomization apps --with-source --context home-k3s
```

## Verify

```bash
# Pod health
kubectl --context home-k3s get pods -n forgejo
kubectl --context home-k3s logs -n forgejo deploy/forgejo --tail=50

# Runner status
kubectl --context home-k3s logs -n forgejo deploy/forgejo-runner -c runner --tail=10

# Backup jobs
kubectl --context home-k3s get cronjob -n forgejo

# Web UI
curl -s https://git.kblab.me/api/healthz
```

Endpoints:

- Web UI: `https://git.kblab.me`
- API: `https://git.kblab.me/api/v1`
- Container Registry: `git.kblab.me` (docker login)
- In-cluster HTTP: `forgejo-http.forgejo.svc:3000`
- In-cluster SSH: `forgejo-ssh.forgejo.svc:22`

## Backup Notes

- `forgejo-backup` runs daily at `03:00`
- Full archives and compressed SQLite backups stored under `/var/backups/forgejo` (hostPath on asus-laptop's root disk)
- Retention: newest **7** full backups (~55G each). The job prunes BEFORE writing and aborts if `/backup` has under 100G free, so it can never fill the node.
- History: on 2026-07-09 a retention of 30 (~1.6TiB of daily fulls) filled asus-laptop's root disk, tripped kubelet DiskPressure, and evicted the entire home-k3s workload cluster for ~6h. Retention was cut to 7 and a prune-first + free-space guard added. Longer-term recovery points belong off-box (MinIO/S3), not on the same disk as the data they protect.

## Related Docs

- [../../docs/gitops.md](../../docs/gitops.md)
- [../../docs/backup-runbook.md](../../docs/backup-runbook.md)
- [../../infrastructure.md](../../infrastructure.md)
