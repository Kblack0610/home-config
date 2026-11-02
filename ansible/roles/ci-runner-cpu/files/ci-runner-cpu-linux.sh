#!/bin/sh
# CPU each CI runner is using, for node-exporter's textfile collector (the Infra tab's cores
# per job). A runner's CPU is its unit's cgroup plus the cgroups of the job containers that
# mount its work directory. Containers come and go, so this reports the cores used since the
# previous sample, a gauge: a counter summed over them would fall each time one exits.
# Tests: test-ci-runner-cpu.sh beside this file.
set -eu

out_dir=${1:-/var/lib/node-exporter-textfile}
state=${CI_RUNNER_CPU_STATE:-/run/ci-runner-cpu.state}
cg=${CGROUP_ROOT:-/sys/fs/cgroup}
proc=${PROC_ROOT:-/proc}
tab=$(printf '\t')

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
: > "$work/units"
: > "$work/dirs"
: > "$work/cgroups"

# runner name <tab> cgroup, for each runner unit and each job container it started.
for unit in $(systemctl list-units 'actions.runner.*' --no-legend --plain --state=running | awk '{print $1}'); do
    name=${unit#actions.runner.}
    name=${name%.service}
    props=$(systemctl show -p ControlGroup -p WorkingDirectory "$unit")
    cgroup=$(printf '%s\n' "$props" | sed -n 's/^ControlGroup=//p')
    dir=$(printf '%s\n' "$props" | sed -n 's/^WorkingDirectory=//p')
    [ -n "$cgroup" ] || continue
    printf '%s\t%s\n' "$name" "$cgroup" >> "$work/units"
    if [ -n "$dir" ]; then printf '%s\t%s\n' "$dir" "$name" >> "$work/dirs"; fi
done
cat "$work/units" > "$work/cgroups"

ids=$(docker ps -q 2>/dev/null || true)
if [ -n "$ids" ] && [ -s "$work/dirs" ]; then
    # shellcheck disable=SC2086 # one argument per container id
    docker inspect -f '{{.State.Pid}}{{range .Mounts}} {{.Source}}{{end}}' $ids 2>/dev/null |
        while read -r pid mounts; do
            path=$(sed -n 's/^0:://p' "$proc/$pid/cgroup" 2>/dev/null || true)
            [ -n "$path" ] || continue
            # The runner whose work directory holds one of the container's mounts.
            # shellcheck disable=SC2086 # one mount per line
            name=$(printf '%s\n' $mounts | awk -F'\t' -v dirs="$work/dirs" '
                BEGIN { while ((getline l < dirs) > 0) { split(l, a, "\t"); d[a[1]] = a[2] } }
                { for (k in d) if (index($0 "/", k "/") == 1) { print d[k]; exit } }')
            if [ -n "$name" ]; then printf '%s\t%s\n' "$name" "$path" >> "$work/cgroups"; fi
        done
fi

now=$(cut -d' ' -f1 "$proc/uptime")
: > "$work/sample"
while IFS=$tab read -r name path; do
    usage=$(awk '$1 == "usage_usec" { print $2 }' "$cg$path/cpu.stat" 2>/dev/null || true)
    if [ -n "$usage" ]; then printf '%s\t%s\t%s\n' "$name" "$path" "$usage" >> "$work/sample"; fi
done < "$work/cgroups"

tmp="$out_dir/.ci_runner.prom.$$"
trap 'rm -rf "$work" "$tmp"' EXIT
{
    echo '# HELP ci_runner_cores_busy CPU cores a CI runner and its job containers used since the previous sample'
    echo '# TYPE ci_runner_cores_busy gauge'
    # A cgroup counts only when the previous sample saw it too, so a runner's first sample
    # (and a container's first and last interval) are left out rather than guessed.
    awk -F'\t' -v now="$now" -v prev="$state" '
        BEGIN { while ((getline l < prev) > 0) { split(l, a, "\t"); if (a[1] == "T") then_ = a[2]; else pu[a[1]] = a[2] } }
        { if (then_ != "" && now > then_ && ($2 in pu) && $3 >= pu[$2]) { busy[$1] += ($3 - pu[$2]) / 1e6 / (now - then_); have[$1] = 1 } }
        END { for (n in have) printf "ci_runner_cores_busy{runner=\"%s\"} %.2f\n", n, busy[n] }' "$work/sample" | sort
    echo '# HELP ci_runner_cpus_allowed CPUs the runner'"'"'s cgroup may use'
    echo '# TYPE ci_runner_cpus_allowed gauge'
    while IFS=$tab read -r name path; do
        cpus=$(cat "$cg$path/cpuset.cpus.effective" 2>/dev/null || true)
        [ -n "$cpus" ] || continue
        printf '%s\n' "$cpus" | awk -F, -v n="$name" '
            { c = 0; for (i = 1; i <= NF; i++) { split($i, r, "-"); c += (r[2] == "" ? 1 : r[2] - r[1] + 1) } }
            END { printf "ci_runner_cpus_allowed{runner=\"%s\"} %d\n", n, c }'
    done < "$work/units" | sort
} > "$tmp"
chmod 644 "$tmp"
mv "$tmp" "$out_dir/ci_runner.prom"

{ echo "T$tab$now"; cut -f2,3 "$work/sample"; } > "$state.new"
mv "$state.new" "$state"
