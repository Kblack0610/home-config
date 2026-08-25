# Backup Runbook

Use this guide to verify scheduled backups, trigger them manually, or restore data for services that back up into local node storage and the NAS.

## asus-laptop drive inventory

Authoritative list of the disks on asus-laptop (the pinned NAS + backup node). The kernel device letter (`/dev/sdX`) is NOT stable across reboots/replugs - always identify a disk by its label or UUID, never by `/dev/sda`.

| Disk | Size | Label / UUID | Role | Mount(s) |
|---|---|---|---|---|
| USB HDD | 7.3TB (8TB nominal) | `backup8t` / `99a8c3bb-b1d9-4a78-88de-627b429cd147` | consolidated backup target | `/mnt/backup-8t/*` |
| NVMe | 1.8TB | `nvme1n1p2` | NAS data (public + private shares) | `/mnt/nas/public`, `/mnt/nas/private` |
| NVMe | 1.8TB | `nvme0n1p2` | OS root + kubelet | `/` |

Note: this is a same-box setup - the NAS data and its backup drive are both inside asus-laptop. That protects against a single disk failing and against accidental deletion (via snapshots), but NOT against loss of the whole machine (fire/theft). Offsite is not yet implemented.

## Backup Drive (asus-laptop)

The Seagate Expansion USB HDD (label `backup8t`, uuid `99a8c3bb-b1d9-4a78-88de-627b429cd147`; currently enumerates as `/dev/sdc`) is the primary consolidated backup target on asus-laptop. It holds the Immich photo originals + DB snapshots and the 3D-print files mirror, with `@media` and `@app-config` subvolumes reserved for Phase 2.

### Drive layout

| Subvolume | Mount | Contents |
|---|---|---|
| `@immich` | `/mnt/backup-8t/immich` | rsync mirror of Immich originals (`/mnt/nas/private/immich`) |
| `@immich-db` | `/mnt/backup-8t/immich-db` | DB dumps copied from `/var/backups/immich` |
| `@immich-snapshots` | `/mnt/backup-8t/immich-snapshots` | Dated read-only btrfs snapshots (14 retained) |
| `@3d-prints` | `/mnt/backup-8t/3d-prints` | rsync mirror of 3D-print files (`/mnt/nas/public/3d-printing`) |
| `@3d-prints-snapshots` | `/mnt/backup-8t/3d-prints-snapshots` | Dated read-only btrfs snapshots (14 retained) |
| `@media` | `/mnt/backup-8t/media` | Reserved for Phase 2 (media library) |
| `@app-config` | `/mnt/backup-8t/app-config` | Reserved for Phase 2 (all NAS app-config) |

### Reprovisioning (if drive is reformatted or replaced)

Run on asus-laptop (ssh -p 2222 192.168.1.152). The device letter is not stable - resolve it from the label first:

```bash
# 0. Resolve the device by label (do NOT assume /dev/sda - it currently enumerates as /dev/sdc)
DEV=$(blkid -L backup8t 2>/dev/null | sed 's/[0-9]*$//')   # e.g. /dev/sdc ; if unformatted, find via lsblk
echo "Device: $DEV"

# 1. Wipe + partition
wipefs -a "$DEV"
printf "label: gpt\nstart=, size=, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=backup8t\n" | sudo sfdisk "$DEV"

# 2. Format btrfs
sudo mkfs.btrfs -L backup8t "${DEV}1"
NEW_UUID=$(sudo blkid -s UUID -o value "${DEV}1")
echo "UUID: $NEW_UUID"

# 3. Create subvolumes
sudo mkdir -p /mnt/btrfs-top
sudo mount -o subvolid=5 "${DEV}1" /mnt/btrfs-top
sudo btrfs subvolume create /mnt/btrfs-top/@immich
sudo btrfs subvolume create /mnt/btrfs-top/@immich-db
sudo btrfs subvolume create /mnt/btrfs-top/@immich-snapshots
sudo btrfs subvolume create /mnt/btrfs-top/@3d-prints
sudo btrfs subvolume create /mnt/btrfs-top/@3d-prints-snapshots
sudo btrfs subvolume create /mnt/btrfs-top/@media
sudo btrfs subvolume create /mnt/btrfs-top/@app-config
sudo umount /mnt/btrfs-top
sudo rmdir /mnt/btrfs-top

# 4. Create mount points
sudo mkdir -p /mnt/backup-8t/{immich,immich-db,immich-snapshots,3d-prints,3d-prints-snapshots,media,app-config}

# 5. Append to /etc/fstab (replace UUID with $NEW_UUID from step 2)
# UUID=... /mnt/backup-8t/immich               btrfs noatime,compress=zstd:1,nofail,subvol=@immich               0 0
# UUID=... /mnt/backup-8t/immich-db            btrfs noatime,compress=zstd:1,nofail,subvol=@immich-db            0 0
# UUID=... /mnt/backup-8t/immich-snapshots     btrfs noatime,compress=zstd:1,nofail,subvol=@immich-snapshots     0 0
# UUID=... /mnt/backup-8t/3d-prints            btrfs noatime,compress=zstd:1,nofail,subvol=@3d-prints            0 0
# UUID=... /mnt/backup-8t/3d-prints-snapshots  btrfs noatime,compress=zstd:1,nofail,subvol=@3d-prints-snapshots  0 0
# UUID=... /mnt/backup-8t/media                btrfs noatime,compress=zstd:1,nofail,subvol=@media                0 0
# UUID=... /mnt/backup-8t/app-config           btrfs noatime,compress=zstd:1,nofail,subvol=@app-config           0 0

sudo mount -a
findmnt /mnt/backup-8t/immich  # verify
```

