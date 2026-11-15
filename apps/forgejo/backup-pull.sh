#!/bin/sh
# backup-pull.sh - pull Forgejo's backup from forge-hel1 onto asus-laptop's 8TB
# drive and build the nightly full here. Run by backup-pull-cronjob.yaml, which
# supplies /ssh (backup-pull-secret.sops.yaml), the three 8TB subvolumes
# (/dest, /packages, /packages-snapshots) and SRC. The key's forced command is
# `rrsync -ro /mnt/forgejo`, so remote paths are relative to that.
#
# The full is built here from a mirror of the tree, so the VM runs only its
# hourly snapshot; the format is forgejo-backup's, which nas-backup-verify and
# scripts/forgejo-restore-drill.sh read.
set -eu

: "${SRC:?SRC is required}"
KEEP=${KEEP:-3}                 # fulls kept; the drive is shared with every other mirror
MIN_FREE_GB=${MIN_FREE_GB:-500} # left free after the new full, for the other tenants
SNAP_MAX_AGE=${SNAP_MAX_AGE:-7200}   # the VM's db snapshot runs hourly
PKG_KEEP=${PKG_KEEP:-4}         # weekly registry snapshots, as forgejo-packages-backup kept
PKG_EVERY_DAYS=7

apk add --no-cache rsync openssh-client tar gzip coreutils btrfs-progs

RSH="ssh -i /ssh/id_ed25519 -o UserKnownHostsFile=/ssh/known_hosts -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=60"
pull() { rsync -e "$RSH" "$@"; }
fail() { echo "ABORT: $*" >&2; exit 1; }

# -- Mount guards (the same two as apps/nas/mirror-snapshot.sh) ---------------
# A hostPath is always a mountpoint inside the pod, so only `btrfs subvolume
# show` sees a missing host mount; and a USB drive that dropped off stays
# mounted, so only a real write sees that.
for dir in /dest /packages /packages-snapshots; do
  btrfs subvolume show "$dir" >/dev/null 2>&1 || fail "$dir is not a btrfs subvolume (host mount missing?)"
  touch "$dir/.write-probe" 2>/dev/null || fail "$dir is mounted but not writable (stale mount)"
  rm -f "$dir/.write-probe"
done

TS=$(date -u +%Y%m%d-%H%M%S)
echo "=== forgejo backup pull from $SRC: $TS ==="

# A source path that lists nothing (volume not mounted on the VM, wrong root)
# must never reach an rsync --delete, which would empty the copy.
listed() { pull --list-only "$SRC:$1" | grep -c "$2" || true; }

# -- 1. Hourly db snapshots, mirrored ----------------------------------------
[ "$(listed backups/snapshots/ 'forgejo-db-.*\.db\.gz$')" -gt 0 ] || fail "no db snapshot listed on $SRC (backups/snapshots)"
mkdir -p /dest/snapshots
pull -a --delete --include='forgejo-db-*.db.gz' --exclude='*' "$SRC:backups/snapshots/" /dest/snapshots/
# shellcheck disable=SC2012  # names are forgejo-db-<ts>.db.gz
SNAP=$(ls -t /dest/snapshots/forgejo-db-*.db.gz | head -1)
SNAP_AGE=$(( $(date +%s) - $(stat -c %Y "$SNAP") ))
# A stale snapshot means the VM's hourly job is failing: a full must never pair
# an old db with newer repos.
[ "$SNAP_AGE" -le "$SNAP_MAX_AGE" ] || fail "newest db snapshot is $(( SNAP_AGE / 60 ))m old: $(basename "$SNAP")"
gzip -t "$SNAP"

# -- 2. The data tree, mirrored (the registry has its own copy, step 4) -------
[ "$(listed data/gitea/ ' gitea\.db$')" -eq 1 ] || fail "data/gitea/gitea.db not listed on $SRC"
mkdir -p /dest/tree
pull -aH --delete --numeric-ids --partial-dir=.rsync-partial \
  --exclude=/gitea/data/packages/ "$SRC:data/" /dest/tree/

