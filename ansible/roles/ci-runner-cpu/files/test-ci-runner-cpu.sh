#!/bin/sh
# Offline test of ci-runner-cpu-linux.sh and ci-runner-cpu-mac.sh against scratch trees, with
# systemctl, docker and ps faked on PATH. CI runs it in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
fail=0
has() { if grep -Eq -- "$2" "$t/out/ci_runner.prom"; then echo "ok   $1"; else echo "FAIL $1: no match for $2"; cat "$t/out/ci_runner.prom"; fail=1; fi; }
hasnt() { if grep -Eq -- "$2" "$t/out/ci_runner.prom"; then echo "FAIL $1: matched $2"; fail=1; else echo "ok   $1"; fi; }

# --- Linux -------------------------------------------------------------------------------
mkdir -p "$t/bin" "$t/out" "$t/proc/101" "$t/proc/102" "$t/proc/103" "$t/proc/104" "$t/proc/105"
cat > "$t/bin/systemctl" <<'EOF'
#!/bin/sh
case "$1" in
list-units) printf '%s\n' 'actions.runner.h-unity-pool-1.service loaded active running GitHub runner' \
                         'actions.runner.h-light-1.service loaded active running GitHub runner' \
                         'forgejo-runner-h-forgejo-1.service loaded active running Forgejo runner' \
                         'forgejo-runner-h-forgejo-2.service loaded active running Forgejo runner' ;;
show) case "$*" in
      *unity-pool-1*) printf '%s\n' 'ControlGroup=/ci.slice/u1/actions.runner.h-unity-pool-1.service' 'WorkingDirectory=/w/unity-pool-1' ;;
      *light-1*) printf '%s\n' 'ControlGroup=/ci.slice/ci-linux.slice/actions.runner.h-light-1.service' 'WorkingDirectory=/w/light-1' ;;
      *forgejo-1*) printf '%s\n' 'ControlGroup=/ci.slice/ci-linux.slice/forgejo-runner-h-forgejo-1.service' 'WorkingDirectory=/w/forgejo-1' ;;
      *forgejo-2*) printf '%s\n' 'ControlGroup=/ci.slice/ci-linux.slice/forgejo-runner-h-forgejo-2.service' 'WorkingDirectory=/w/forgejo-2' ;;
      esac ;;
esac
EOF
# forgejo-1 last took task 17423; forgejo-2 last took 17419, whose container is gone.
cat > "$t/bin/journalctl" <<'EOF'
#!/bin/sh
case "$*" in
*forgejo-1*) echo 'time="2026-11-18T11:43:05-08:00" level=info msg="task 17423 repo is kb/hc https://data.forgejo.org http://forge:3000"' ;;
*forgejo-2*) echo 'time="2026-11-18T11:43:04-08:00" level=info msg="task 17419 repo is kb/hc https://data.forgejo.org http://forge:3000"' ;;
esac
EOF
chmod +x "$t/bin/journalctl"
cat > "$t/bin/docker" <<'EOF'
#!/bin/sh
case "$1" in
ps) echo c1; echo c2; echo c3; echo c4; echo c5 ;;
# c1 is pool-1's job; c2 mounts nothing of a runner's; c3 is a pool-10 path, which must not read as pool-1.
# c4 is forgejo-1's task, with only docker volumes mounted; c5 is a task no runner says it runs.
inspect) printf '%s\n' '101 /c1 /var/lib/unity-machine-id /w/unity-pool-1/_work/repo' '102 /c2 /elsewhere' \
                       '103 /c3 /w/unity-pool-10/_work/repo' \
                       '104 /FORGEJO-ACTIONS-TASK-17423_WORKFLOW-ab_JOB-lint /var/lib/docker/volumes/x/_data /var/run/docker.sock' \
                       '105 /FORGEJO-ACTIONS-TASK-999_WORKFLOW-cd_JOB-old /var/lib/docker/volumes/y/_data' ;;
