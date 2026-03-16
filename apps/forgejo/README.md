# Forgejo

Self-hosted Git forge running in the `forgejo` namespace, exposed at `git.kblab.me`.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, app, services, ingress, and backup job |
| `configmap.yaml` | Forgejo application config |
| `deployment.yaml` | Main Forgejo workload |
| `service-http.yaml` | HTTP service on port `3000` |
| `service-ssh.yaml` | In-cluster SSH service on port `22` |
| `ingress.yaml` | TLS ingress for `git.kblab.me` |
| `pvc.yaml` | Persistent Forgejo data volume |
| `backup-cronjob.yaml` | Daily backup job for full data and SQLite snapshots |

## Configuration and Data

- The workload mounts `forgejo-config` to `/etc/gitea/app.ini`.
- Persistent data is stored on the `forgejo-data` PVC.
- The deployment uses a `Recreate` strategy because the PVC is `ReadWriteOnce`.

## Deploy

```bash
kubectl apply -k apps/forgejo
```

Use Flux for normal changes unless you are doing initial setup or recovery work.

## Verify

```bash
kubectl -n forgejo get all
kubectl -n forgejo logs deployment/forgejo --tail=100
kubectl -n forgejo get cronjob forgejo-backup
```

Ingress and services:

- Web UI: `https://git.kblab.me`
- In-cluster HTTP service: `forgejo-http:3000`
- In-cluster SSH service: `forgejo-ssh:22`
- External Git-over-SSH is intentionally disabled so node SSH on port `22` remains available

## Backup Notes

- `forgejo-backup` runs daily at `03:00`.
- Full archives and compressed SQLite backups are stored under `/var/backups/forgejo`.
- Retention is handled in the job by pruning to the newest 30 backups.

## Related Docs

- [../../docs/backup-runbook.md](../../docs/backup-runbook.md)
- [../../docs/gitops.md](../../docs/gitops.md)
- [../../infrastructure.md](../../infrastructure.md)
