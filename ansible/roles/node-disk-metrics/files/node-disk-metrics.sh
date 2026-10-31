#!/bin/sh
# Core dumps and local-path PV use, for node-exporter's textfile collector. Tests:
# test-node-disk-metrics.sh beside this file.
set -eu

out_dir=${1:-/var/lib/node-exporter-textfile}
storage=${STORAGE_DIR:-/var/lib/rancher/k3s/storage}
coredumps=${COREDUMP_DIR:-/var/lib/systemd/coredump}

tmp="$out_dir/.node_disk.prom.$$"
trap 'rm -f "$tmp"' EXIT
{
    echo '# HELP node_coredumps_last_hour Core dump files written in the last hour'
    echo '# TYPE node_coredumps_last_hour gauge'
    echo "node_coredumps_last_hour $(find "$coredumps" -type f -mmin -60 2>/dev/null | wc -l)"
    echo '# HELP node_coredump_bytes Disk used by stored core dumps'
    echo '# TYPE node_coredump_bytes gauge'
    bytes=$(du -sB1 "$coredumps" 2>/dev/null | cut -f1 || true)
    echo "node_coredump_bytes ${bytes:-0}"
    echo '# HELP local_path_pv_used_bytes Disk used by a local-path PV, which does not enforce its size'
    echo '# TYPE local_path_pv_used_bytes gauge'
    for dir in "$storage"/pvc-*_*_*; do
        [ -d "$dir" ] || continue
        # local-path names a directory pvc-<uid>_<namespace>_<claim>, and neither name can hold a _.
        name=${dir##*/}
        rest=${name#*_}
        echo "local_path_pv_used_bytes{volumename=\"${name%%_*}\",claim_namespace=\"${rest%%_*}\",persistentvolumeclaim=\"${rest#*_}\"} $(du -sxB1 "$dir" | cut -f1)"
    done
} > "$tmp"
chmod 644 "$tmp"
mv "$tmp" "$out_dir/node_disk.prom"
