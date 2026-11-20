#!/bin/sh
# What runs on this host, for node-exporter's textfile collector: the busiest processes by
# comm, the running systemd services and the Docker containers. Tests:
# test-node-inventory-metrics.sh beside this file.
set -eu

out_dir=${1:-/var/lib/node-exporter-textfile}
state=${NODE_INVENTORY_STATE:-/run/node-inventory-metrics.state}
proc=${PROC_ROOT:-/proc}
top=15
# Comms listed whatever their rank (space-separated, set by the role from its one list): the
# agent harnesses, so an idle claude cannot fall below the top 15 and leave the agents class
# partial. Their totals are node_agent_*; with none named the file has no such lines.
keep=${NODE_INVENTORY_KEEP:-}

work=$(mktemp -d)
tmp="$out_dir/.node_inventory.prom.$$"
trap 'rm -rf "$work" "$tmp" "$state.$$"' EXIT
trap 'exit 1' HUP INT TERM    # dash runs the EXIT trap on a signal only through exit

systemctl list-units --type=service --state=running --no-legend --plain | awk '{ print $1 }' > "$work/units"

# Docker is optional: with no CLI, or a daemon that does not answer, the file has no docker lines.
: > "$work/images"
if ! { command -v docker >/dev/null 2>&1 &&
    timeout 10 docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' > "$work/stats" 2>/dev/null &&
    timeout 10 docker ps --format '{{.Names}}\t{{.Image}}' > "$work/images" 2>/dev/null; }; then
    : > "$work/stats"
fi

