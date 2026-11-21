set -eu
apk add --no-cache sqlite gzip su-exec
# sqlite3 creates an empty database for a missing path.
if [ ! -s /data/gitea/gitea.db ]; then
  echo "ABORT: /data/gitea/gitea.db is missing or empty" >&2
  exit 1
fi
KEEP=48
TS=$(date +%Y%m%d-%H%M%S)
OUT=/snapshots/forgejo-db-$TS.db
trap 'rm -f "$OUT.tmp" "$OUT.gz.tmp"' EXIT
rm -f /snapshots/*.tmp
# sqlite3 runs as Forgejo's uid: as root, a run killed mid-way
# could leave root-owned -wal/-shm files Forgejo cannot open.
# VACUUM INTO reads one consistent snapshot in a single read
# transaction; in WAL mode Forgejo keeps writing meanwhile.
su-exec 1000:1000 sqlite3 /data/gitea/gitea.db "VACUUM INTO '$OUT.tmp'"
CHECK=$(su-exec 1000:1000 sqlite3 "$OUT.tmp" "PRAGMA quick_check;")
if [ "$CHECK" != ok ]; then
  echo "ABORT: snapshot failed quick_check: $CHECK" >&2
  exit 1
fi
gzip -c "$OUT.tmp" > "$OUT.gz.tmp"
mv "$OUT.gz.tmp" "$OUT.gz"
ls -t /snapshots/forgejo-db-*.db.gz | tail -n +$((KEEP+1)) | xargs -r rm -v
ls -lh "$OUT.gz"
