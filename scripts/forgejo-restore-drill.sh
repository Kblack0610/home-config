#!/usr/bin/env bash
# Prove a Forgejo backup restores: unpack the newest nightly full that has its DB
# snapshot into a scratch dir, start Forgejo on it in a container with no network,
# and require healthz, the deployed version, the Forgejo CLI reading the restored
# db, and `git fsck` passing on every repository the db names. The scratch dir and
# container are removed on every exit.
#
# Run as root on asus-laptop, the backup host (Forgejo itself is on hp-victus):
#   sudo scripts/forgejo-restore-drill.sh [backup-dir]
# backup-dir defaults to /mnt/backups/forgejo. IMAGE defaults to the image in
# apps/forgejo/deployment.yaml beside this script; set it when running a copy.
#
# --network none keeps the copy from pushing, mailing or taking jobs; cron, actions
# and the mailer are off so it never writes into the restored repos. What it does
# not rehearse: docs/backup-runbook.md, the asus-laptop "Root" row.
set -euo pipefail

BACKUP_DIR=${1:-/mnt/backups/forgejo}
# The unpack goes here; it must not be the root disk (DiskPressure evicted the whole
# cluster on 2026-07-09).
DRILL_ROOT=${DRILL_ROOT:-/mnt/backups}
# forgejo-backup aborts under 100G free on /mnt/backups, so the drill leaves that
# much on top of its own unpack.
BACKUP_FLOOR_GB=100
# Forgejo answers within seconds on a restored db; a cold disk and its migration
# check can take minutes. A network-less container has no readiness event to wait
# on from outside, so poll on one bounded cadence.
READY_POLL_SECONDS=5
READY_TIMEOUT_SECONDS=600

fail() { echo "DRILL FAILED: $*" >&2; exit 1; }
warn() { echo "drill: WARNING: $*" >&2; }

[ "$(id -u)" -eq 0 ] || fail "run as root (it unpacks uid 1000 files and drives docker)"
for c in docker sqlite3 flock; do command -v "$c" >/dev/null || fail "$c is not installed"; done
if [ -z "${IMAGE:-}" ]; then
  manifest="$(dirname "$0")/../apps/forgejo/deployment.yaml"
  [ -f "$manifest" ] || fail "IMAGE is unset and $manifest is not beside this copy"
  IMAGE=$(sed -n 's|^ *image: *\(.*forgejo/forgejo:.*\)$|\1|p' "$manifest" | head -1)
  [ -n "$IMAGE" ] || fail "no forgejo image in $manifest"