esac
EOF
chmod +x "$t/bin/systemctl" "$t/bin/docker"
cg() { mkdir -p "$t/cg$1"; echo "usage_usec $2" > "$t/cg$1/cpu.stat"; }
echo '0::/ci.slice/u1/docker-c1.scope' > "$t/proc/101/cgroup"
echo '0::/ci.slice/docker-c2.scope' > "$t/proc/102/cgroup"
echo '0::/ci.slice/u10/docker-c3.scope' > "$t/proc/103/cgroup"
echo '0::/ci.slice/ci-linux.slice/docker-c4.scope' > "$t/proc/104/cgroup"
echo '0::/ci.slice/ci-linux.slice/docker-c5.scope' > "$t/proc/105/cgroup"
cg /ci.slice/u1/actions.runner.h-unity-pool-1.service 1000000
cg /ci.slice/ci-linux.slice/actions.runner.h-light-1.service 1000000
cg /ci.slice/ci-linux.slice/forgejo-runner-h-forgejo-1.service 1000000
cg /ci.slice/ci-linux.slice/forgejo-runner-h-forgejo-2.service 1000000
cg /ci.slice/u1/docker-c1.scope 1000000
cg /ci.slice/docker-c2.scope 1000000
cg /ci.slice/u10/docker-c3.scope 1000000
cg /ci.slice/ci-linux.slice/docker-c4.scope 1000000
cg /ci.slice/ci-linux.slice/docker-c5.scope 1000000
echo '2-4,10-12' > "$t/cg/ci.slice/u1/actions.runner.h-unity-pool-1.service/cpuset.cpus.effective"
linux() {
    echo "$1 0" > "$t/proc/uptime"
    PATH="$t/bin:$PATH" CGROUP_ROOT="$t/cg" PROC_ROOT="$t/proc" CI_RUNNER_CPU_STATE="$t/state" \
        sh "$here/ci-runner-cpu-linux.sh" "$t/out" 2> "$t/err"
}

linux 100.00; rc=$?
[ "$rc" = 0 ] && echo "ok   first sample exits 0" || { echo "FAIL first sample exit $rc"; cat "$t/err"; fail=1; }
hasnt "the first sample guesses no busy figure" '^ci_runner_cores_busy\{'
has "a pinned slot reports its CPUs" '^ci_runner_cpus_allowed\{runner="h-unity-pool-1"\} 6$'
hasnt "a unit with no cpuset reports no CPUs" 'cpus_allowed\{runner="h-light-1"\}'

# Five seconds on: the unit used 5 CPU-seconds and its container 10, so 3 cores.
cg /ci.slice/u1/actions.runner.h-unity-pool-1.service 6000000
cg /ci.slice/u1/docker-c1.scope 11000000
cg /ci.slice/docker-c2.scope 51000000
cg /ci.slice/u10/docker-c3.scope 51000000
# forgejo-1's daemon used 0.5 CPU-seconds and its task's container 5, so 1.1 cores;
# c5 used 50, which no runner owns.
cg /ci.slice/ci-linux.slice/forgejo-runner-h-forgejo-1.service 1500000
cg /ci.slice/ci-linux.slice/docker-c4.scope 6000000
cg /ci.slice/ci-linux.slice/docker-c5.scope 51000000
linux 105.00; rc=$?
[ "$rc" = 0 ] && echo "ok   second sample exits 0" || { echo "FAIL second sample exit $rc"; cat "$t/err"; fail=1; }
has "unit plus its job container" '^ci_runner_cores_busy\{runner="h-unity-pool-1"\} 3\.00$'
has "an idle runner reads zero" '^ci_runner_cores_busy\{runner="h-light-1"\} 0\.00$'
has "a Forgejo runner plus its task's container" '^ci_runner_cores_busy\{runner="h-forgejo-1"\} 1\.10$'
has "a Forgejo runner whose task ended reads zero" '^ci_runner_cores_busy\{runner="h-forgejo-2"\} 0\.00$'
[ "$(ls -A "$t/out" | wc -l)" = 1 ] && echo "ok   no temp file left" || { echo "FAIL temp file left"; fail=1; }