# Cores per comm since the previous run, from utime+stime in /proc/<pid>/stat. A process
# counts only when the previous run saw it (same pid and starttime, so a reused pid is not
# matched) or it started since; one that exited in between is not seen. With no previous
# run there are no cpu lines, rather than a lifetime average.
printf '%s\n' "$proc"/[0-9]* |
    LC_ALL=C awk -v now="$(cut -d' ' -f1 "$proc/uptime")" -v hz="$(getconf CLK_TCK)" -v page="$(getconf PAGESIZE)" \
        -v prev="$state" -v next_="$state.$$" -v top="$top" -v keep="$keep" -v units="$work/units" \
        -v stats="$work/stats" -v images="$work/images" '
    # Prometheus label value escaping, a byte at a time (awks disagree on backslashes in gsub).
    # A byte outside printable ASCII becomes "?": a comm can be any bytes, and one invalid
    # UTF-8 label value makes node-exporter drop the whole file.
    function esc(s,   o, i, ch) {
        o = ""
        for (i = 1; i <= length(s); i++) {
            ch = substr(s, i, 1)
            o = o (ch == "\\" ? "\\\\" : ch == "\"" ? "\\\"" : ch == "\n" ? "\\n" : ch < " " || ch > "~" ? "?" : ch)
        }
        return o
    }
    # Appends the top comms by v[] (above zero, ties to the lower name) to order[], once each.
    function pick(v,   k, c, b, done) {
        for (k = 0; k < top; k++) {
            b = ""
            for (c in v) if (v[c] > 0 && !(c in done) && (b == "" || v[c] > v[b] || (v[c] == v[b] && c < b))) b = c
            if (b == "") return
            done[b] = 1
            if (!(b in picked)) { picked[b] = 1; order[++n] = b }
        }
    }
    BEGIN {
        while ((getline l < prev) > 0) { split(l, a, " "); if (a[1] == "T") then_ = a[2]; else seen[a[1] " " a[2]] = a[3] }
        close(prev)
        elapsed = (then_ != "" && now > then_) ? now - then_ : 0
        printf "T %s\n", now > next_
    }
    {
        # comm sits between the first "(" and the last ")" and may hold spaces, parentheses
        # and newlines, so the whole file is one record.
        f = $0 "/stat"; rec = ""; n = 0
        while ((getline l < f) > 0) rec = (n++ ? rec "\n" : "") l
        close(f)
        o = index(rec, "("); p = 0
        while ((i = index(substr(rec, p + 1), ")")) > 0) p += i
        if (o == 0 || p <= o) next
        comm = substr(rec, o + 1, p - o - 1)
        split(substr(rec, p + 2), s, " ")    # s[k] is field k+2 of proc(5)
        ticks = s[12] + s[13]; start = s[20]
        pid = $0; sub(/.*\//, "", pid)
        printf "%s %s %s\n", pid, start, ticks > next_
        m = $0 "/statm"; rss = 0
        if ((getline l < m) > 0) { split(l, r, " "); rss = r[2] * page }
        close(m)
        count[comm]++; mem[comm] += rss; total++
        if (elapsed > 0) {
            k = pid " " start
            dt = (k in seen) ? ticks - seen[k] : start / hz >= then_ ? ticks : 0
            busy[comm] += dt > 0 ? dt : 0
        }
    }
    END {
        for (c in busy) cpu[c] = busy[c] / hz / elapsed
        n = 0; pick(cpu); pick(mem)
        nk = split(keep, kept, " ")
        for (k = 1; k <= nk; k++) if ((kept[k] in count) && !(kept[k] in picked)) { picked[kept[k]] = 1; order[++n] = kept[k] }
        print "# HELP node_processes_total Processes on the host"
        print "# TYPE node_processes_total gauge"
        printf "node_processes_total %d\n", total
        print "# HELP node_process_cpu_cores CPU cores used since the previous run by the processes with this comm still running"
        print "# TYPE node_process_cpu_cores gauge"
        for (k = 1; k <= n; k++) if (order[k] in cpu) printf "node_process_cpu_cores{comm=\"%s\"} %.3f\n", esc(order[k]), cpu[order[k]]
        print "# HELP node_process_resident_bytes Resident memory of the processes with this comm"
        print "# TYPE node_process_resident_bytes gauge"
        for (k = 1; k <= n; k++) printf "node_process_resident_bytes{comm=\"%s\"} %.0f\n", esc(order[k]), mem[order[k]]
        print "# HELP node_process_count Processes with this comm"
        print "# TYPE node_process_count gauge"
        for (k = 1; k <= n; k++) printf "node_process_count{comm=\"%s\"} %d\n", esc(order[k]), count[order[k]]
        if (nk > 0) {
            for (k = 1; k <= nk; k++) if (!(kept[k] in summed)) { summed[kept[k]] = 1; acpu += cpu[kept[k]]; amem += mem[kept[k]] }
            print "# HELP node_agent_cpu_cores CPU cores the always-listed agent comms used since the previous run"
            print "# TYPE node_agent_cpu_cores gauge"
            if (elapsed > 0) printf "node_agent_cpu_cores %.3f\n", acpu
            print "# HELP node_agent_resident_bytes Resident memory of the always-listed agent comms"
            print "# TYPE node_agent_resident_bytes gauge"
            printf "node_agent_resident_bytes %.0f\n", amem
        }
        print "# HELP node_service_running A systemd service unit that is running"
        print "# TYPE node_service_running gauge"
        while ((getline l < units) > 0) if (l != "") printf "node_service_running{unit=\"%s\"} 1\n", esc(l)
        while ((getline l < images) > 0) { split(l, a, "\t"); image[a[1]] = a[2] }
        while ((getline l < stats) > 0) {
            split(l, a, "\t"); split(a[3], u, " ")
            num = u[1]; sub(/[A-Za-z]+$/, "", num); unit = substr(u[1], length(num) + 1)
            mult = unit == "B" ? 1 : unit == "KiB" ? 1024 : unit == "MiB" ? 1048576 : unit == "GiB" ? 1073741824 : unit == "TiB" ? 1099511627776 : \
                unit == "kB" || unit == "KB" ? 1e3 : unit == "MB" ? 1e6 : unit == "GB" ? 1e9 : unit == "TB" ? 1e12 : 0
            pct = a[2]; sub(/%$/, "", pct)
            if (a[1] == "" || num !~ /^[0-9.]+$/ || mult == 0 || pct !~ /^[0-9.]+$/) continue
            ln = "{name=\"" esc(a[1]) "\",image=\"" esc(image[a[1]]) "\"}"
            dcpu[++nd] = "node_docker_container_cpu_cores" ln " " sprintf("%.3f", pct / 100)
            dmem[nd] = "node_docker_container_memory_bytes" ln " " sprintf("%.0f", num * mult)
        }
        if (nd) {
            print "# HELP node_docker_container_cpu_cores CPU cores a Docker container is using"
            print "# TYPE node_docker_container_cpu_cores gauge"
            for (k = 1; k <= nd; k++) print dcpu[k]
            print "# HELP node_docker_container_memory_bytes Memory a Docker container is using"
            print "# TYPE node_docker_container_memory_bytes gauge"
            for (k = 1; k <= nd; k++) print dmem[k]
        }
    }' > "$tmp"
chmod 644 "$tmp"
mv "$tmp" "$out_dir/node_inventory.prom"
mv "$state.$$" "$state"
