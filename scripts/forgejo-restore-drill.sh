#!/usr/bin/env bash
# Prove the newest Forgejo backup restores: unpack the newest nightly full and its
# paired DB snapshot into a scratch dir, start Forgejo on it in a container with no
# network, and require /api/healthz, /api/v1/version and a repository count that
# matches the disk. The scratch dir and container are removed on every exit.
#
# Run as root on asus-laptop, the backup host (Forgejo itself is on hp-victus):
#   sudo scripts/forgejo-restore-drill.sh [backup-dir]
# backup-dir defaults to /mnt/backups/forgejo. IMAGE defaults to the version the
# deployment runs; it is loaded from k3s containerd when docker lacks it.
#
# --network none is the isolation: the restored copy cannot push mirrors, send mail,
# fire webhooks or take runner jobs, and cron and actions are switched off as well.
# The restore it rehearses: docs/backup-runbook.md, the asus-laptop "Root" row.
set -euo pipefail

BACKUP_DIR=${1:-/mnt/backups/forgejo}
IMAGE=${IMAGE:-codeberg.org/forgejo/forgejo:14.0.4}
# Forgejo answers within seconds on a restored db; a cold disk and a 300M db
# migration check can take a few minutes. There is no readiness event to wait on
# from outside a network-less container, so poll on one bounded cadence.
READY_POLL_SECONDS=5
READY_TIMEOUT_SECONDS=600

fail() { echo "DRILL FAILED: $*" >&2; exit 1; }

FULL=$(ls -t "$BACKUP_DIR"/forgejo-full-*.tar.gz 2>/dev/null | head -1 || true)
[ -n "$FULL" ] || fail "no forgejo-full-*.tar.gz in $BACKUP_DIR"
TS=${FULL##*/forgejo-full-}; TS=${TS%.tar.gz}
DB="$BACKUP_DIR/forgejo-db-$TS.db.gz"
[ -f "$DB" ] || fail "no DB snapshot paired with $(basename "$FULL") (want $(basename "$DB"))"

# On the backup disk, beside the archives, so the 45G unpack never lands on a root disk.
WORK=$(mktemp -d "$BACKUP_DIR/../.forgejo-restore-drill.XXXXXX")
NAME=forgejo-restore-drill-$$
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

echo "drill: $(basename "$FULL") with $(basename "$DB"), into $WORK"
START=$(date +%s)
mkdir "$WORK/data"
tar -xzpf "$FULL" -C "$WORK/data"
# The full's own gitea.db is a copy taken mid-write; the paired snapshot is the
# consistent one, as in a real restore.
rm -f "$WORK/data/gitea/gitea.db" "$WORK/data/gitea/gitea.db-wal" "$WORK/data/gitea/gitea.db-shm"
gunzip -c "$DB" > "$WORK/data/gitea/gitea.db"
chown 1000:1000 "$WORK/data/gitea/gitea.db"
echo "drill: unpacked in $(( $(date +%s) - START ))s, $(du -sh "$WORK/data" | cut -f1)"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  k3s ctr -n k8s.io images export - "$IMAGE" | docker load >/dev/null
fi

docker run -d --name "$NAME" --network none \
  -v "$WORK/data:/data" \
  -e FORGEJO__server__ROOT_URL=http://127.0.0.1:3000/ \
  -e FORGEJO__server__DISABLE_SSH=true \
  -e FORGEJO__cron__ENABLED=false \
  -e FORGEJO__actions__ENABLED=false \
  -e FORGEJO__mailer__ENABLED=false \
  -e FORGEJO__mirror__ENABLED=false \
  "$IMAGE" >/dev/null

in_box() { docker exec "$NAME" "$@"; }
deadline=$(( $(date +%s) + READY_TIMEOUT_SECONDS ))
until in_box wget -qO- http://127.0.0.1:3000/api/healthz >/dev/null 2>&1; do
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || { docker logs --tail 40 "$NAME" >&2; fail "Forgejo exited"; }
  [ "$(date +%s)" -lt "$deadline" ] || { docker logs --tail 40 "$NAME" >&2; fail "no healthz in ${READY_TIMEOUT_SECONDS}s"; }
  sleep "$READY_POLL_SECONDS"
done

VERSION=$(in_box wget -qO- http://127.0.0.1:3000/api/v1/version)
echo "drill: /api/v1/version $VERSION"
case "$VERSION" in *'"version"'*) ;; *) fail "unexpected /api/v1/version reply" ;; esac

DB_REPOS=$(sqlite3 -readonly "$WORK/data/gitea/gitea.db" 'SELECT count(*) FROM repository')
DISK_REPOS=$(find "$WORK/data/git/repositories" -mindepth 2 -maxdepth 2 -type d -name '*.git' ! -name '*.wiki.git' | wc -l)
echo "drill: $DB_REPOS repositories in the db, $DISK_REPOS on disk"
[ "$DB_REPOS" -eq "$DISK_REPOS" ] || fail "db and disk disagree on the repository count"

echo "drill: OK in $(( $(date +%s) - START ))s from $(basename "$FULL")"