# Memory: pool-1's slice u1 caps it alone at 14G; ci-linux.slice caps three runners together.
echo 1000 > "$t/cg/ci.slice/u1/actions.runner.h-unity-pool-1.service/memory.current"
# c1 holds 5000 of which 1000 is inactive page cache, so its working set is 4000.
echo 5000 > "$t/cg/ci.slice/u1/docker-c1.scope/memory.current"
printf 'active_file 200\ninactive_file 1000\n' > "$t/cg/ci.slice/u1/docker-c1.scope/memory.stat"
echo 8000 > "$t/cg/ci.slice/u10/docker-c3.scope/memory.current"
echo 300 > "$t/cg/ci.slice/ci-linux.slice/actions.runner.h-light-1.service/memory.current"
echo max > "$t/cg/ci.slice/u1/actions.runner.h-unity-pool-1.service/memory.max"
echo 15032385536 > "$t/cg/ci.slice/u1/memory.max"
echo 21474836480 > "$t/cg/ci.slice/ci-linux.slice/memory.max"
linux 110.00
has "a runner's memory is its unit plus its job container" '^ci_runner_memory_bytes\{runner="h-unity-pool-1"\} 5000$'
has "a runner with no container is its unit's memory" '^ci_runner_memory_bytes\{runner="h-light-1"\} 300$'
has "the cap is the nearest cgroup above with one" '^ci_runner_memory_max_bytes\{runner="h-unity-pool-1"\} 15032385536$'
hasnt "a cap three runners share is no one runner's" 'memory_max_bytes\{runner="h-light-1"\}'

# A job container of pool-1 outside its slice u1: the cap does not bound the whole figure.
sed -i 's|/c2 /elsewhere|/c2 /w/unity-pool-1/_work/other|' "$t/bin/docker"
echo 100 > "$t/cg/ci.slice/docker-c2.scope/memory.current"
linux 115.00
has "a container outside the slice still counts" '^ci_runner_memory_bytes\{runner="h-unity-pool-1"\} 5100$'
hasnt "a cap that misses one of its containers is not the runner's" 'memory_max_bytes\{runner="h-unity-pool-1"\}'

# --disk: bytes under each runner's working directory.
mkdir -p "$t/w/unity-pool-1/_work/Library" "$t/w/light-1"
head -c 300000 /dev/zero > "$t/w/unity-pool-1/_work/Library/a"
cat > "$t/bin/systemctl" <<EOF
#!/bin/sh
case "\$1" in
list-units) printf '%s\n' 'actions.runner.h-unity-pool-1.service loaded active running GitHub runner' \
                         'actions.runner.h-light-1.service loaded active running GitHub runner' \
                         'actions.runner.h-gone-1.service loaded active running GitHub runner' \
                         'actions.runner.h-root-1.service loaded active running GitHub runner' \
                         'forgejo-runner-h-forgejo-9.service loaded active running Forgejo runner' ;;
show) case "\$*" in
      *forgejo-9*) printf '%s\n' 'ControlGroup=/f9' 'WorkingDirectory=$t/w/light-1' ;;
      *root-1*) printf '%s\n' 'ControlGroup=/r1' 'WorkingDirectory=/' ;;
      *unity-pool-1*) printf '%s\n' 'ControlGroup=/u1' 'WorkingDirectory=$t/w/unity-pool-1' ;;
      *light-1*) printf '%s\n' 'ControlGroup=/l1' 'WorkingDirectory=$t/w/light-1' ;;
      *gone-1*) printf '%s\n' 'ControlGroup=/g1' 'WorkingDirectory=$t/w/gone-1' ;;
      esac ;;
