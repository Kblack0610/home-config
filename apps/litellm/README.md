# LiteLLM

LLM gateway deployment in the `ai-gateway` namespace, exposed through Traefik at `llm.kblab.me`.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, app, service, ingress, and backup job |
| `configmap.yaml` | LiteLLM config mounted into the container |
| `deployment.yaml` | Main gateway workload and health checks |
| `service.yaml` | Cluster service on port `4000` |
| `ingress.yaml` | TLS ingress for `llm.kblab.me` |
| `pvc.yaml` | Persistent application data |
| `backup-cronjob.yaml` | Daily local and NAS backup job |
| `backup-nas-secret.yaml` | NAS SMB credentials for backup upload |

## Configuration and Secrets

- Runtime secrets are loaded from `litellm-secrets` via `envFrom`.
- Config is mounted from `litellm-config` to `/etc/litellm/config.yaml`.
- Persistent data is stored on the `litellm-data` PVC and backed up daily.

## Deploy

```bash
kubectl apply -k apps/litellm
```

Prefer committing manifest changes and reconciling through Flux for normal operations.

## Verify

```bash
kubectl -n ai-gateway get all
kubectl -n ai-gateway logs deployment/litellm --tail=100
kubectl -n ai-gateway get cronjob litellm-backup
```

Health endpoints used by the deployment:

- `/health/liveliness`
- `/health/readiness`

## Backup Notes

- The `litellm-backup` CronJob runs daily at `02:00`.
- Local backups are written to `/var/backups/litellm` on the node.
- NAS upload is best-effort and uses SMB credentials from `backup-nas-credentials`.

## Related Docs

- [../../docs/backup-runbook.md](../../docs/backup-runbook.md)
- [../../docs/gitops.md](../../docs/gitops.md)
- [../../infrastructure.md](../../infrastructure.md)
