# Backup Runbook

## Architecture

All K3s app backups run daily at 2 AM via CronJobs. Each backup:
1. Creates a local tar.gz on the node's hostPath (`/var/backups/{app}/`)
2. Uploads a copy to the NAS via SMB (`//nas.nas.svc.cluster.local/private`)

NAS backup location: `/mnt/nas/private/backups/home-k3s/{app}/`

### Backup Schedule

| CronJob | Schedule | Namespace | NAS Path |
|---------|----------|-----------|----------|
| `home-assistant-backup` | Daily 2 AM | home-assistant | `home-k3s/home-assistant/` |
| `adguard-home-backup` | Daily 2 AM | adguard-home | `home-k3s/adguard-home/` |
| `litellm-backup` | Daily 2 AM | ai-gateway | `home-k3s/litellm/` |
| `sops-key-backup` | Sunday 4 AM | nas | `home-k3s/sops/` |
| `nas-backup-cleanup` | Sunday 5 AM | nas | N/A (prunes old backups) |
| `nas-backup-verify` | Monday 6 AM | nas | N/A (writes to `manifest.log`) |

### Retention

| Location | Retention |
|----------|-----------|
| Local (`/var/backups/`) | 30 backups per app |
| NAS (`/mnt/nas/private/backups/`) | 14 backups per app |
| SOPS key | 4 copies |

## Manual Backup

Trigger a backup immediately for any app:

```sh
# Home Assistant
kubectl --context home-k3s create job --from=cronjob/home-assistant-backup manual-backup-$(date +%s) -n home-assistant

# AdGuard Home
kubectl --context home-k3s create job --from=cronjob/adguard-home-backup manual-backup-$(date +%s) -n adguard-home

# LiteLLM
kubectl --context home-k3s create job --from=cronjob/litellm-backup manual-backup-$(date +%s) -n ai-gateway

# SOPS key
kubectl --context home-k3s create job --from=cronjob/sops-key-backup manual-sops-backup-$(date +%s) -n nas
```

## Restore Procedures

### Restore Home Assistant

```sh
# 1. Scale down the deployment
kubectl --context home-k3s scale deployment home-assistant -n home-assistant --replicas=0

# 2. Find the backup to restore (check NAS first, then local)
# NAS:
ssh pc-home-asus-laptop ls -lt /mnt/nas/private/backups/home-k3s/home-assistant/
# Local (on the node running the PVC):
ssh raspberrypi ls -lt /var/backups/home-assistant/

# 3. Copy backup to a temp pod and extract
kubectl --context home-k3s run restore --rm -it --image=alpine \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"home-assistant-config"}}],"containers":[{"name":"restore","image":"alpine","stdin":true,"tty":true,"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
  -n home-assistant -- /bin/sh
# Inside the pod:
# rm -rf /data/*
# tar -xzf /path/to/backup.tar.gz -C /data/

# 4. Scale back up
kubectl --context home-k3s scale deployment home-assistant -n home-assistant --replicas=1
```

### Restore AdGuard Home

Same procedure as Home Assistant, substituting:
- Namespace: `adguard-home`
- Deployment: `adguard-home`
- PVC: `adguard-home-config`
- NAS path: `home-k3s/adguard-home/`
- Local path: `/var/backups/adguard-home/`

### Restore LiteLLM

Same procedure as Home Assistant, substituting:
- Namespace: `ai-gateway`
- Deployment: `litellm`
- PVC: `litellm-data`
- NAS path: `home-k3s/litellm/`
- Local path: `/var/backups/litellm/`

### Restore SOPS Age Key

If both the dev machine and the K8s secret are lost:

```sh
# 1. Find the backup on the NAS
ssh pc-home-asus-laptop ls -lt /mnt/nas/private/backups/home-k3s/sops/

# 2. Copy and apply the secret
scp pc-home-asus-laptop:/mnt/nas/private/backups/home-k3s/sops/sops-age-YYYYMMDD-HHMMSS.yaml ./sops-age-restore.yaml

# 3. Apply to cluster
kubectl --context home-k3s apply -f sops-age-restore.yaml

# 4. Extract the private key for local dev use
kubectl --context home-k3s get secret sops-age -n flux-system -o jsonpath='{.data.age\.agekey}' | base64 -d > ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt
```

## Manual Verification

Check that backups are working without waiting for the weekly verify job:

```sh
# Check NAS backup directories
ssh pc-home-asus-laptop 'for d in /mnt/nas/private/backups/home-k3s/*/; do echo "=== $(basename $d) ==="; ls -lt "$d" | head -3; done'

# Check local backups on each node
ssh raspberrypi 'ls -lt /var/backups/home-assistant/ | head -3'
ssh raspberrypi-771be84c 'ls -lt /var/backups/adguard-home/ | head -3'

# Check CronJob status
kubectl --context home-k3s get cronjobs -A | grep backup

# Check recent job results
kubectl --context home-k3s get jobs -A --sort-by=.status.startTime | grep backup | tail -5

# Read the manifest log
ssh pc-home-asus-laptop cat /mnt/nas/private/backups/manifest.log
```

## Troubleshooting

### NAS upload fails but local backup succeeds

This is expected behavior — the SMB upload is best-effort. Check:
1. NAS pod is running: `kubectl --context home-k3s get pods -n nas`
2. SMB service resolves: `kubectl --context home-k3s run --rm -it dns-test --image=alpine -- nslookup nas.nas.svc.cluster.local`
3. NAS storage is mounted: `ssh pc-home-asus-laptop df -h /mnt/nas/private`

### Backup job shows as Failed

```sh
# Check the pod logs
kubectl --context home-k3s logs job/<job-name> -n <namespace>
```

Common causes:
- PVC is locked by another pod (shouldn't happen with readOnly mount)
- Node disk full (`df -h` on the node)
- NAS disk full (check `/mnt/nas/private`)