# -- 3. The nightly full and its db, from the mirror --------------------------
rm -f /dest/*.tmp
# shellcheck disable=SC2012
LAST=$(ls -t /dest/forgejo-full-*.tar.gz 2>/dev/null | head -1 || true)
NEED_GB=1
if [ -n "$LAST" ]; then NEED_GB=$(( $(stat -c %s "$LAST") / 1073741824 + 1 )); fi
FREE_GB=$(df -PBG /dest | awk 'NR==2 {gsub(/G/,"",$4); print $4}')
echo "8TB drive free: ${FREE_GB}G, full: about ${NEED_GB}G, floor: ${MIN_FREE_GB}G"
[ $(( FREE_GB - NEED_GB )) -ge "$MIN_FREE_GB" ] || fail "a new full would leave under ${MIN_FREE_GB}G free on the 8TB drive"

FULL=/dest/forgejo-full-$TS.tar.gz
DB=/dest/forgejo-db-$TS.db.gz
trap 'rm -f "$FULL.tmp" "$DB.tmp"' EXIT
# The mirror is still, so any tar error is real (forgejo-backup tolerates exit
# 1 only because it reads the live tree).
tar -czf "$FULL.tmp" -C /dest/tree .
gzip -t "$FULL.tmp"
mv "$FULL.tmp" "$FULL"
cp "$SNAP" "$DB.tmp"
mv "$DB.tmp" "$DB"
echo "Full: $(basename "$FULL"), DB: $(basename "$SNAP")"

# Prune only after a good full exists, so a failing night never eats history.
# shellcheck disable=SC2012
ls -t /dest/forgejo-full-*.tar.gz | tail -n +$((KEEP+1)) | xargs -r rm -v
# shellcheck disable=SC2012
ls -t /dest/forgejo-db-*.db.gz | tail -n +$((KEEP+1)) | xargs -r rm -v

# -- 4. The package registry, add-only ----------------------------------------
# Blobs are content-addressed and never rewritten, so --ignore-existing copies
# only new ones; no --delete, so a blob removed on the VM stays here. Dot files
# are Forgejo's in-flight uploads.
[ "$(listed data/gitea/data/packages/ '.')" -gt 1 ] || fail "data/gitea/data/packages lists nothing on $SRC"
pull -a --ignore-existing --numeric-ids --partial-dir=.rsync-partial --exclude='.*' \
  "$SRC:data/gitea/data/packages/" /packages/

# A weekly read-only snapshot, named as forgejo-packages-backup named them, so
# nas-backup-verify's forgejo-packages check reads it unchanged.
# shellcheck disable=SC2012  # names are forgejo-packages-<ts>
NEWEST_PKG=$(ls -d /packages-snapshots/forgejo-packages-* 2>/dev/null | sort | tail -1 || true)
NEWEST_DAY=${NEWEST_PKG##*/forgejo-packages-}
CUTOFF_DAY=$(date -u -d "@$(( $(date +%s) - PKG_EVERY_DAYS * 86400 ))" +%Y%m%d)
if [ -z "$NEWEST_PKG" ] || [ "${NEWEST_DAY%-*}" -le "$CUTOFF_DAY" ]; then
  btrfs subvolume snapshot -r /packages "/packages-snapshots/forgejo-packages-$TS"
  # shellcheck disable=SC2012
  COUNT=$(ls -d /packages-snapshots/forgejo-packages-* | wc -l)
  if [ "$COUNT" -gt "$PKG_KEEP" ]; then
    # shellcheck disable=SC2012
    ls -d /packages-snapshots/forgejo-packages-* | sort | head -n $(( COUNT - PKG_KEEP )) | while read -r old; do
      btrfs subvolume delete "$old"
    done
  fi
fi

echo "=== forgejo backup pull complete: $TS ==="
ls -lht /dest/forgejo-full-*.tar.gz
