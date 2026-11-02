#!/bin/sh
# CPU each CI runner is using on a Mac, for node-exporter's textfile collector (the Infra
# tab's cores per job). macOS has no cgroups, so a runner's CPU is the summed %CPU of its
# Runner.Listener's process tree (the job's Unity, compilers and shells all descend from
# it). ps %CPU is already a short decaying average, so this is a gauge.
# Tests: test-ci-runner-cpu.sh beside this file.
set -eu

out_dir=${1:?usage: ci-runner-cpu-mac.sh <textfile dir>}
export LC_ALL=C

work=$(mktemp -d)
tmp="$out_dir/.ci_runner.prom.$$"
trap 'rm -rf "$work" "$tmp"' EXIT

ps -axo pid=,ppid=,%cpu=,command= > "$work/ps"

# Listener pid <tab> runner name, the name being agentName in the runner directory's .runner.
awk '{ i = index($0, "/bin/Runner.Listener "); if (i) { cmd = $0; sub(/^ *[0-9]+ +[0-9]+ +[0-9.]+ +/, "", cmd); print $1 "\t" substr(cmd, 1, index(cmd, "/bin/Runner.Listener ") - 1) } }' "$work/ps" |
    while IFS=$(printf '\t') read -r pid dir; do
        name=$(sed -n 's/.*"agentName"[^"]*"\([^"]*\)".*/\1/p' "$dir/.runner" 2>/dev/null | head -n 1)
        if [ -n "$name" ]; then printf '%s\t%s\n' "$pid" "$name"; fi
    done > "$work/roots"

{
    echo '# HELP ci_runner_cores_busy CPU cores a CI runner and its job processes are using'
    echo '# TYPE ci_runner_cores_busy gauge'
    awk -v roots="$work/roots" '
        BEGIN { while ((getline l < roots) > 0) { split(l, a, "\t"); root[a[1]] = a[2] } }
        { parent[$1] = $2; cpu[$1] = $3 }
        END {
            for (p in cpu) {
                # Walk up to a Listener; the depth bound guards against a pid cycle in a racy ps.
                q = p
                for (d = 0; d < 64 && q != "" && q != "0" && !(q in root); d++) q = parent[q]
                if (q in root) busy[root[q]] += cpu[p] / 100
            }
            for (q in root) printf "ci_runner_cores_busy{runner=\"%s\"} %.2f\n", root[q], busy[root[q]]
        }' "$work/ps" | sort
} > "$tmp"
chmod 644 "$tmp"
mv "$tmp" "$out_dir/ci_runner.prom"
