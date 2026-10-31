#!/bin/sh
# mirror-snapshot.sh - mirror a NAS dataset onto the 8TB drive, then take a
# dated read-only btrfs snapshot and prune to the newest $KEEP.
#
# Shipped as the `nas-mirror-script` ConfigMap and run by every 8TB mirror
# CronJob in this namespace (3d-prints-backup, phones-backup,
# forgejo-packages-backup). The CronJob
# supplies the three mounts and the env:
#   /source       the dataset, mounted read-only
#   /backup-dest  an 8TB btrfs subvolume (the mirror)
#   /snapshots    an 8TB btrfs subvolume (holds the dated snapshots)
#   NAME          snapshot prefix, e.g. 3d-prints -> 3d-prints-YYYYmmdd-HHMMSS
#   KEEP          snapshots to retain (default 14)
set -e

: "${NAME:?NAME is required}"
KEEP="${KEEP:-14}"

apk add --no-cache rsync btrfs-progs

# -- Dest mount-safety guard -------------------------------------------------
# `mountpoint` alone is NOT sufficient. When the USB drive is physically
# disconnected the filesystem stays MOUNTED: mountpoint returns true, df
# reports the old size, and only real reads fail with EIO. That is what
# happened on 2026-08-09 09:39:43 (`usb 4-2: USB disconnect`) and it let the
# immich backup on the same drive run "successfully guarded" for two days
# while writing nothing. Probing with a real write distinguishes mounted from
# present.
require_live_mount() {
  dir="$1"
  mountpoint -q "$dir" || {
    echo "ERROR: $dir is not mounted. Aborting to avoid filling root fs."
    exit 1
  }
  if ! touch "$dir/.write-probe" 2>/dev/null; then
    echo "ERROR: $dir is mounted but not writable - the backing device is gone (stale mount). Aborting."
    exit 1
  fi
  rm -f "$dir/.write-probe"
}

require_live_mount /backup-dest
require_live_mount /snapshots

# A hostPath is always a mountpoint inside the container, so the check above
# cannot see a missing host mount. If the subvolume is absent, the mirror would
# land on the node's root disk. The snapshot step needs a subvolume anyway, so
# fail here, before any data moves.
for dir in /backup-dest /snapshots; do
  btrfs subvolume show "$dir" >/dev/null 2>&1 || {
    echo "ERROR: $dir is not a btrfs subvolume (host mount missing?). Aborting."
    exit 1
  }
done

# -- Source-safety guard -----------------------------------------------------
# The source lives on a NAS share (a separate mount). If that mount is gone,
# /source would be empty and rsync --delete would WIPE the backup. Abort if
# the source has no entries.
if [ -z "$(ls -A /source 2>/dev/null)" ]; then
  echo "ERROR: /source is empty (NAS share unmounted?). Aborting to protect the backup."
  exit 1
fi

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
echo "=== $NAME backup starting: $TIMESTAMP ==="

# -- Step 1: rsync mirror (read-only source) ---------------------------------
echo "Syncing $NAME..."
rsync -aH --delete --stats /source/ /backup-dest/
echo "rsync complete"

# -- Step 2: btrfs read-only snapshot ----------------------------------------
SNAP_NAME="$NAME-$TIMESTAMP"
btrfs subvolume snapshot -r /backup-dest "/snapshots/$SNAP_NAME"
echo "Snapshot created: $SNAP_NAME"

# -- Step 3: prune old snapshots (keep $KEEP) --------------------------------
# Snapshot names are always $NAME-YYYYmmdd-HHMMSS, so ls|sort is safe here.
# shellcheck disable=SC2012
SNAP_COUNT=$(ls -d "/snapshots/$NAME"-* 2>/dev/null | wc -l)
if [ "$SNAP_COUNT" -gt "$KEEP" ]; then
  REMOVE=$((SNAP_COUNT - KEEP))
  echo "Pruning $REMOVE old snapshots..."
  # shellcheck disable=SC2012
  ls -d "/snapshots/$NAME"-* | sort | head -n "$REMOVE" | while read -r SNAP; do
    btrfs subvolume delete "$SNAP" && echo "Deleted: $SNAP"
  done
fi

echo "=== $NAME backup complete: $TIMESTAMP ==="
