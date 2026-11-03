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
mkdir -p "$t/bin" "$t/out" "$t/proc/101" "$t/proc/102" "$t/proc/103"
cat > "$t/bin/systemctl" <<'EOF'
#!/bin/sh
case "$1" in
list-units) printf '%s\n' 'actions.runner.h-unity-pool-1.service loaded active running GitHub runner' \
                         'actions.runner.h-light-1.service loaded active running GitHub runner' ;;
show) case "$*" in
      *unity-pool-1*) printf '%s\n' 'ControlGroup=/ci.slice/u1/actions.runner.h-unity-pool-1.service' 'WorkingDirectory=/w/unity-pool-1' ;;
      *light-1*) printf '%s\n' 'ControlGroup=/ci.slice/ci-linux.slice/actions.runner.h-light-1.service' 'WorkingDirectory=/w/light-1' ;;
      esac ;;
esac
EOF
cat > "$t/bin/docker" <<'EOF'
#!/bin/sh
case "$1" in
ps) echo c1; echo c2; echo c3 ;;
# c1 is pool-1's job; c2 mounts nothing of a runner's; c3 is a pool-10 path, which must not read as pool-1.
inspect) printf '%s\n' '101 /var/lib/unity-machine-id /w/unity-pool-1/_work/repo' '102 /elsewhere' '103 /w/unity-pool-10/_work/repo' ;;
esac
EOF
chmod +x "$t/bin/systemctl" "$t/bin/docker"
cg() { mkdir -p "$t/cg$1"; echo "usage_usec $2" > "$t/cg$1/cpu.stat"; }
echo '0::/ci.slice/u1/docker-c1.scope' > "$t/proc/101/cgroup"
echo '0::/ci.slice/docker-c2.scope' > "$t/proc/102/cgroup"
echo '0::/ci.slice/u10/docker-c3.scope' > "$t/proc/103/cgroup"
cg /ci.slice/u1/actions.runner.h-unity-pool-1.service 1000000
cg /ci.slice/ci-linux.slice/actions.runner.h-light-1.service 1000000
cg /ci.slice/u1/docker-c1.scope 1000000
cg /ci.slice/docker-c2.scope 1000000
cg /ci.slice/u10/docker-c3.scope 1000000
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
linux 105.00; rc=$?
[ "$rc" = 0 ] && echo "ok   second sample exits 0" || { echo "FAIL second sample exit $rc"; cat "$t/err"; fail=1; }
has "unit plus its job container" '^ci_runner_cores_busy\{runner="h-unity-pool-1"\} 3\.00$'
has "an idle runner reads zero" '^ci_runner_cores_busy\{runner="h-light-1"\} 0\.00$'
[ "$(ls -A "$t/out" | wc -l)" = 1 ] && echo "ok   no temp file left" || { echo "FAIL temp file left"; fail=1; }

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
