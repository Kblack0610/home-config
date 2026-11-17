set -eu
apk add --no-cache curl >/dev/null

# Closed allowlist. Never refs/, never objects/pack/, never
# the database: an orphaned lock is crash debris, but a ref
# lock removed under a live writer would be data loss.
NAMES="commit-graph-chain.lock index.lock packed-refs.lock config.lock HEAD.lock"
GLOBS="tmp_graph_* tmp_pack_* tmp_idx_* gc.log"

FOUND=""
for n in $NAMES; do
  FOUND="$FOUND $(find /data/git/repositories -type f -name "$n" -mmin +"$STALE_MINUTES" 2>/dev/null || true)"
done
for g in $GLOBS; do
  FOUND="$FOUND $(find /data/git/repositories -type f -name "$g" -mmin +"$STALE_MINUTES" 2>/dev/null || true)"
done

# shellcheck disable=SC2086
set -- $FOUND
if [ "$#" -eq 0 ]; then
  echo "no orphaned git locks older than ${STALE_MINUTES}m"
  exit 0
fi

echo "reaping $# orphaned file(s):"
REPORT=""
for f in "$@"; do
  echo "  $f"
  REPORT="$REPORT
$f"
  rm -f "$f"
done

# Reaping is never silent. A repo that needs reaping twice is
# the signal that something upstream is broken, and that is
# only visible if every reap is reported.
if ! curl -sf -X POST "$NTFY_TOPIC" \
  -H "Title: Forgejo: reaped $# orphaned git lock(s)" \
  -H "Priority: default" \
  -H "Tags: warning,git" \
  -d "Removed after ${STALE_MINUTES}m idle (2x git.timeout MIGRATE). A repeat on the same repo means a process is dying mid-operation:
$REPORT"; then
  echo "FATAL: could not deliver the finding to $NTFY_TOPIC"
  exit 1
fi
echo "reported $# reaped file(s) to ntfy"
exit 0
