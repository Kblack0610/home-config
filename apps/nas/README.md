# NAS

Samba file server running on the `asus-laptop` node, providing SMB shares for LAN clients and a backup target for cluster workloads.

## Overview

| Property | Value |
|----------|-------|
| Namespace | `nas` |
| Node | `asus-laptop` (pinned via `storage.role/nas: "true"`) |
| Image | `ghcr.io/servercontainers/samba:latest` |
| Network | `hostNetwork: true` (direct LAN access on port 445) |
| Discovery | Avahi/Bonjour — advertised as **HomeDrive** in Finder |
| Service | Headless (`clusterIP: None`) — clients connect via host IP |

## Shares

| Share | Path on host | Auth | Notes |
|-------|-------------|------|-------|
| `public` | `/mnt/nas/public` | Guest OK | macOS-friendly (fruit VFS) |
| `private` | `/mnt/nas/private` | User `nas` required | Backups stored here |

Both shares use `vfs objects = fruit streams_xattr` for macOS compatibility (Time Capsule icon, metadata streams).

## Important Manifests

| File | Purpose |
|------|---------|
| `deployment.yaml` | Samba container, share config, volume mounts |
| `service.yaml` | Headless service for Gatus health checks |
| `secret.yaml` | SOPS-encrypted SMB password for user `nas` |
| `sops-backup-cronjob.yaml` | Weekly SOPS age key backup (Sun 4 AM) |
| `backup-cleanup-cronjob.yaml` | Weekly backup retention pruning (Sun 5 AM, keep 14) |
| `backup-verify-cronjob.yaml` | Weekly backup integrity check (Mon 6 AM) |
| `3d-prints-backup-cronjob.yaml` | Weekly mirror of the public 3D-print files to the 8TB drive + snapshot (Sun 3:45 AM) |
| `sops-backup-rbac.yaml` | ServiceAccount for SOPS key backup job |

## Backup Infrastructure

The NAS serves as the central backup target. App backup cronjobs write to `/mnt/nas/private/backups/home-k3s/<app>/`.

| CronJob | Schedule | What it does |
|---------|----------|-------------|
| `3d-prints-backup` | Sun 3:45 AM | Mirrors `/mnt/nas/public/3d-printing` to the 8TB drive (`/mnt/backup-8t/3d-prints`) + dated btrfs snapshot, keeps last 14. See [backup runbook](../../docs/backup-runbook.md) |
| `sops-key-backup` | Sun 4 AM | Exports `sops-age` secret from `flux-system`, keeps last 4 copies |
| `nas-backup-cleanup` | Sun 5 AM | Prunes per-app backups beyond 14 copies (skips sops) |
| `nas-backup-verify` | Mon 6 AM | Checks backup freshness (< 48h) and tar.gz integrity for home-assistant, litellm; verifies SOPS key backup exists |

Verification results are appended to `/mnt/nas/private/backups/manifest.log`.

## Secrets

SMB credentials are in `secret.yaml` (SOPS-encrypted). To update:

```bash
# Decrypt, edit, re-encrypt
sops apps/nas/secret.yaml
```

The secret provides `smb-password` for the `nas` SMB user account.

## Deploy

Deployed via Flux with the rest of `apps/`. To force reconcile:

```bash
flux reconcile kustomization apps --with-source
```

## Verify

```bash
# Pod running on asus-laptop
kubectl --context home-k3s get pods -n nas -o wide

# SMB reachable
smbclient -L //192.168.1.152 -N

# Gatus health check
# Monitored as "NAS (SMB)" in the home-k3s group at status.kblab.me

# Backup verification (manual trigger)
kubectl --context home-k3s create job --from=cronjob/nas-backup-verify manual-verify -n nas
kubectl --context home-k3s logs job/manual-verify -n nas
```

## Related Docs

- [Backup runbook](../../docs/backup-runbook.md) — restore procedures and manual backup triggers
- [Infrastructure status](../../infrastructure.md) — cluster-wide inventory
