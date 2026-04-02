# Immich — Self-hosted Photo Management

Google Photos alternative with mobile backup, face recognition, smart search, and sharing.

## Architecture

All components run on the `asus-laptop` node (NAS node, 64GB RAM).

| Component | Image | Port |
|-----------|-------|------|
| Server (API + Web) | `ghcr.io/immich-app/immich-server:release` | 2283 |
| Machine Learning | `ghcr.io/immich-app/immich-machine-learning:release` | 3003 |
| PostgreSQL + vectorchord | `ghcr.io/immich-app/postgres:16-vectorchord0.3.0` | 5432 |
| Redis | `redis:7-alpine` | 6379 |

## Storage

| Data | Path | Description |
|------|------|-------------|
| Photos | `/mnt/nas/private/immich/upload` | Photo library on NAS private share |
| PostgreSQL | `/mnt/media/app-config/immich/postgres` | Database files |
| ML models | `/mnt/media/app-config/immich/ml-cache` | Downloaded ML models (~2GB) |

## Access

- **URL**: https://photos.kblab.me (LAN only, via Traefik)
- **Mobile app**: Immich app (iOS/Android) — set server URL to `https://photos.kblab.me`

## Backups

Daily at 3 AM via CronJob:
- `pg_dump` → gzip → `/var/backups/immich/` (local, 30 retained)
- Copy to NAS via smbclient → `/mnt/nas/private/backups/home-k3s/immich/`

### Restore

```bash
kubectl exec -n immich deploy/immich-postgres -- pg_isready
gunzip -c /var/backups/immich/immich-YYYYMMDD-HHMMSS.sql.gz | \
  kubectl exec -i -n immich deploy/immich-postgres -- psql -U immich -d immich
```

## Secrets

Managed via SOPS:
- `secret.yaml` — DB credentials
- `backup-nas-secret.yaml` — NAS backup credentials

Encrypt after editing:
```bash
sops --encrypt --in-place apps/immich/secret.yaml
sops --encrypt --in-place apps/immich/backup-nas-secret.yaml
```

## Troubleshooting

```bash
# Check all pods
kubectl get pods -n immich

# Server logs
kubectl logs -n immich deploy/immich-server

# ML logs (slow startup is normal — model loading takes 1-2 min)
kubectl logs -n immich deploy/immich-ml

# Database
kubectl exec -n immich deploy/immich-postgres -- pg_isready -d immich
```
