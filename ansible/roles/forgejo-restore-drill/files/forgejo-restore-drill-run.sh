#!/usr/bin/env bash
# Run by forgejo-restore-drill.service: drill against the Forgejo version deployed now,
# and record the outcome for node-exporter. The Sentinel watch forgejo-restore-drill
# pages when the last run failed or none passed in 8 days.
#   forgejo-restore-drill-run <textfile-dir> <forgejo-url> <image-repo>
# DRILL overrides the drill script (the offline test uses a stub).
set -uo pipefail
out_dir=$1 url=$2 repo=$3
drill=${DRILL:-/usr/local/lib/forgejo-restore-drill/forgejo-restore-drill.sh}
prom=$out_dir/forgejo_restore_drill.prom

# A failed run keeps the time of the last pass, so "not passed in 8 days" survives it.
last_ok=$(awk '$1 == "forgejo_restore_drill_last_success_timestamp_seconds" {print $2}' "$prom" 2>/dev/null)
start=$(date +%s)
ok=0
# The tag is the deployed version: a host copy of the manifest goes stale on every
# Forgejo upgrade, and an older binary refuses a db a newer one migrated.
version=$(curl -fsS --max-time 30 "$url/api/v1/version" | sed -n 's/.*"version":"\([^"+]*\).*/\1/p')
if [ -z "$version" ]; then
  echo "DRILL FAILED: cannot read the deployed version from $url/api/v1/version" >&2
elif IMAGE="$repo:$version" "$drill"; then
  ok=1
fi
end=$(date +%s)
[ "$ok" = 1 ] && last_ok=$end

tmp=$(mktemp "$out_dir/.forgejo_restore_drill.XXXXXX")
{
  echo "# HELP forgejo_restore_drill_last_run_success 1 if the last Forgejo restore drill passed."
  echo "# TYPE forgejo_restore_drill_last_run_success gauge"
  echo "forgejo_restore_drill_last_run_success $ok"
  echo "# HELP forgejo_restore_drill_last_run_timestamp_seconds When the last Forgejo restore drill finished."
  echo "# TYPE forgejo_restore_drill_last_run_timestamp_seconds gauge"
  echo "forgejo_restore_drill_last_run_timestamp_seconds $end"
  echo "# HELP forgejo_restore_drill_last_run_duration_seconds How long the last Forgejo restore drill took."
  echo "# TYPE forgejo_restore_drill_last_run_duration_seconds gauge"
  echo "forgejo_restore_drill_last_run_duration_seconds $((end - start))"
  if [ -n "$last_ok" ]; then
    echo "# HELP forgejo_restore_drill_last_success_timestamp_seconds When a Forgejo restore drill last passed."
    echo "# TYPE forgejo_restore_drill_last_success_timestamp_seconds gauge"
    echo "forgejo_restore_drill_last_success_timestamp_seconds $last_ok"
  fi
} > "$tmp"
chmod 644 "$tmp"
mv "$tmp" "$prom"
[ "$ok" = 1 ]