### USB autosuspend udev rule (required — install once)

The Seagate Expansion (0bc2:203b) disconnects mid-write under sustained load when Linux's USB autosuspend is enabled. Install this rule on asus-laptop:

```bash
sudo tee /etc/udev/rules.d/99-seagate-expansion-no-suspend.rules > /dev/null << 'EOF'
# Disable USB autosuspend for Seagate Expansion SW (0bc2:203b).
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="0bc2", ATTRS{idProduct}=="203b", TEST=="power/control", ATTR{power/control}="on"
ACTION=="bind", SUBSYSTEM=="usb", ATTRS{idVendor}=="0bc2", ATTRS{idProduct}=="203b", TEST=="power/autosuspend_delay_ms", ATTR{power/autosuspend_delay_ms}="-1"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
# Verify:
cat /sys/bus/usb/devices/4-2/power/control   # expect: on
```

Without this rule, btrfs will accumulate thousands of write_io_errs, abort its transaction, and force-remount read-only when the drive reconnects. Recovery: unmount all backup-8t mounts, run `btrfs rescue zero-log /dev/sdXY`, then `mount -a`.

Note: `nofail` means an unplugged drive never blocks boot. The `immich-originals-backup` CronJob's mount-safety guard detects an unmounted drive and exits 1 before writing, so you'll see a failed job rather than silently filled root fs.

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

| CronJob | Schedule | Namespace | Target |
|---------|----------|-----------|--------|
| `home-assistant-backup` | Daily 2 AM | `home-assistant` | NAS `home-k3s/home-assistant/` |
| `litellm-backup` | Daily 2 AM | `ai-gateway` | NAS `home-k3s/litellm/` |
| `actual-budget-backup` | Daily 3 AM | `actual-budget` | `/var/backups/actual-budget` + NAS `home-k3s/actual-budget/` |
| `forgejo-backup` | Daily 3 AM | `forgejo` | asus-laptop `/mnt/backups/forgejo` (2nd disk; off-box copy is the separate job below) |
| `immich-backup` | Daily 3 AM | `immich` | `/var/backups/immich` + NAS `home-k3s/immich/` (DB only) |
| `karakeep-backup` | Daily 3 AM | `karakeep` | `/var/backups/karakeep` (LOCAL ONLY - no off-box copy) |
| `vaultwarden-backup` | Daily 3 AM | `vaultwarden` | `/var/backups/vaultwarden` + NAS `home-k3s/vaultwarden/` |
| `forgejo-backup-offbox` | Daily 3:30 AM | `forgejo` | hp-victus NFS `192.168.1.243:/srv/backups/forgejo` |
| `immich-originals-backup` | Daily 3:30 AM | `immich` | 8TB `/mnt/backup-8t/immich` + snapshots (originals + DB) |
| `3d-prints-backup` | Sunday 3:45 AM | `nas` | 8TB `/mnt/backup-8t/3d-prints` + snapshots (mirror of `/mnt/nas/public/3d-printing`) |
| `sops-key-backup` | Sunday 4 AM | `nas` | NAS `home-k3s/sops/` |
| `zomboid-backup` | Daily 4:30 AM | `zomboid` | NAS **public** share `backups/zomboid/` |
| `nas-backup-cleanup` | Sunday 5 AM | `nas` | not applicable |
| `nas-backup-verify` | Monday 6 AM | `nas` | writes `manifest.log` |

Every copy in this table lives in this house. There is no offsite tier: see the note under `asus-laptop drive inventory`.

## Retention

| Location | Retention |
|----------|-----------|
| Local node storage | 30 backups per app |
| NAS backups | 14 backups per app |
| 8TB btrfs snapshots (immich, 3d-prints) | 14 snapshots each |
| SOPS key copies | 4 copies |

## Manual Backup

