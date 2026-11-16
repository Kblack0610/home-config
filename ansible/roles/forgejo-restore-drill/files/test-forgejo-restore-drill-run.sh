#!/bin/sh
# Offline test of forgejo-restore-drill-run.sh with a stub drill and a stub Forgejo.
# CI runs it in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/out" "$t/bin" "$t/www/api/v1"
# curl reads file:// URLs, so a directory stands in for Forgejo.
echo '{"version":"14.0.4+gitea-1.22.0"}' > "$t/www/api/v1/version"
cat > "$t/drill" <<'STUB'
#!/bin/sh
echo "$IMAGE" > "$(dirname "$0")/image"
exit "${STUB_EXIT:-0}"
STUB
chmod +x "$t/drill"

fail=0
prom=$t/out/forgejo_restore_drill.prom
run() { DRILL=$t/drill bash "$here/forgejo-restore-drill-run.sh" "$t/out" "$1" data.forgejo.org/forgejo/forgejo 2>"$t/err"; }
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', want '$3'"; fail=1; fi; }
val() { awk -v m="$1" '$1 == m {print $2}' "$prom"; }

run "file://$t/www"; check "a pass exits 0" "$?" 0
check "the image tag is the deployed version" "$(cat "$t/image")" data.forgejo.org/forgejo/forgejo:14.0.4
check "a pass reports success" "$(val forgejo_restore_drill_last_run_success)" 1
passed=$(val forgejo_restore_drill_last_success_timestamp_seconds)
check "a pass records its time" "$passed" "$(val forgejo_restore_drill_last_run_timestamp_seconds)"
check "no temp file left" "$(ls -A "$t/out" | wc -l | tr -d ' ')" 1
check "readable by node-exporter" "$(stat -c %a "$prom")" 644

sed -i "s/^forgejo_restore_drill_last_success_timestamp_seconds .*/forgejo_restore_drill_last_success_timestamp_seconds 1000/" "$prom"
STUB_EXIT=1 run "file://$t/www"; rc=$?
[ "$rc" != 0 ]; check "a failed drill exits non-zero" "$?" 0
check "a failed drill reports failure" "$(val forgejo_restore_drill_last_run_success)" 0
check "a failed drill keeps the last pass" "$(val forgejo_restore_drill_last_success_timestamp_seconds)" 1000

rm -f "$prom" "$t/image"
run "file://$t/nowhere"; rc=$?
[ "$rc" != 0 ]; check "an unreadable version fails" "$?" 0
[ ! -e "$t/image" ]; check "the drill does not run without a version" "$?" 0
check "an unreadable version reports failure" "$(val forgejo_restore_drill_last_run_success)" 0
check "a host that never passed has no last-pass series" "$(val forgejo_restore_drill_last_success_timestamp_seconds)" ""

exit $fail
