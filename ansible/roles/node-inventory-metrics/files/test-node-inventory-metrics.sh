#!/bin/sh
# Offline test of node-inventory-metrics.sh against a fixture /proc, with systemctl and docker
# stubbed on a PATH that holds only what the script needs. CI runs it in the checks workflow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
hz=$(getconf CLK_TCK); page=$(getconf PAGESIZE); sh=$(command -v sh)
mkdir -p "$t/out" "$t/bin" "$t/docker-up" "$t/docker-down"
for tool in awk cut getconf mktemp mv chmod rm timeout; do ln -s "$(command -v "$tool")" "$t/bin/$tool"; done
cat > "$t/bin/systemctl" <<'STUB'
#!/bin/sh
printf 'sshd.service loaded active running OpenSSH Daemon\n'
printf 'systemd-fsck@dev-disk-by\\x2duuid-1.service loaded active running File System Check\n'
STUB
cat > "$t/docker-up/docker" <<'STUB'
#!/bin/sh
case $1 in
stats) printf 'mailpit\t2.50%%\t12.5MiB / 15.5GiB\npostgres15\t150.00%%\t1.5GiB / 15.5GiB\nstarting\t--\t-- / --\n' ;;
ps) printf 'mailpit\taxllent/mailpit:v1.20\npostgres15\tpostgres:15\n' ;;
esac
STUB
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n' > "$t/docker-down/docker"
chmod +x "$t/bin/systemctl" "$t/docker-up/docker" "$t/docker-down/docker"

# proc <root> <pid> <comm> <utime> <stime> <starttime ticks> <rss pages>
proc() {
    mkdir -p "$1/$2"
    printf '%s (%s) S 1 1 1 0 -1 4194560 0 0 0 0 %s %s 0 0 20 0 1 0 %s 123456 %s\n' "$2" "$3" "$4" "$5" "$6" "$7" > "$1/$2/stat"
    printf '1000 %s 10 1 0 100 0\n' "$7" > "$1/$2/statm"
}
fail=0
run() { PATH="$2:$t/bin" PROC_ROOT=$1 NODE_INVENTORY_STATE="$t/state" "$sh" "$here/node-inventory-metrics.sh" "$t/out" 2>"$t/err"; }
has() { if grep -Fq -- "$2" "$t/out/node_inventory.prom"; then echo "ok   $1"; else echo "FAIL $1: no line $2"; fail=1; fi; }
hasnt() { if [ ! -f "$t/out/node_inventory.prom" ] || grep -Fq -- "$2" "$t/out/node_inventory.prom"; then echo "FAIL $1: has $2"; fail=1; else echo "ok   $1"; fi; }
cores() { awk -v t="$1" -v hz="$hz" 'BEGIN { printf "%.3f", t / hz / 10 }'; }

# First run, at uptime 1000s: no previous sample, so no cpu lines.
p=$t/proc; mkdir -p "$p"; echo '1000.00 5000.00' > "$p/uptime"
proc "$p" 100 nginx 1000 500 100 50
proc "$p" 101 nginx 200 0 100 30
proc "$p" 200 'a) "b\c' 10 0 100 1
proc "$p" 201 "$(printf 'two\nline')" 10 0 100 1
proc "$p" 300 reused 900 0 100 1
run "$p" "$t/docker-up"; rc=$?
[ "$rc" = 0 ] && echo "ok   first run exits 0" || { echo "FAIL first run exit $rc"; cat "$t/err"; fail=1; }
hasnt "the first run has no cpu lines" 'node_process_cpu_cores{'
has "the first run reports memory" "node_process_resident_bytes{comm=\"nginx\"} $((80 * page))"
has "processes are counted by comm" 'node_process_count{comm="nginx"} 2'
has "all processes are totalled" 'node_processes_total 5'