```bash
# Home Assistant
kubectl --context home-k3s create job --from=cronjob/home-assistant-backup manual-backup-$(date +%s) -n home-assistant

# Actual Budget (finance.kblab.me) - run this before any risky change to the budget
kubectl --context home-k3s create job --from=cronjob/actual-budget-backup manual-backup-$(date +%s) -n actual-budget

# LiteLLM
kubectl --context home-k3s create job --from=cronjob/litellm-backup manual-backup-$(date +%s) -n ai-gateway

# Immich DB dump (runs first)
kubectl --context home-k3s create job --from=cronjob/immich-backup manual-backup-$(date +%s) -n immich

# Immich originals + snapshots to 8TB (run after immich-backup so a fresh dump is available)
kubectl --context home-k3s create job --from=cronjob/immich-originals-backup manual-backup-$(date +%s) -n immich

# 3D-print files: mirror /mnt/nas/public/3d-printing to the 8TB + snapshot
kubectl --context home-k3s create job --from=cronjob/3d-prints-backup manual-backup-$(date +%s) -n nas

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

## Restore Actual Budget (finance.kblab.me)

Actual stores two things under `/data`, and a restore needs both:

| Path | Holds |
|---|---|
| `server-files/account.sqlite` | server accounts, sessions, and the list of budget files |
| `user-files/group-<uuid>.sqlite` | the budget itself, as an append-only CRDT sync log |
| `user-files/file-<uuid>.blob` | the budget's initial snapshot |

The budget is a sync log, not a table of transactions, so category assignments are messages inside `group-<uuid>.sqlite`. That is why a partial restore of only `account.sqlite` gets you a working login and an empty budget.

```bash
# 1. Pick a backup. Newest first, both tiers:
kubectl --context home-k3s get pods -n actual-budget -o wide           # which node holds the PVC
ssh <that-node> ls -lt /var/backups/actual-budget/                     # node-local copy
ssh pc-home-asus-laptop sudo ls -lt /mnt/nas/private/backups/home-k3s/actual-budget/   # off-box copy

# 2. Scale down. The PVC is RWO and the Deployment is strategy: Recreate,
#    so the app MUST be stopped or it will write over the restore.
kubectl --context home-k3s scale deployment actual-budget -n actual-budget --replicas=0

# 3. Restore pod with the PVC mounted
kubectl --context home-k3s run restore --rm -it --image=alpine \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"actual-budget-data"}}],"containers":[{"name":"restore","image":"alpine","stdin":true,"tty":true,"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
  -n actual-budget -- /bin/sh
```

Inside the restore pod (copy the chosen archive in first, e.g. with `kubectl cp`):

```bash
rm -rf /data/*
tar -xzf /path/to/actual-YYYYMMDD-HHMMSS.tar.gz -C /data/
ls -la /data/user-files /data/server-files    # confirm both are populated
exit
```

Bring it back:

```bash
kubectl --context home-k3s scale deployment actual-budget -n actual-budget --replicas=1
```

Then, in the browser: the web client keeps its own local copy of the budget and will try to sync it back over the restored server state. After a server-side restore, close the file in the Actual UI and re-download it from the server rather than continuing in the open tab.

## Verify a backup without restoring

A backup that exists is not the same as a backup that holds your data. These checks read a copy directly and touch nothing in the cluster. Actual is the worked example; the shape applies to any SQLite-backed app here.

```bash
# Pull the newest off-box copy and unpack it somewhere scratch
ssh pc-home-asus-laptop sudo cat /mnt/nas/private/backups/home-k3s/actual-budget/actual-YYYYMMDD-HHMMSS.tar.gz > /tmp/ab.tar.gz
mkdir -p /tmp/ab && tar -xzf /tmp/ab.tar.gz -C /tmp/ab && tar -tzf /tmp/ab.tar.gz

# Both databases must be structurally sound
sqlite3 /tmp/ab/server-files/account.sqlite "pragma integrity_check;"
sqlite3 /tmp/ab/user-files/group-*.sqlite   "pragma integrity_check;"

# The budget file is registered and not deleted
sqlite3 /tmp/ab/server-files/account.sqlite "select id,name,group_id,deleted from files;"

# How much budget history the archive actually contains, and how fresh it is
sqlite3 /tmp/ab/user-files/group-*.sqlite \
  "select count(*) messages, min(timestamp) oldest, max(timestamp) newest from messages_binary;"
```

To confirm a specific piece of work survived, search the sync log for the column it wrote. `hex()` returns UPPERCASE, so the needle must be uppercase or every count comes back 0. The needle is the protobuf field-3 tag (`1A`), the length, then the column name:

```bash
# transactions that have a category assigned ("category" = 8 bytes -> 1A 08)
sqlite3 /tmp/ab/user-files/group-*.sqlite \
  "select count(*) from messages_binary where instr(hex(content),'1A0863617465676F7279')>0;"
```

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
