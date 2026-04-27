# Postgres (shared)

Shared Postgres + pgvector deployment in the `databases` namespace. Per-app database isolation; ClusterIP-only (no external ingress).

Reachable from inside the cluster at `postgres.databases.svc.cluster.local:5432`.

Image: [`pgvector/pgvector:0.8.2-pg17-trixie`](https://hub.docker.com/r/pgvector/pgvector) — Postgres 17 + pgvector 0.8.2.

## Why shared

Until this directory existed, each Postgres-using app rolled its own (e.g., `apps/immich/postgres-deployment.yaml` with its own image, hostPath, and backup story). One shared cluster collapses backups, version upgrades, and disk planning to a single touchpoint. New apps get a database + role via the init pattern below; mem0 (`apps/mem0/`) is the first consumer.

Immich is intentionally NOT migrated — its existing Postgres uses an Immich-specific vectorchord extension and custom data layout. Migrating later is a separate decision.

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | `databases` namespace |
| `pvc.yaml` | 20Gi local-path PVC |
| `init-configmap.yaml` | First-boot SQL scripts (one per consumer app) |
| `secret.yaml.template` | SOPS-encryption template for superuser + per-app passwords |
| `deployment.yaml` | pgvector container, 5432 ClusterIP |
| `service.yaml` | ClusterIP `:5432` |
| `kustomization.yaml` | Roll-up (excludes secret until encrypted file exists) |

## Bootstrap

1. Generate the secrets:

   ```bash
   openssl rand -base64 32   # POSTGRES_PASSWORD (superuser)
   openssl rand -base64 32   # POSTGRES_MEM0_PASSWORD (mem0 role)
   ```

2. Copy the template, fill in values, encrypt:

   ```bash
   cp apps/postgres/secret.yaml.template apps/postgres/secret.yaml
   $EDITOR apps/postgres/secret.yaml
   sops --encrypt --in-place apps/postgres/secret.yaml
   ```

3. Add `- secret.yaml` to `apps/postgres/kustomization.yaml` resources.

4. Stamp the same `POSTGRES_MEM0_PASSWORD` value into the consuming app's secret (`apps/mem0/secret.yaml`'s `POSTGRES_PASSWORD`). The init script reads the env var on first boot to provision the role with that password.

5. Commit + push. Flux reconciles. On first boot, `10-mem0.sh` runs and creates the `mem0` database + role.

## Verify

```bash
kubectl --context home-k3s -n databases get all
kubectl --context home-k3s -n databases logs deployment/postgres --tail=50

# Confirm the mem0 db + extension landed:
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql -U postgres -d mem0 -c "\dx"   # vector should be listed
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql -U postgres -c "\du"           # mem0 role should be listed

# Smoke-test the mem0 role can connect:
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql "postgresql://mem0:$POSTGRES_MEM0_PASSWORD@localhost:5432/mem0" -c "SELECT 1"
```

## Onboarding a new app post-bootstrap

The `docker-entrypoint-initdb.d` scripts only run when the data directory is empty (first boot). For apps onboarded after Postgres is already running:

1. Add a `POSTGRES_<APP>_PASSWORD` field to `secret.yaml`, regenerate-encrypt-commit.
2. Add `<NN>-<app>.sh` to `init-configmap.yaml` for documentation + future fresh-installs.
3. Run the equivalent SQL by hand against the live cluster:

   ```bash
   kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
     psql -U postgres -c "CREATE ROLE <app> LOGIN PASSWORD '<password>';"
   kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
     psql -U postgres -c "CREATE DATABASE <app> OWNER <app>;"
   # If the app needs pgvector:
   kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
     psql -U postgres -d <app> -c "CREATE EXTENSION IF NOT EXISTS vector;"
   ```

4. Stamp the password into `apps/<app>/secret.yaml` and reference `postgres.databases.svc.cluster.local` in the app's config.

## Backup

Not yet wired. Tracked as a follow-up — this Postgres holds mem0 memories; once the workload is real, add a daily `pg_dump`-to-NAS CronJob analogous to `apps/litellm/backup-cronjob.yaml`.