fi
# The tag, without the @sha256 pin deployment.yaml carries.
TAG=${IMAGE%%@*}
TAG=${TAG##*:}

exec 9>/run/lock/forgejo-restore-drill.lock
flock -n 9 || fail "another drill is running"
# Holding the lock, any scratch dir left is from a drill that was killed.
rm -rf "$DRILL_ROOT"/.forgejo-restore-drill.*

[ "$(stat -c %d "$DRILL_ROOT")" != "$(stat -c %d /)" ] || fail "$DRILL_ROOT is on the root disk"

# The newest full whose DB snapshot exists. The backup job writes the full before it
# copies the snapshot, so a run that failed in between leaves a full with no pair.
FULL="" DB=""
for f in $(ls -t "$BACKUP_DIR"/forgejo-full-*.tar.gz 2>/dev/null); do
  ts=${f##*/forgejo-full-}; ts=${ts%.tar.gz}
  if [ -f "$BACKUP_DIR/forgejo-db-$ts.db.gz" ]; then FULL=$f; DB="$BACKUP_DIR/forgejo-db-$ts.db.gz"; break; fi
  warn "$(basename "$f") has no DB snapshot, so forgejo-backup failed that night; trying the one before"
done
[ -n "$FULL" ] || fail "no full with a paired DB snapshot in $BACKUP_DIR"

# A full unpacks to about 1.2x its size (44G -> 51G on 2026-11-11); ask for 2x.
NEED_GB=$(( $(stat -c %s "$FULL") * 2 / 1024 / 1024 / 1024 + BACKUP_FLOOR_GB ))
FREE_GB=$(df -PBG "$DRILL_ROOT" | awk 'NR==2 {gsub(/G/,"",$4); print $4}')
[ "$FREE_GB" -ge "$NEED_GB" ] || fail "$DRILL_ROOT has ${FREE_GB}G free, needs ${NEED_GB}G"

WORK=$(mktemp -d "$DRILL_ROOT/.forgejo-restore-drill.XXXXXX")
NAME=forgejo-restore-drill-$$
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

echo "drill: $(basename "$FULL") with $(basename "$DB"), $IMAGE, into $WORK"
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
  docker pull -q "$IMAGE" >/dev/null || fail "cannot pull $IMAGE"
fi

docker run -d --name "$NAME" --network none \
  -v "$WORK/data:/data" \
  -e FORGEJO__server__ROOT_URL=http://127.0.0.1:3000/ \
  -e FORGEJO__server__DISABLE_SSH=true \
  -e FORGEJO__cron__ENABLED=false \
  -e FORGEJO__cron_0x2E_update_mirrors__ENABLED=false \
  -e FORGEJO__cron_0x2E_git_gc_repos__ENABLED=false \
  -e FORGEJO__actions__ENABLED=false \
  -e FORGEJO__mailer__ENABLED=false \
  -e FORGEJO__mirror__ENABLED=false \
  "$IMAGE" >/dev/null

in_box() { docker exec -u git "$NAME" "$@"; }
deadline=$(( $(date +%s) + READY_TIMEOUT_SECONDS ))
until in_box wget -qO- http://127.0.0.1:3000/api/healthz >/dev/null 2>&1; do
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || { docker logs --tail 40 "$NAME" >&2; fail "Forgejo exited"; }
  [ "$(date +%s)" -lt "$deadline" ] || { docker logs --tail 40 "$NAME" >&2; fail "no healthz in ${READY_TIMEOUT_SECONDS}s"; }
  sleep "$READY_POLL_SECONDS"
done

INI=/data/gitea/conf/app.ini
for task in update_mirrors git_gc_repos; do
  in_box awk -v s="[cron.$task]" '$0==s{f=1;next} /^\[/{f=0} f&&/^ENABLED *= *false/{ok=1} END{exit !ok}' "$INI" ||
    fail "cron.$task is still enabled in the restored app.ini"
done

VERSION=$(in_box wget -qO- http://127.0.0.1:3000/api/v1/version)
echo "drill: /api/v1/version $VERSION"
case "$VERSION" in *"\"version\":\"$TAG"*) ;; *) fail "serves $VERSION, deployed is $TAG" ;; esac

USERS=$(in_box forgejo --config "$INI" admin user list | tail -n +2 | wc -l)
[ "$USERS" -gt 0 ] || fail "the Forgejo CLI reads no users from the restored db"

# The snapshot can be up to 2h older than the tar, so a repo created or deleted in
# that gap is skew, not a broken backup: warn on it, fail only on a repo that fsck rejects.
# --no-reflogs: a reflog entry whose object gc pruned (.notes, 2026-11-11) is history,
# not data a restore needs; refs and everything they reach are still checked.
DB_REPOS=$(sqlite3 -readonly "$WORK/data/gitea/gitea.db" "SELECT lower(owner_name) || '/' || lower_name FROM repository" | LC_ALL=C sort)
ON_DISK=$(cd "$WORK/data/git/repositories" && find . -mindepth 2 -maxdepth 2 -type d -name '*.git' ! -name '*.wiki.git' | sed 's|^\./||; s|\.git$||' | LC_ALL=C sort)
for r in $(LC_ALL=C comm -23 <(echo "$DB_REPOS") <(echo "$ON_DISK")); do warn "$r is in the db but not on disk"; done
for r in $(LC_ALL=C comm -13 <(echo "$DB_REPOS") <(echo "$ON_DISK")); do warn "$r is on disk but not in the db"; done
checked=0
for r in $(LC_ALL=C comm -12 <(echo "$DB_REPOS") <(echo "$ON_DISK")); do
  if ! out=$(in_box git -C "/data/git/repositories/$r.git" fsck --connectivity-only --no-reflogs --no-progress 2>&1); then
    echo "$out" | tail -n 5 >&2
    fail "git fsck rejects $r"
  fi
  checked=$((checked + 1))
done
[ "$checked" -gt 0 ] || fail "no repository is both in the db and on disk"
echo "drill: $USERS users; $checked of $(echo "$DB_REPOS" | grep -c .) repositories pass git fsck"

echo "drill: OK in $(( $(date +%s) - START ))s from $(basename "$FULL")"