esac
EOF
printf '#!/bin/sh\necho journalctl >> "%s"\n' "$t/calls" > "$t/bin/journalctl"
PATH="$t/bin:$PATH" CGROUP_ROOT="$t/cg" PROC_ROOT="$t/proc" CI_RUNNER_CPU_STATE="$t/state" \
    sh "$here/ci-runner-cpu-linux.sh" --disk "$t/out" 2> "$t/err"; rc=$?
[ "$rc" = 0 ] && echo "ok   disk exits 0" || { echo "FAIL disk exit $rc"; cat "$t/err"; fail=1; }
d="$t/out/ci_runner_disk.prom"
bytes=$(sed -n 's/^ci_runner_disk_bytes{runner="h-unity-pool-1"} //p' "$d")
[ "${bytes:-0}" -ge 300000 ] && echo "ok   a slot's disk counts its Library" || { echo "FAIL pool-1 disk ${bytes:-none}"; cat "$d"; fail=1; }
grep -q '^ci_runner_disk_bytes{runner="h-light-1"} ' "$d" && echo "ok   an empty home still reads" || { echo "FAIL light-1 missing"; fail=1; }
grep -q 'runner="h-gone-1"' "$d" && { echo "FAIL a missing directory reads a figure"; fail=1; } || echo "ok   a missing directory reads no figure"
grep -q 'runner="h-root-1"' "$d" && { echo "FAIL a home of / was walked"; fail=1; } || echo "ok   a home of / is never walked"
# A journalctl that fails proves disk mode never asks the journal for Forgejo tasks.
grep -q journalctl "$t/calls" 2>/dev/null && { echo "FAIL disk mode read the journal"; fail=1; } || echo "ok   disk mode reads no journal"
grep -Eq '^ci_runner_disk_measured_seconds [0-9]+$' "$d" && echo "ok   the run says when it measured" || { echo "FAIL no measured time"; fail=1; }
grep -q '^ci_runner_cores_busy' "$d" && { echo "FAIL disk mode wrote CPU"; fail=1; } || echo "ok   disk mode leaves the CPU file alone"

# --- macOS -------------------------------------------------------------------------------
rm -rf "${t:?}/out" "${t:?}/bin"
mkdir -p "$t/out" "$t/bin" "$t/r/pool-1" "$t/r/pool-2"
printf '\357\273\277{\n  "agentId": 7,\n  "agentName": "mac-unity-pool-1",\n  "poolId": 1\n}\n' > "$t/r/pool-1/.runner"
printf '{"agentName": "mac-unity-pool-2"}\n' > "$t/r/pool-2/.runner"
cat > "$t/bin/ps" <<EOF
#!/bin/sh
cat <<'PS'
   10     1   0.0 $t/r/pool-1/bin/Runner.Listener run --startuptype service
   11    10   1.0 $t/r/pool-1/bin.2.338.2/Runner.Worker spawnclient 159 162
   12    11   0.0 timeout -k 60 900 /Applications/Unity/Unity.app/Contents/MacOS/Unity -batchmode
   13    12 2154.2 /Applications/Unity/Unity.app/Contents/MacOS/Unity -batchmode -projectPath $t/r/pool-1/_work/x
   20     1   0.0 $t/r/pool-2/bin/Runner.Listener run --startuptype service
   30     1  50.0 /usr/bin/something-else
PS
EOF
chmod +x "$t/bin/ps"
PATH="$t/bin:$PATH" sh "$here/ci-runner-cpu-mac.sh" "$t/out" 2> "$t/err"; rc=$?
[ "$rc" = 0 ] && echo "ok   mac exits 0" || { echo "FAIL mac exit $rc"; cat "$t/err"; fail=1; }
has "the job's whole process tree counts" '^ci_runner_cores_busy\{runner="mac-unity-pool-1"\} 21\.55$'
has "an idle Mac runner reads zero" '^ci_runner_cores_busy\{runner="mac-unity-pool-2"\} 0\.00$'
hasnt "a process outside every runner is not counted" '0\.50'
exit $fail