# Ten seconds on. nginx used 1000 ticks across two processes; pid 300 is now a different
# process (later starttime) that counts its own 50 ticks, not 50 - 900; pid 400 started
# since the last run; pid 500 started before it but was not seen, so it has no baseline.
echo '1010.00 5000.00' > "$p/uptime"
proc "$p" 100 nginx 1400 700 100 50
proc "$p" 101 nginx 600 0 100 30
proc "$p" 300 reused 50 0 $((1005 * hz)) 1
proc "$p" 400 newbie 30 0 $((1002 * hz)) 1
proc "$p" 500 racer 5000 0 $((500 * hz)) 1
run "$p" "$t/docker-up"; rc=$?
[ "$rc" = 0 ] && echo "ok   second run exits 0" || { echo "FAIL second run exit $rc"; cat "$t/err"; fail=1; }
has "cores are the tick delta over elapsed time" "node_process_cpu_cores{comm=\"nginx\"} $(cores 1000)"
has "a reused pid is matched by starttime" "node_process_cpu_cores{comm=\"reused\"} $(cores 50)"
has "a process started since counts its whole ticks" "node_process_cpu_cores{comm=\"newbie\"} $(cores 30)"
has "a process with no baseline counts nothing" 'node_process_cpu_cores{comm="racer"} 0.000'
has "quote, backslash and paren in a comm are escaped" 'node_process_count{comm="a) \"b\\c"} 1'
has "a newline in a comm is escaped" 'node_process_count{comm="two\nline"} 1'
has "running services are listed" 'node_service_running{unit="sshd.service"} 1'
has "a backslash in a unit name is escaped" 'node_service_running{unit="systemd-fsck@dev-disk-by\\x2duuid-1.service"} 1'
has "container cpu is percent over 100" 'node_docker_container_cpu_cores{name="mailpit",image="axllent/mailpit:v1.20"} 0.025'
has "container memory MiB to bytes" 'node_docker_container_memory_bytes{name="mailpit",image="axllent/mailpit:v1.20"} 13107200'
has "container memory GiB to bytes" 'node_docker_container_memory_bytes{name="postgres15",image="postgres:15"} 1610612736'
hasnt "a container with no reading is skipped" 'name="starting"'
[ "$(ls -A "$t/out" | wc -l)" = 1 ] && echo "ok   no temp file left" || { echo "FAIL temp file left"; fail=1; }
[ "$(stat -c %a "$t/out/node_inventory.prom")" = 644 ] && echo "ok   readable by node-exporter" || { echo "FAIL mode"; fail=1; }
[ "$(ls "$t" | grep -c '^state')" = 1 ] && echo "ok   one state file" || { echo "FAIL state files: $(ls "$t")"; fail=1; }

# A daemon that does not answer, and a host with no docker CLI: no docker lines, still exits 0.
run "$p" "$t/docker-down"; rc=$?
[ "$rc" = 0 ] && echo "ok   docker down exits 0" || { echo "FAIL docker down exit $rc"; cat "$t/err"; fail=1; }
hasnt "docker down has no docker lines" 'node_docker'
has "docker down still reports services" 'node_service_running{unit="sshd.service"} 1'
run "$p" "$t/nowhere"; rc=$?
[ "$rc" = 0 ] && echo "ok   no docker CLI exits 0" || { echo "FAIL no docker exit $rc"; cat "$t/err"; fail=1; }
hasnt "no docker CLI has no docker lines" 'node_docker'

# Cardinality: the top 15 comms by cpu union the top 15 by memory, of 40.
q=$t/proc2; rm -f "$t/state"; mkdir -p "$q"; echo '2000.00 1.00' > "$q/uptime"
i=1
while [ $i -le 20 ]; do
    n=$(printf '%02d' $i)
    proc "$q" $((1000 + i)) "busy$n" 0 0 100 1
    proc "$q" $((2000 + i)) "fat$n" 0 0 100 $(( (21 - i) * 1000 ))
    i=$((i + 1))
done
run "$q" "$t/nowhere"
echo '2010.00 1.00' > "$q/uptime"
i=1
while [ $i -le 20 ]; do proc "$q" $((1000 + i)) "busy$(printf '%02d' $i)" $(( (21 - i) * 10 )) 0 100 1; i=$((i + 1)); done
run "$q" "$t/nowhere"
has "all 40 processes are totalled" 'node_processes_total 40'
[ "$(grep -c '^node_process_count{' "$t/out/node_inventory.prom")" = 30 ] && echo "ok   30 comms reported" || { echo "FAIL comms: $(grep -c '^node_process_count{' "$t/out/node_inventory.prom")"; fail=1; }
has "the 15th busiest is in" 'node_process_count{comm="busy15"}'
hasnt "the 16th busiest is out" 'comm="busy16"'
has "the 15th largest is in" 'node_process_count{comm="fat15"}'
hasnt "the 16th largest is out" 'comm="fat16"'
exit $fail
