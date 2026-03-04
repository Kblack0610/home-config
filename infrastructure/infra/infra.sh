#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# infra — Homelab device/node health CLI
# =============================================================================

# Resolve symlinks to find real script directory
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

# =============================================================================
# Colors & logging
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*" >&2; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }

# =============================================================================
# Configuration
# =============================================================================

K8S_CONTEXTS=("home-k3s|local" "do-nyc3-prod|DigitalOcean")

MAC_MACHINES=(
    "mac-studio|192.168.1.4"
    "mac-mini-m1|192.168.1.7"
)

KUBECTL_TIMEOUT=5

# Temp directory for parallel data collection
TMPDIR_INFRA=""

cleanup() {
    if [[ -n "$TMPDIR_INFRA" && -d "$TMPDIR_INFRA" ]]; then
        rm -rf "$TMPDIR_INFRA"
    fi
}
trap cleanup EXIT

# =============================================================================
# Kubernetes helpers
# =============================================================================

fetch_k8s_data() {
    local ctx="$1"
    local tmpdir="$2"

    # Get nodes (name, status, roles, age)
    kubectl --context "$ctx" get nodes \
        --no-headers \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.nodes" 2>/dev/null || true

    # Get top nodes for CPU/memory
    kubectl --context "$ctx" top nodes \
        --no-headers \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.top" 2>/dev/null || true
}

parse_k8s_nodes() {
    local ctx="$1"
    local tmpdir="$2"
    local nodes_file="$tmpdir/${ctx}.nodes"
    local top_file="$tmpdir/${ctx}.top"

    if [[ ! -s "$nodes_file" ]]; then
        echo "UNREACHABLE"
        return
    fi

    # Build associative array of top data: node -> cpu% mem%
    declare -A cpu_map mem_map
    if [[ -s "$top_file" ]]; then
        while read -r name cpu_cores cpu_pct mem_bytes mem_pct _rest; do
            # kubectl top may return <unknown> for nodes without metrics
            if [[ "$cpu_pct" == *"unknown"* ]]; then cpu_pct="-"; fi
            if [[ "$mem_pct" == *"unknown"* ]]; then mem_pct="-"; fi
            cpu_map["$name"]="$cpu_pct"
            mem_map["$name"]="$mem_pct"
        done < "$top_file"
    fi

    # Parse nodes
    while read -r name status roles age _rest; do
        local cpu="${cpu_map[$name]:-"-"}"
        local mem="${mem_map[$name]:-"-"}"

        # Normalize roles
        if [[ "$roles" == *"control-plane"* ]] || [[ "$roles" == *"master"* ]]; then
            roles="master"
        elif [[ "$roles" == "<none>" ]] || [[ -z "$roles" ]]; then
            roles="worker"
        fi

        # Normalize status
        local up="UP"
        if [[ "$status" != "Ready" ]]; then
            up="DOWN"
            cpu="-"
            mem="-"
        fi

        echo "${up}|${name}|${roles}|${cpu}|${mem}|${age}"
    done < "$nodes_file"
}

# =============================================================================
# Mac machine helpers
# =============================================================================

fetch_mac_metrics() {
    local name="$1"
    local ip="$2"
    local tmpdir="$3"

    curl -s --connect-timeout 2 --max-time 5 "http://${ip}:9100/metrics" \
        > "$tmpdir/mac.${name}.metrics" 2>/dev/null || true
}

