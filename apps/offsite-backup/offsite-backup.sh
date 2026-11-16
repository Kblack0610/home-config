#!/bin/sh
# offsite-backup.sh - the offsite leg of 3-2-1: an encrypted restic copy of the
# 8TB drive's newest snapshots in Backblaze B2. Runbook: docs/backup-runbook.md
# "Offsite (B2)".
#
# Shipped as the `offsite-backup-script` ConfigMap, run by the offsite-backup
# CronJob. It reads only /browse (the 8TB top level, read-only), so it never
# touches live data.
#
# Each dataset is bind-mounted at a fixed path /data/<name> before the backup:
# restic picks the parent snapshot by path, so backing up the dated snapshot
# directory directly would rescan every file every night.
#
# Env: RESTIC_REPOSITORY, RESTIC_PASSWORD, AWS_ACCESS_KEY_ID,
# AWS_SECRET_ACCESS_KEY (the offsite-backup secret), RESTIC_CACHE_DIR,
# LIMIT_UPLOAD_KIB (0 = no limit).
set -eu

: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD:?}"
LIMIT_UPLOAD_KIB="${LIMIT_UPLOAD_KIB:-0}"

# Datasets with dated snapshots in /browse/@<name>-snapshots/<name>-YYYYmmdd-HHMMSS.
SNAPSHOTTED="immich phones harvest eeg nas-backups app-config 3d-prints forgejo-packages"
# Datasets kept as plain subvolumes of dated files (no snapshot set).
DIRECT="immich-db forgejo-backups"

# If the drive is gone, /browse is an empty directory. Fail, so the stale alert
# fires, rather than back up nothing and report success.
if [ -z "$(ls -A /browse 2>/dev/null)" ]; then
  echo "ERROR: /browse is empty (8TB drive missing?). Aborting."
  exit 1
fi

mkdir -p /data
for name in $SNAPSHOTTED; do
  # Snapshot names are always <name>-YYYYmmdd-HHMMSS, so ls|sort is safe here.
  # shellcheck disable=SC2012
  snap=$(ls -d "/browse/@$name-snapshots/$name"-* 2>/dev/null | sort | tail -1)
  if [ -z "$snap" ]; then
    echo "ERROR: no snapshot for $name. Aborting."
    exit 1
  fi
  mkdir -p "/data/$name"
  mount --bind "$snap" "/data/$name"
  echo "$name <- $(basename "$snap")"
done
for name in $DIRECT; do
  if [ -z "$(ls -A "/browse/@$name" 2>/dev/null)" ]; then
    echo "ERROR: /browse/@$name is empty. Aborting."
    exit 1
  fi
  mkdir -p "/data/$name"
  mount --bind "/browse/@$name" "/data/$name"
  echo "$name <- @$name"
done

# The repository is created once by hand (runbook "Offsite (B2)"), never here:
# an init against a wrong URL or password would quietly start a second repo.
#
# A live run, such as a manual job (concurrencyPolicy: Forbid does not see
# one), must stop this run, or both upload the same data. unlock removes only
# stale locks, and restic calls a lock stale 30 minutes after its last refresh
# (a live run refreshes every 5), so a run that died minutes ago (runbook
# "First upload") still looks live. restic has no event for a lock going
# stale, so poll: proceed once unlock clears the locks, abort if one is still
# held after the stale window, which only a live run can do.
LOCK_STALE_WAIT_S=1860 # restic's StaleLockTimeout (30 min) plus a minute
LOCK_POLL_S=60
waited=0
while :; do
  restic unlock
  # An assignment, so a failed listing stops the script under set -e instead
  # of reading as "no lock".
  locks=$(restic list locks --no-lock -q)
  [ -z "$locks" ] && break
  if [ "$waited" -ge "$LOCK_STALE_WAIT_S" ]; then
    echo "ERROR: another restic run holds the repository. Aborting."
    exit 1
  fi
  echo "A lock is held; waiting up to $((LOCK_STALE_WAIT_S - waited))s for it to clear or go stale."
  sleep "$LOCK_POLL_S"
  waited=$((waited + LOCK_POLL_S))
done
restic backup --host asus-laptop --tag nightly \
  --limit-upload "$LIMIT_UPLOAD_KIB" /data

# Sunday: apply retention and drop unreferenced data.
if [ "$(date +%u)" = 7 ]; then
  restic forget --host asus-laptop --keep-daily 7 --keep-weekly 8 --keep-monthly 12 --prune
fi

# The 1st of the month: re-download and verify 5% of the stored data
# (about 30 GB, inside B2's free egress allowance).
if [ "$(date +%d)" = 01 ]; then
  restic check --read-data-subset=5%
fi

echo "=== offsite backup complete ==="
