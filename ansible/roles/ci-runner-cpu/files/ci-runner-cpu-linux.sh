#!/bin/sh
# CPU each CI runner is using, for node-exporter's textfile collector (the Infra tab's cores
# per job). A runner's CPU is its unit's cgroup plus the cgroups of its job containers: for a
# GitHub runner the containers that mount its work directory, for a Forgejo runner the
# FORGEJO-ACTIONS-TASK-<task>_ container of the task its journal last says it took (its job
# containers mount only docker volumes, and it takes one task at a time, so that is the one
# running). Containers come and go, so this reports the cores used since the
# previous sample, a gauge: a counter summed over them would fall each time one exits.
# Memory is the same cgroups' memory.current, summed. A runner's memory cap is the nearest
# cgroup above it with a memory.max, reported only when no other runner sits under that
# cgroup: a cap shared by several runners is not any one runner's.
#
# --disk: the bytes under each runner's working directory (its home: the checkout, the warm
# Unity Library and _work) into ci_runner_disk.prom, run hourly by ci-runner-disk.timer.
# Disk use has no event to wait on, and a du of a 60G Library is too slow for the 15s CPU timer.
# Tests: test-ci-runner-cpu.sh beside this file.
set -eu

mode=cpu
if [ "${1:-}" = --disk ]; then mode=disk; shift; fi
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
: > "$work/tasks"
: > "$work/mem"

# runner name <tab> cgroup, for each runner unit and each job container it started.
for unit in $(systemctl list-units 'actions.runner.*' 'forgejo-runner-*' --no-legend --plain --state=running | awk '{print $1}'); do
    name=${unit#actions.runner.}
    name=${name#forgejo-runner-}
    name=${name%.service}
    case $unit in forgejo-runner-*)
        # The task id of the runner's last "task <id> repo is" line, the one it runs.
        task=$(journalctl -u "$unit" -g ' msg="task [0-9]+ repo is ' -n 1 -o cat --no-pager 2>/dev/null |
            sed -n 's/.* msg="task \([0-9]*\) repo is .*/\1/p')
        if [ -n "$task" ]; then printf '%s\t%s\n' "$task" "$name" >> "$work/tasks"; fi ;;
    esac
    props=$(systemctl show -p ControlGroup -p WorkingDirectory "$unit")
    cgroup=$(printf '%s\n' "$props" | sed -n 's/^ControlGroup=//p')
    dir=$(printf '%s\n' "$props" | sed -n 's/^WorkingDirectory=//p')
    [ -n "$cgroup" ] || continue
    printf '%s\t%s\n' "$name" "$cgroup" >> "$work/units"
    if [ -n "$dir" ]; then printf '%s\t%s\n' "$dir" "$name" >> "$work/dirs"; fi
done
cat "$work/units" > "$work/cgroups"

if [ "$mode" = disk ]; then
    tmp="$out_dir/.ci_runner_disk.prom.$$"
    trap 'rm -rf "$work" "$tmp"' EXIT
    {
        echo '# HELP ci_runner_disk_bytes Bytes under a CI runner'"'"'s working directory, on its filesystem only'
        echo '# TYPE ci_runner_disk_bytes gauge'
        while IFS=$tab read -r dir name; do
            bytes=$(nice -n 19 ionice -c3 du -sxb "$dir" 2>/dev/null | cut -f1 || true)
            if [ -n "$bytes" ]; then printf 'ci_runner_disk_bytes{runner="%s"} %s\n' "$name" "$bytes"; fi
        done < "$work/dirs" | sort
        echo '# HELP ci_runner_disk_measured_seconds Unix time the runner'"'"'s directory was measured'
        echo '# TYPE ci_runner_disk_measured_seconds gauge'
        echo "ci_runner_disk_measured_seconds $(date +%s)"
    } > "$tmp"
    chmod 644 "$tmp"
    mv "$tmp" "$out_dir/ci_runner_disk.prom"
    exit 0
fi

ids=$(docker ps -q 2>/dev/null || true)
if [ -n "$ids" ] && [ -s "$work/units" ]; then
    # shellcheck disable=SC2086 # one argument per container id
    docker inspect -f '{{.State.Pid}} {{.Name}}{{range .Mounts}} {{.Source}}{{end}}' $ids 2>/dev/null |
        while read -r pid cname mounts; do
            path=$(sed -n 's/^0:://p' "$proc/$pid/cgroup" 2>/dev/null || true)
            [ -n "$path" ] || continue
            # The Forgejo runner whose task the container runs, else the runner whose work
            # directory holds one of the container's mounts.
            task=$(printf '%s\n' "$cname" | sed -n 's|^/FORGEJO-ACTIONS-TASK-\([0-9]*\)_.*|\1|p')
            name=$(awk -F'\t' -v t="$task" 't != "" && $1 == t { print $2; exit }' "$work/tasks")
            # shellcheck disable=SC2086 # one mount per line
            [ -n "$name" ] || name=$(printf '%s\n' $mounts | awk -F'\t' -v dirs="$work/dirs" '
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
    mem=$(cat "$cg$path/memory.current" 2>/dev/null || true)
    if [ -n "$mem" ]; then printf '%s\t%s\n' "$name" "$mem" >> "$work/mem"; fi
done < "$work/cgroups"

# runner name <tab> the cap of the nearest cgroup above its unit that has one.
: > "$work/caps"
while IFS=$tab read -r name path; do
    p=$path
    while [ -n "$p" ] && [ "$p" != / ]; do
        max=$(cat "$cg$p/memory.max" 2>/dev/null || true)
        case $max in ''|max) p=${p%/*} ;; *) break ;; esac
    done
    [ -n "$p" ] && [ "$p" != / ] || continue
    shared=$(awk -F'\t' -v p="$p/" 'index($2 "/", p) == 1' "$work/units" | wc -l)
    if [ "$shared" -eq 1 ]; then printf '%s\t%s\n' "$name" "$max" >> "$work/caps"; fi
done < "$work/units"

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
    echo '# HELP ci_runner_memory_bytes Memory a CI runner and its job containers hold now'
    echo '# TYPE ci_runner_memory_bytes gauge'
    awk -F'\t' '{ m[$1] += $2 } END { for (n in m) printf "ci_runner_memory_bytes{runner=\"%s\"} %d\n", n, m[n] }' "$work/mem" | sort
    echo '# HELP ci_runner_memory_max_bytes Memory cap of the cgroup that holds this runner alone'
    echo '# TYPE ci_runner_memory_max_bytes gauge'
    awk -F'\t' '{ printf "ci_runner_memory_max_bytes{runner=\"%s\"} %s\n", $1, $2 }' "$work/caps" | sort
} > "$tmp"
chmod 644 "$tmp"
mv "$tmp" "$out_dir/ci_runner.prom"

{ echo "T$tab$now"; cut -f2,3 "$work/sample"; } > "$state.new"
mv "$state.new" "$state"