parse_mac_metrics() {
    local name="$1"
    local ip="$2"
    local tmpdir="$3"
    local metrics_file="$tmpdir/mac.${name}.metrics"

    if [[ ! -s "$metrics_file" ]]; then
        echo "DOWN|${name}|${ip}|-|-|-"
        return
    fi

    # CPU: calculate idle percentage from node_cpu_seconds_total
    local cpu_pct="-"
    cpu_pct=$(awk '
        /^node_cpu_seconds_total\{.*mode="idle"/ { idle += $2 }
        /^node_cpu_seconds_total\{/              { total += $2 }
        END { if (total > 0) printf "%d%%", 100 - (idle * 100 / total); else print "-" }
    ' "$metrics_file" 2>/dev/null)

    # Memory: macOS uses node_memory_total_bytes / node_memory_free_bytes
    # Linux uses node_memory_MemTotal_bytes / node_memory_MemAvailable_bytes
    local mem_pct="-"
    mem_pct=$(awk '
        /^node_memory_MemTotal_bytes /     { total = $2 }
        /^node_memory_MemAvailable_bytes / { avail = $2 }
        /^node_memory_total_bytes /        { total = $2 }
        /^node_memory_free_bytes /         { avail = $2 }
        END { if (total > 0) printf "%d%%", (total - avail) * 100 / total; else print "-" }
    ' "$metrics_file" 2>/dev/null)

    # Disk: root filesystem (handles scientific notation via awk)
    local disk_pct="-"
    disk_pct=$(awk '
        /^node_filesystem_size_bytes\{.*mountpoint="\/"[,}]/  { if (!size) size = $2 }
        /^node_filesystem_avail_bytes\{.*mountpoint="\/"[,}]/ { if (!avail) avail = $2 }
        END { if (size > 0) printf "%d%%", (size - avail) * 100 / size; else print "-" }
    ' "$metrics_file" 2>/dev/null)

    echo "UP|${name}|${ip}|${cpu_pct}|${mem_pct}|${disk_pct}"
}

# =============================================================================
# Local workstation helpers (reads /proc and df directly)
# =============================================================================

get_local_metrics() {
    local cpu_pct mem_pct disk_pct
    local hostname
    hostname=$(hostname)

    # CPU: sample /proc/stat twice with a brief interval
    local idle1 total1 idle2 total2
    read -r idle1 total1 < <(awk '/^cpu / { idle=$5; total=0; for(i=2;i<=NF;i++) total+=$i; print idle, total }' /proc/stat)
    sleep 0.3
    read -r idle2 total2 < <(awk '/^cpu / { idle=$5; total=0; for(i=2;i<=NF;i++) total+=$i; print idle, total }' /proc/stat)

    local diff_idle=$(( idle2 - idle1 ))
    local diff_total=$(( total2 - total1 ))
    if [[ "$diff_total" -gt 0 ]]; then
        cpu_pct="$(( 100 - (diff_idle * 100 / diff_total) ))%"
    else
        cpu_pct="-"
    fi

    # Memory from /proc/meminfo
    mem_pct=$(awk '/^MemTotal:/ { total=$2 } /^MemAvailable:/ { avail=$2 } END { if (total>0) printf "%d%%", (total-avail)*100/total; else print "-" }' /proc/meminfo)

    # Disk: root filesystem
    disk_pct=$(df / | awk 'NR==2 { gsub(/%/,"",$5); printf "%d%%", $5 }')

    echo "UP|${hostname}|${cpu_pct}|${mem_pct}|${disk_pct}"
}

# =============================================================================
# Formatting helpers
# =============================================================================

print_header() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M')
    echo ""
    echo -e " ${BOLD}Homelab Devices${NC}                                    ${DIM}${timestamp}${NC}"
}

print_cluster_header() {
    local ctx="$1"
    local label="$2"
    echo ""
    echo -e " ${BOLD}CLUSTER: ${ctx} (${label})${NC}"
    echo -e " ${DIM}──────────────────────────────────────────────────────────────────${NC}"
    printf "  ${BOLD}%-6s  %-24s %-8s %5s  %5s  %5s${NC}\n" "STATUS" "NODE" "ROLE" "CPU" "MEM" "AGE"
}

print_mac_header() {
    echo ""
    echo -e " ${BOLD}MAC MACHINES (external)${NC}"
    echo -e " ${DIM}──────────────────────────────────────────────────────────────────${NC}"
    printf "  ${BOLD}%-6s  %-24s %-16s %5s  %5s  %5s${NC}\n" "STATUS" "MACHINE" "IP" "CPU" "MEM" "DISK"
}

print_node_row() {
    local status="$1" name="$2" role="$3" cpu="$4" mem="$5" age="$6"

    local status_icon status_color status_text
    if [[ "$status" == "UP" ]]; then
        status_icon="${GREEN}●${NC}"
        status_color="${GREEN}"
        status_text="UP"
    else
        status_icon="${RED}●${NC}"
        status_color="${RED}"
        status_text="DOWN"
    fi

    printf "  ${status_icon} ${status_color}%4s${NC}  %-24s %-8s %5s  %5s  %5s\n" \
        "$status_text" "$name" "$role" "$cpu" "$mem" "$age"
}

print_mac_row() {
    local status="$1" name="$2" ip="$3" cpu="$4" mem="$5" disk="$6"

    local status_icon status_color status_text
    if [[ "$status" == "UP" ]]; then
        status_icon="${GREEN}●${NC}"
        status_color="${GREEN}"
        status_text="UP"
    else
        status_icon="${RED}●${NC}"
        status_color="${RED}"
        status_text="DOWN"
    fi

    printf "  ${status_icon} ${status_color}%4s${NC}  %-24s %-16s %5s  %5s  %5s\n" \
        "$status_text" "$name" "$ip" "$cpu" "$mem" "$disk"
}

print_local_header() {
    echo ""
    echo -e " ${BOLD}LOCAL WORKSTATION${NC}"
    echo -e " ${DIM}──────────────────────────────────────────────────────────────────${NC}"
    printf "  ${BOLD}%-6s  %-24s %5s  %5s  %5s${NC}\n" "STATUS" "HOSTNAME" "CPU" "MEM" "DISK"
}

print_local_row() {
    local status="$1" name="$2" cpu="$3" mem="$4" disk="$5"

    local status_icon status_color status_text
    if [[ "$status" == "UP" ]]; then
        status_icon="${GREEN}●${NC}"
        status_color="${GREEN}"
        status_text="UP"
    else
        status_icon="${RED}●${NC}"
        status_color="${RED}"
        status_text="DOWN"
    fi

    printf "  ${status_icon} ${status_color}%4s${NC}  %-24s %5s  %5s  %5s\n" \
        "$status_text" "$name" "$cpu" "$mem" "$disk"
}

print_summary() {
    local total="$1" healthy="$2" down="$3"
    echo ""
    local summary=" Summary: ${total} devices | ${BOLD}${GREEN}${healthy} healthy${NC}"
    if [[ "$down" -gt 0 ]]; then
        summary+=" | ${BOLD}${RED}${down} down${NC}"
    fi
    echo -e "$summary"
    echo ""
}

# =============================================================================
# Commands
# =============================================================================

cmd_nodes() {
    TMPDIR_INFRA=$(mktemp -d)

    # --- Parallel data collection -------------------------------------------
    local pids=()

    for entry in "${K8S_CONTEXTS[@]}"; do
        local ctx="${entry%%|*}"
        fetch_k8s_data "$ctx" "$TMPDIR_INFRA" &
        pids+=($!)
    done

    for entry in "${MAC_MACHINES[@]}"; do
        local mac_name="${entry%%|*}"
        local mac_ip="${entry##*|}"
        fetch_mac_metrics "$mac_name" "$mac_ip" "$TMPDIR_INFRA" &
        pids+=($!)
    done

    # Wait for all background jobs
    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    # --- Render output ------------------------------------------------------
    print_header

    local total=0 healthy=0 down=0

    for entry in "${K8S_CONTEXTS[@]}"; do
        local ctx="${entry%%|*}"
        local label="${entry##*|}"

        print_cluster_header "$ctx" "$label"

        local node_data
        node_data=$(parse_k8s_nodes "$ctx" "$TMPDIR_INFRA")

        if [[ "$node_data" == "UNREACHABLE" ]]; then
            echo -e "  ${YELLOW}Context unreachable${NC}"
        else
            while IFS='|' read -r status name role cpu mem age; do
                print_node_row "$status" "$name" "$role" "$cpu" "$mem" "$age"
                total=$((total + 1))
                if [[ "$status" == "UP" ]]; then
                    healthy=$((healthy + 1))
                else
                    down=$((down + 1))
                fi
            done <<< "$node_data"
        fi
    done

    # Mac machines
    print_mac_header
    for entry in "${MAC_MACHINES[@]}"; do
        local mac_name="${entry%%|*}"
        local mac_ip="${entry##*|}"

        local mac_data
        mac_data=$(parse_mac_metrics "$mac_name" "$mac_ip" "$TMPDIR_INFRA")

        IFS='|' read -r status name ip cpu mem disk <<< "$mac_data"
        print_mac_row "$status" "$name" "$ip" "$cpu" "$mem" "$disk"
        total=$((total + 1))
        if [[ "$status" == "UP" ]]; then
            healthy=$((healthy + 1))
        else
            down=$((down + 1))
        fi
    done

    # Local workstation
    print_local_header
    local local_data
    local_data=$(get_local_metrics)
    IFS='|' read -r status name cpu mem disk <<< "$local_data"
    print_local_row "$status" "$name" "$cpu" "$mem" "$disk"
    total=$((total + 1))
    healthy=$((healthy + 1))

    print_summary "$total" "$healthy" "$down"
}

cmd_help() {
    cat <<'EOF'
infra — Homelab device/node health CLI

USAGE:
    infra [command]

COMMANDS:
    nodes    Show all devices and their health (default)
    help     Show this help message

EXAMPLES:
    infra            # show all devices
    infra nodes      # same thing
    infra help       # show this help

DATA SOURCES:
    Kubernetes nodes   kubectl get nodes / top nodes (per context)
    Mac machines       node_exporter metrics via HTTP (:9100)
EOF
}

# =============================================================================
# Main
# =============================================================================

case "${1:-nodes}" in
    nodes)          cmd_nodes ;;
    help|--help|-h) cmd_help ;;
    *)
        log_error "Unknown command: $1"
        cmd_help
        exit 1
        ;;
esac
