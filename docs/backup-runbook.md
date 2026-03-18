# Backup Runbook

Use this guide to verify scheduled backups, trigger them manually, or restore data for services that back up into local node storage and the NAS.

## Start Here

| Task | Command or section |
|------|--------------------|
| See which CronJobs exist | `kubectl --context home-k3s get cronjobs -A | grep backup` |
| Trigger a backup now | See `Manual Backup` |
| Check recent backup jobs | `kubectl --context home-k3s get jobs -A --sort-by=.status.startTime | grep backup | tail -5` |
| Restore Home Assistant | See `Restore Home Assistant` |
| Restore LiteLLM | See `Restore LiteLLM` |
| Recover the SOPS Age key | See `Restore SOPS Age Key` |

## Architecture

K3s app backups run on schedule via CronJobs. Each backup writes a local archive on the node first, then attempts to upload a copy to the NAS over SMB.

- Local storage root: `/var/backups/{app}/`
- NAS backup root: `/mnt/nas/private/backups/home-k3s/{app}/`
- Current NAS host: `asus-laptop` (`192.168.1.152`) via `hostNetwork`; move `/mnt/nas` data before changing the pinned NAS node in Git.

## Backup Schedule

| CronJob | Schedule | Namespace | NAS Path |
|---------|----------|-----------|----------|
| `home-assistant-backup` | Daily 2 AM | `home-assistant` | `home-k3s/home-assistant/` |
| `litellm-backup` | Daily 2 AM | `ai-gateway` | `home-k3s/litellm/` |
| `sops-key-backup` | Sunday 4 AM | `nas` | `home-k3s/sops/` |
| `nas-backup-cleanup` | Sunday 5 AM | `nas` | not applicable |
| `nas-backup-verify` | Monday 6 AM | `nas` | writes `manifest.log` |

## Retention

| Location | Retention |
|----------|-----------|
| Local node storage | 30 backups per app |
| NAS backups | 14 backups per app |
| SOPS key copies | 4 copies |

## Manual Backup

```bash
# Home Assistant
kubectl --context home-k3s create job --from=cronjob/home-assistant-backup manual-backup-$(date +%s) -n home-assistant

# LiteLLM
kubectl --context home-k3s create job --from=cronjob/litellm-backup manual-backup-$(date +%s) -n ai-gateway

# SOPS key
kubectl --context home-k3s create job --from=cronjob/sops-key-backup manual-sops-backup-$(date +%s) -n nas
```

## Verification

### Quick checks

```bash
# CronJobs
kubectl --context home-k3s get cronjobs -A | grep backup

# Recent jobs
kubectl --context home-k3s get jobs -A --sort-by=.status.startTime | grep backup | tail -5

# NAS manifest
ssh pc-home-asus-laptop cat /mnt/nas/private/backups/manifest.log
```

### Check stored backups

```bash
# NAS copies
ssh pc-home-asus-laptop 'for d in /mnt/nas/private/backups/home-k3s/*/; do echo "=== $(basename "$d") ==="; ls -lt "$d" | head -3; done'

# Local node copies for Home Assistant
ssh raspberrypi 'ls -lt /var/backups/home-assistant/ | head -3'
```

## Restore Home Assistant

```bash
# Scale the deployment down
kubectl --context home-k3s scale deployment home-assistant -n home-assistant --replicas=0

# Inspect available backups
ssh pc-home-asus-laptop ls -lt /mnt/nas/private/backups/home-k3s/home-assistant/
ssh raspberrypi ls -lt /var/backups/home-assistant/

# Start a temporary restore pod with the PVC mounted
kubectl --context home-k3s run restore --rm -it --image=alpine \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"home-assistant-config"}}],"containers":[{"name":"restore","image":"alpine","stdin":true,"tty":true,"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
  -n home-assistant -- /bin/sh
```

Inside the restore pod:

```bash
rm -rf /data/*
tar -xzf /path/to/backup.tar.gz -C /data/
exit
```

Bring the deployment back:

```bash
kubectl --context home-k3s scale deployment home-assistant -n home-assistant --replicas=1
```

## Restore LiteLLM

Use the same flow as Home Assistant with these substitutions:

- Namespace: `ai-gateway`
- Deployment: `litellm`
- PVC: `litellm-data`
- NAS path: `/mnt/nas/private/backups/home-k3s/litellm/`
- Local path: `/var/backups/litellm/`

## Restore SOPS Age Key

Use this when both the local development key and the in-cluster secret are unavailable.

```bash
# Find the newest NAS backup
ssh pc-home-asus-laptop ls -lt /mnt/nas/private/backups/home-k3s/sops/

# Copy the backup manifest locally
scp pc-home-asus-laptop:/mnt/nas/private/backups/home-k3s/sops/sops-age-YYYYMMDD-HHMMSS.yaml ./sops-age-restore.yaml

# Restore the cluster secret
kubectl --context home-k3s apply -f sops-age-restore.yaml

# Extract the private key for local SOPS use
kubectl --context home-k3s get secret sops-age -n flux-system -o jsonpath='{.data.age\.agekey}' | base64 -d > ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt
```

## Troubleshooting

### NAS upload fails but local backup succeeds

That is expected. The local backup is the primary success condition; NAS copy is best-effort.

```bash
kubectl --context home-k3s get pods -n nas
kubectl --context home-k3s run --rm -it dns-test --image=alpine -- nslookup nas.nas.svc.cluster.local
ssh pc-home-asus-laptop df -h /mnt/nas/private
```

### A backup job fails

```bash
kubectl --context home-k3s logs job/<job-name> -n <namespace>
```

Common causes:

- PVC or source path is unavailable
- node disk is full
- NAS storage is full or unreachable

## Related Docs

- [gitops.md](./gitops.md)
- [../infrastructure.md](../infrastructure.md)
