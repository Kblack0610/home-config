#!/bin/sh
# Offline test of node-disk-metrics.sh against a scratch tree. CI runs it in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/out" "$t/core" "$t/storage/pvc-aaa_frigate_frigate-media" "$t/storage/pvc-bbb_forgejo_forgejo-data" "$t/storage/not-a-pv"
head -c 3000000 /dev/zero > "$t/storage/pvc-aaa_frigate_frigate-media/f"
head -c 2000 /dev/zero > "$t/core/new.zst"; head -c 2000 /dev/zero > "$t/core/old.zst"
touch -d '3 hours ago' "$t/core/old.zst"

fail=0
run() { STORAGE_DIR=$1 COREDUMP_DIR=$2 sh "$here/node-disk-metrics.sh" "$t/out" 2>"$t/err"; }
has() { if grep -Eq -- "$2" "$t/out/node_disk.prom"; then echo "ok   $1"; else echo "FAIL $1: no match for $2"; fail=1; fi; }
hasnt() { if grep -Eq -- "$2" "$t/out/node_disk.prom"; then echo "FAIL $1: matched $2"; fail=1; else echo "ok   $1"; fi; }

run "$t/storage" "$t/core"; rc=$?
[ "$rc" = 0 ] && echo "ok   exits 0" || { echo "FAIL exit $rc"; cat "$t/err"; fail=1; }
has "only the recent core dump counts" '^node_coredumps_last_hour 1$'
has "core dump bytes cover both files" '^node_coredump_bytes [0-9]{4,}$'
has "PV labels parse" 'local_path_pv_used_bytes\{volumename="pvc-aaa",claim_namespace="frigate",persistentvolumeclaim="frigate-media"\} [0-9]{7,}$'
has "second PV is listed" 'claim_namespace="forgejo",persistentvolumeclaim="forgejo-data"'
hasnt "a directory that is not a PV is skipped" 'not-a-pv'
[ "$(ls -A "$t/out" | wc -l)" = 1 ] && echo "ok   no temp file left" || { echo "FAIL temp file left"; fail=1; }
[ "$(stat -c %a "$t/out/node_disk.prom")" = 644 ] && echo "ok   readable by node-exporter" || { echo "FAIL mode"; fail=1; }

# A node with no PVs and no core dump directory still reports, so a Pi reads as zero and not as missing.
run "$t/nothing" "$t/nothing"; rc=$?
[ "$rc" = 0 ] && echo "ok   an empty node exits 0" || { echo "FAIL empty node exit $rc"; cat "$t/err"; fail=1; }
has "an empty node reports zero core dumps" '^node_coredumps_last_hour 0$'
has "an empty node reports zero bytes" '^node_coredump_bytes 0$'
exit $fail
