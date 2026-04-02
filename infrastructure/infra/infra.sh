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

K8S_CONTEXTS=("home-k3s|local" "do-nyc3-placemyparents-k8s-prod|DigitalOcean")

MAC_MACHINES=(
    "mac-studio|192.168.1.4"
    "mac-mini-m1|192.168.1.7"
)

# Standalone devices (Linux boxes not in K8s): "name|ip"
# Uses node_exporter on :9100 for metrics, falls back to ping for UP/DOWN.
STANDALONE_DEVICES=(
    "pi3-adguard|192.168.1.193"
)

# Android devices: "serial|friendly-name"
# Run `adb devices -l` to get serials. Unknown serials auto-detect name.
ANDROID_DEVICES=(
    "R3CR40W97CN|s21"
    "R3CW30A55DB|zfold5"
    # "SERIAL|pixel-fold"
    # "SERIAL|s22"
)

KUBECTL_TIMEOUT=5
SHOW_TEMP=false
SHOW_ALL=false
SHOW_AGE=false
SHOW_DISK=false
DHCP_DEVICES_FILE="${SCRIPT_DIR}/../dhcp/devices.yaml"

# Temp directory for parallel data collection
TMPDIR_INFRA=""

cleanup() {
    if [[ -n "$TMPDIR_INFRA" && -d "$TMPDIR_INFRA" ]]; then
        rm -rf "$TMPDIR_INFRA"
    fi
}
trap cleanup EXIT

# =============================================================================
# Utility helpers
# =============================================================================

format_uptime_age() {
    local seconds="${1%.*}"  # strip decimal if present
    if [[ -z "$seconds" || "$seconds" == "-" ]]; then
        echo "-"
        return
    fi
    local days=$(( seconds / 86400 ))
    if [[ "$days" -ge 1 ]]; then
        echo "${days}d"
    else
        local hours=$(( seconds / 3600 ))
        echo "${hours}h"
    fi
}

# =============================================================================
# Kubernetes helpers
# =============================================================================

fetch_k8s_data() {
    local ctx="$1"
    local tmpdir="$2"

    # Get nodes with -o wide for INTERNAL-IP (field 6)
    kubectl --context "$ctx" get nodes -o wide \
        --no-headers \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.nodes" 2>/dev/null || true

    # Get top nodes for CPU/memory
    kubectl --context "$ctx" top nodes \
        --no-headers \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.top" 2>/dev/null || true

    # Scrape node_exporter metrics from each node (only when --disk or --temp)
    if [[ ("$SHOW_DISK" == true || "$SHOW_TEMP" == true) && -s "$tmpdir/${ctx}.nodes" ]]; then
        local ne_pids=()
        while read -r name _status _roles _age _ver nodeip _rest; do
            if [[ -n "$nodeip" && "$nodeip" != "<none>" ]]; then
                curl -s --connect-timeout 2 --max-time 5 \
                    "http://${nodeip}:9100/metrics" \
                    > "$tmpdir/ne.${ctx}.${name}.metrics" 2>/dev/null &
                ne_pids+=($!)
            fi
        done < "$tmpdir/${ctx}.nodes"
        for pid in "${ne_pids[@]}"; do
            wait "$pid" 2>/dev/null || true
        done
    fi
}

fetch_apps_data() {
    local ctx="$1"
    local tmpdir="$2"

    kubectl --context "$ctx" get deploy,statefulset,daemonset,replicaset -A -o json \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.apps.workloads.json" 2>/dev/null || true

    kubectl --context "$ctx" get pods -A -o json \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.apps.pods.json" 2>/dev/null || true

    kubectl --context "$ctx" get svc -A -o json \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.apps.services.json" 2>/dev/null || true

    kubectl --context "$ctx" get ingress -A -o json \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.apps.ingress.json" 2>/dev/null || true

    if ! kubectl --context "$ctx" get ingressroute -A -o json \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.apps.ingressroutes.json" 2>/dev/null; then
        echo '{"items":[]}' > "$tmpdir/${ctx}.apps.ingressroutes.json"
    fi

    kubectl --context "$ctx" get nodes -o json \
        --request-timeout="${KUBECTL_TIMEOUT}s" \
        > "$tmpdir/${ctx}.apps.nodes.json" 2>/dev/null || true
}

parse_node_temp() {
    local metrics_file="$1"
    if [[ ! -s "$metrics_file" ]]; then
        echo "-"
        return
    fi
    awk '
    # RPi: node_thermal_zone_temp{type="cpu-thermal"}
    /^node_thermal_zone_temp\{.*type="cpu-thermal"/ { rpi = $2 }

    # Collect chip names: node_hwmon_chip_names{chip="hwmonN"} = "chipname"
    # The metric value is 1 but the label carries the chip id
    /^node_hwmon_chip_names\{/ {
        match($0, /chip="([^"]+)"/, c)
        match($0, /chip_name="([^"]+)"/, cn)
        if (c[1] && cn[1]) chips[c[1]] = cn[1]
    }

    # Collect sensor labels: node_hwmon_sensor_label{chip="hwmonN",sensor="tempN"} = "LabelName"
    # Encoded as node_hwmon_sensor_label{...label="..."...} value
    /^node_hwmon_sensor_label\{/ {
        match($0, /chip="([^"]+)"/, c)
        match($0, /sensor="([^"]+)"/, s)
        match($0, /label="([^"]+)"/, l)
        if (c[1] && s[1] && l[1]) labels[c[1] SUBSEP s[1]] = l[1]
    }

    # Collect temp values: node_hwmon_temp_celsius{chip="hwmonN",sensor="tempN"} value
    /^node_hwmon_temp_celsius\{/ {
        match($0, /chip="([^"]+)"/, c)
        match($0, /sensor="([^"]+)"/, s)
        if (c[1] && s[1]) temps[c[1] SUBSEP s[1]] = $2
    }

    END {
        # Priority 1: RPi thermal zone
        if (rpi+0 > 0) { printf "%dC", rpi; exit }

        # Priority 2: coretemp "Package id 0"
        # Priority 3: k10temp "Tctl"
        for (cs in chips) {
            cname = chips[cs]
            if (cname == "coretemp") {
                for (key in labels) {
                    if (index(key, cs SUBSEP) == 1 && labels[key] == "Package id 0") {
                        split(key, parts, SUBSEP)
                        val = temps[key]
                        if (val+0 > 0) { printf "%dC", val; exit }
                    }
                }
            }
        }
        for (cs in chips) {
            cname = chips[cs]
            if (cname == "k10temp") {
                for (key in labels) {
                    if (index(key, cs SUBSEP) == 1 && labels[key] == "Tctl") {
                        val = temps[key]
                        if (val+0 > 0) { printf "%dC", val; exit }
                    }
                }
            }
        }
        print "-"
    }
    ' "$metrics_file"
}

parse_node_disk() {
    local metrics_file="$1"
    if [[ ! -s "$metrics_file" ]]; then
        echo "-"
        return
    fi
    awk '
        /^node_filesystem_size_bytes\{.*mountpoint="\/"[,}]/  { if (!size) size = $2 }
        /^node_filesystem_avail_bytes\{.*mountpoint="\/"[,}]/ { if (!avail) avail = $2 }
        END { if (size > 0) printf "%d%%", (size - avail) * 100 / size; else print "-" }
    ' "$metrics_file"
}

parse_node_uptime() {
    local metrics_file="$1"
    if [[ ! -s "$metrics_file" ]]; then
        echo "-"
        return
    fi
    local uptime_secs
    uptime_secs=$(awk '
        /^node_time_seconds /      { time = $2 }
        /^node_boot_time_seconds / { boot = $2 }
        END { if (time > 0 && boot > 0) printf "%d", time - boot; else print "-" }
    ' "$metrics_file")
    format_uptime_age "$uptime_secs"
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

    # Parse nodes (-o wide: name status roles age version internal-ip ...)
    while read -r name status roles age _ver nodeip _rest; do
        local cpu="${cpu_map[$name]:-"-"}"
        local mem="${mem_map[$name]:-"-"}"
        local metrics_file="$tmpdir/ne.${ctx}.${name}.metrics"

        # Normalize roles
        if [[ "$roles" == *"control-plane"* ]] || [[ "$roles" == *"master"* ]]; then
            roles="master"
        elif [[ "$roles" == "<none>" ]] || [[ -z "$roles" ]]; then
            roles="worker"
        fi

        # Normalize status
        local up="UP"
        local temp="-"
        local disk="-"
        local node_age="$age"
        if [[ "$status" != "Ready" ]]; then
            up="DOWN"
            cpu="-"
            mem="-"
        else
            if [[ "$SHOW_DISK" == true ]]; then
                disk=$(parse_node_disk "$metrics_file")
            fi
            if [[ "$SHOW_TEMP" == true ]]; then
                temp=$(parse_node_temp "$metrics_file")
            fi
        fi

        echo "${up}|${name}|${roles}|${cpu}|${mem}|${disk}|${temp}|${node_age}"
    done < "$nodes_file"
}

parse_apps_context() {
    local ctx="$1"
    local tmpdir="$2"
    local workloads_file="$tmpdir/${ctx}.apps.workloads.json"
    local pods_file="$tmpdir/${ctx}.apps.pods.json"
    local services_file="$tmpdir/${ctx}.apps.services.json"
    local ingress_file="$tmpdir/${ctx}.apps.ingress.json"
    local ingressroutes_file="$tmpdir/${ctx}.apps.ingressroutes.json"
    local nodes_file="$tmpdir/${ctx}.apps.nodes.json"

    if [[ ! -s "$workloads_file" || ! -s "$pods_file" || ! -s "$services_file" || ! -s "$nodes_file" ]]; then
        echo "UNREACHABLE"
        return
    fi

    python3 - "$ctx" "$workloads_file" "$pods_file" "$services_file" "$ingress_file" "$ingressroutes_file" "$nodes_file" <<'PY'
import json
import re
import sys
from collections import defaultdict


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"items": []}


def compact(values, limit=2):
    cleaned = []
    seen = set()
    for value in values:
        if value in (None, "", "<none>"):
            continue
        if value not in seen:
            cleaned.append(str(value))
            seen.add(value)
    if not cleaned:
        return "-"
    if len(cleaned) <= limit:
        return ",".join(cleaned)
    return ",".join(cleaned[:limit]) + f",+{len(cleaned) - limit}"


def owners_for_pod(pod, deployments, replica_to_owner):
    refs = pod.get("metadata", {}).get("ownerReferences", []) or []
    if not refs:
        return None

    ref = refs[0]
    kind = ref.get("kind")
    name = ref.get("name")
    namespace = pod.get("metadata", {}).get("namespace", "")

    if kind in {"StatefulSet", "DaemonSet"}:
        return (namespace, kind, name)

    if kind == "ReplicaSet":
        owner = replica_to_owner.get((namespace, name))
        if owner:
            return owner

        if namespace in deployments:
            candidates = [dep for dep in deployments[namespace] if name.startswith(dep + "-")]
            if candidates:
                candidates.sort(key=len, reverse=True)
                return (namespace, "Deployment", candidates[0])

    return None


def parse_hosts(match):
    hosts = []
    for segment in re.findall(r"Host\(([^)]*)\)", match or ""):
        for token in segment.split(","):
            token = token.strip().strip("`'\"")
            if token:
                hosts.append(token)
    return hosts


ctx = sys.argv[1]
workloads = load_json(sys.argv[2]).get("items", [])
pods = load_json(sys.argv[3]).get("items", [])
services = load_json(sys.argv[4]).get("items", [])
ingresses = load_json(sys.argv[5]).get("items", [])
ingressroutes = load_json(sys.argv[6]).get("items", [])
nodes = load_json(sys.argv[7]).get("items", [])

node_ips = {}
for node in nodes:
    name = node.get("metadata", {}).get("name")
    ip = None
    for addr in node.get("status", {}).get("addresses", []) or []:
        if addr.get("type") == "InternalIP":
            ip = addr.get("address")
            break
    if not ip:
        for addr in node.get("status", {}).get("addresses", []) or []:
            if addr.get("type") == "ExternalIP":
                ip = addr.get("address")
                break
    if name:
        node_ips[name] = ip

deployments = defaultdict(list)
replica_to_owner = {}
workload_rows = []
workload_selectors = {}

for item in workloads:
    meta = item.get("metadata", {})
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")
    kind = item.get("kind", "")

    if kind == "ReplicaSet":
        refs = meta.get("ownerReferences", []) or []
        if refs and refs[0].get("kind") == "Deployment":
            replica_to_owner[(namespace, name)] = (namespace, "Deployment", refs[0].get("name"))
        continue

    if kind == "DaemonSet" and name.startswith("svclb-"):
        continue

    if kind == "Deployment":
        deployments[namespace].append(name)

    selector = item.get("spec", {}).get("selector", {}).get("matchLabels", {}) or {}
    workload_selectors[(namespace, kind, name)] = selector
    workload_rows.append(item)

pods_by_owner = defaultdict(list)
for pod in pods:
    owner = owners_for_pod(pod, deployments, replica_to_owner)
    if owner:
        pods_by_owner[owner].append(pod)

service_matches = defaultdict(list)
service_names = defaultdict(set)
for svc in services:
    meta = svc.get("metadata", {})
    namespace = meta.get("namespace", "")
    svc_name = meta.get("name", "")
    selector = svc.get("spec", {}).get("selector", {}) or {}
    if not selector:
        continue

    for key, workload_selector in workload_selectors.items():
        wk_namespace, _wk_kind, _wk_name = key
        if wk_namespace != namespace or not workload_selector:
            continue
        if all(workload_selector.get(k) == v for k, v in selector.items()):
            spec = svc.get("spec", {})
            ports = []
            node_ports = []
            for port in spec.get("ports", []) or []:
                target = port.get("port")
                proto = port.get("protocol", "TCP")
                ports.append(f"{target}/{proto}")
                if port.get("nodePort"):
                    node_ports.append(str(port["nodePort"]))

            lb_ips = []
            for entry in svc.get("status", {}).get("loadBalancer", {}).get("ingress", []) or []:
                if entry.get("ip"):
                    lb_ips.append(entry["ip"])
                elif entry.get("hostname"):
                    lb_ips.append(entry["hostname"])

            svc_cluster_ip = spec.get("clusterIP")
            service_matches[key].append(
                {
                    "name": svc_name,
                    "cluster_ip": svc_cluster_ip,
                    "ports": ports,
                    "external_ips": list(spec.get("externalIPs", []) or []) + lb_ips,
                    "node_ports": node_ports,
                }
            )
            service_names[key].add(svc_name)

hosts_by_workload = defaultdict(list)
for ingress in ingresses:
    namespace = ingress.get("metadata", {}).get("namespace", "")
    rules = ingress.get("spec", {}).get("rules", []) or []
    paths = []
    for rule in rules:
        host = rule.get("host")
        if host:
            paths.append(host)
        for path in rule.get("http", {}).get("paths", []) or []:
            backend = path.get("backend", {}).get("service", {}).get("name")
            if not backend:
                continue
            for key, names in service_names.items():
                if key[0] == namespace and backend in names:
                    if host:
                        hosts_by_workload[key].append(host)

for ingressroute in ingressroutes:
    namespace = ingressroute.get("metadata", {}).get("namespace", "")
    for route in ingressroute.get("spec", {}).get("routes", []) or []:
        hosts = parse_hosts(route.get("match", ""))
        services_for_route = route.get("services", []) or []
        for route_service in services_for_route:
            backend = route_service.get("name")
            if not backend:
                continue
            for key, names in service_names.items():
                if key[0] == namespace and backend in names:
                    hosts_by_workload[key].extend(hosts)

rows = []
for item in workload_rows:
    meta = item.get("metadata", {})
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")
    kind = item.get("kind", "")
    key = (namespace, kind, name)
    pods_for_workload = pods_by_owner.get(key, [])

    nodes_for_workload = []
    pod_ips = []
    for pod in pods_for_workload:
        status = pod.get("status", {}).get("phase")
        if status not in {"Running", "Pending"}:
            continue
        node_name = pod.get("spec", {}).get("nodeName")
        if node_name:
            node_ip = node_ips.get(node_name)
            nodes_for_workload.append(f"{node_name}({node_ip})" if node_ip else node_name)
        pod_ip = pod.get("status", {}).get("podIP")
        if pod_ip:
            pod_ips.append(pod_ip)

    svc_entries = service_matches.get(key, [])
    service_values = []
    external_values = []
    for svc in svc_entries:
        ports = ",".join(svc["ports"]) if svc["ports"] else "-"
        cluster_ip = svc["cluster_ip"] if svc["cluster_ip"] and svc["cluster_ip"] != "None" else "-"
        service_values.append(f"{svc['name']}@{cluster_ip}:{ports}")
        external_values.extend(svc["external_ips"])
        external_values.extend([f"nodeport:{port}" for port in svc["node_ports"]])

    status = item.get("status", {})
    if kind == "DaemonSet":
        ready = f"{status.get('numberReady', 0)}/{status.get('desiredNumberScheduled', 0)}"
    else:
        desired = status.get("replicas", 0)
        ready = f"{status.get('readyReplicas', 0)}/{desired}"

    rows.append(
        (
            namespace,
            name,
            kind,
            ready,
            compact(nodes_for_workload),
            compact(pod_ips),
            compact(service_values),
            compact(external_values),
            compact(hosts_by_workload.get(key, [])),
        )
    )

for row in sorted(rows):
    print("|".join(row))
PY
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
        echo "DOWN|${name}|${ip}|-|-|-|-|-"
        return
    fi

    # CPU: calculate idle percentage from node_cpu_seconds_total
    local cpu_pct="-"
    cpu_pct=$(awk '
        /^node_cpu_seconds_total\{.*mode="idle"/ { idle += $2 }
        /^node_cpu_seconds_total\{/              { total += $2 }
        END { if (total > 0) printf "%d%%", 100 - (idle * 100 / total); else print "-" }
    ' "$metrics_file" 2>/dev/null)

    # Memory:
    # - Linux: use MemAvailable for "real" pressure-aware usage
    # - macOS: node_exporter exposes free/inactive/purgeable separately. Using
    #   only free bytes dramatically overstates usage versus Activity Monitor.
    #   Exclude inactive + purgeable so the default view stays fast and closer
    #   to reclaimable-vs-working-set semantics.
    local mem_pct="-"
    mem_pct=$(awk '
        /^node_memory_MemTotal_bytes /     { total = $2 }
        /^node_memory_MemAvailable_bytes / { avail = $2 }
        /^node_memory_total_bytes /        { mac_total = $2 }
        /^node_memory_free_bytes /         { mac_free = $2 }
        /^node_memory_inactive_bytes /     { mac_inactive = $2 }
        /^node_memory_purgeable_bytes /    { mac_purgeable = $2 }
        END {
            if (total > 0) {
                printf "%d%%", (total - avail) * 100 / total
            } else if (mac_total > 0) {
                reclaimable = mac_free + mac_inactive + mac_purgeable
                used = mac_total - reclaimable
                if (used < 0) used = 0
                printf "%d%%", used * 100 / mac_total
            } else {
                print "-"
            }
        }
    ' "$metrics_file" 2>/dev/null)

    # Disk: root filesystem (handles scientific notation via awk)
    local disk_pct="-"
    disk_pct=$(awk '
        /^node_filesystem_size_bytes\{.*mountpoint="\/"[,}]/  { if (!size) size = $2 }
        /^node_filesystem_avail_bytes\{.*mountpoint="\/"[,}]/ { if (!avail) avail = $2 }
        END { if (size > 0) printf "%d%%", (size - avail) * 100 / size; else print "-" }
    ' "$metrics_file" 2>/dev/null)

    # Uptime/age from boot time
    local age="-"
    age=$(parse_node_uptime "$metrics_file")

    echo "UP|${name}|${ip}|${cpu_pct}|${mem_pct}|${disk_pct}|-|${age}"
}

# =============================================================================
# Standalone device helpers (Linux boxes not in K8s)
# =============================================================================

# Reuses mac fetch (node_exporter :9100). Adds ping fallback for UP/DOWN.
parse_standalone_metrics() {
    local name="$1" ip="$2" tmpdir="$3"
    local result
    result=$(parse_mac_metrics "$name" "$ip" "$tmpdir")

    # If node_exporter is unreachable, try ping to distinguish "up without
    # node_exporter" from "actually down"
    if [[ "$result" == DOWN* ]]; then
        if ping -c 1 -W 2 "$ip" &>/dev/null; then
            echo "UP|${name}|${ip}|-|-|-|-|-"
            return
        fi
    fi
    echo "$result"
}

# =============================================================================
# Android device helpers
# =============================================================================

# Resolve friendly name: config lookup → marketing name → model → serial
resolve_android_name() {
    local serial="$1"
    local tmpdir="$2"

    # Check config array for friendly name
    for entry in "${ANDROID_DEVICES[@]}"; do
        local cfg_serial="${entry%%|*}"
        local cfg_name="${entry##*|}"
        if [[ "$cfg_serial" == "$serial" ]]; then
            echo "$cfg_name"
            return
        fi
    done

    # Try marketing name from metrics file
    local metrics_file="$tmpdir/adb.${serial}.metrics"
    if [[ -s "$metrics_file" ]]; then
        local mkt_name
        mkt_name=$(grep '^MARKETING_NAME=' "$metrics_file" 2>/dev/null | cut -d= -f2-)
        if [[ -n "$mkt_name" ]]; then
            echo "$mkt_name"
            return
        fi
        local model
        model=$(grep '^MODEL=' "$metrics_file" 2>/dev/null | cut -d= -f2-)
        if [[ -n "$model" ]]; then
            echo "$model"
            return
        fi
    fi

    echo "$serial"
}

# Fetch metrics for a single Android device (runs in background)
fetch_android_metrics() {
    local serial="$1"
    local tmpdir="$2"
    local outfile="$tmpdir/adb.${serial}.metrics"

    # Batched getprop + battery in a single adb shell call (~100ms)
    adb -s "$serial" shell '
        echo "MODEL=$(getprop ro.product.model)"
        mkt="$(getprop ro.product.marketname)"
        [ -z "$mkt" ] && mkt="$(getprop ro.config.marketing_name)"
        [ -z "$mkt" ] && mkt="$(settings get global device_name 2>/dev/null)"
        [ "$mkt" = "null" ] && mkt=""
        echo "MARKETING_NAME=$mkt"
        echo "ANDROID_VER=$(getprop ro.build.version.release)"
        dumpsys battery 2>/dev/null | head -20 | while IFS= read -r line; do
            case "$line" in
                *"  level: "*)       echo "BATTERY_LEVEL=${line##*: }" ;;
                *"  temperature: "*) echo "BATTERY_TEMP=${line##*: }" ;;
            esac
        done
    ' > "$outfile" 2>/dev/null || true

    # Strip carriage returns
    if [[ -s "$outfile" ]]; then
        sed -i 's/\r//g' "$outfile"
    fi

    # Optional: storage (only with --disk)
    if [[ "$SHOW_DISK" == true ]]; then
        adb -s "$serial" shell 'df /data 2>/dev/null | tail -1' \
            > "$tmpdir/adb.${serial}.storage" 2>/dev/null || true
        sed -i 's/\r//g' "$tmpdir/adb.${serial}.storage" 2>/dev/null || true
    fi
}

# Discover devices and launch parallel metric collection
fetch_android_devices() {
    local tmpdir="$1"

    command -v adb &>/dev/null || return 0

    # Get raw device list
    local adb_output
    adb_output=$(adb devices 2>/dev/null) || return 0

    local device_pids=()
    local found_any=false

    while IFS=$'\t' read -r serial status; do
        [[ -z "$serial" || "$serial" == "List"* || "$serial" == "" ]] && continue
        found_any=true
        echo "${serial}|${status}" >> "$tmpdir/adb.devices"
        if [[ "$status" == "device" ]]; then
            fetch_android_metrics "$serial" "$tmpdir" &
            device_pids+=($!)
        fi
    done <<< "$adb_output"

    for pid in "${device_pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
}

# Parse collected metrics into pipe-delimited row
parse_android_metrics() {
    local serial="$1"
    local status="$2"
    local tmpdir="$3"

    local device_status="DOWN"
    local name model battery android_ver storage

    if [[ "$status" == "device" ]]; then
        device_status="UP"
    elif [[ "$status" == "unauthorized" ]]; then
        device_status="UNAUTH"
    fi

    name=$(resolve_android_name "$serial" "$tmpdir")

    if [[ "$device_status" == "UP" ]]; then
        local metrics_file="$tmpdir/adb.${serial}.metrics"
        if [[ -s "$metrics_file" ]]; then
            model=$(grep '^MODEL=' "$metrics_file" | head -1 | cut -d= -f2-)
            battery=$(grep '^BATTERY_LEVEL=' "$metrics_file" | head -1 | cut -d= -f2-)
            android_ver=$(grep '^ANDROID_VER=' "$metrics_file" | head -1 | cut -d= -f2-)
        fi

        # Storage (only with --disk)
        if [[ "$SHOW_DISK" == true ]]; then
            local storage_file="$tmpdir/adb.${serial}.storage"
            if [[ -s "$storage_file" ]]; then
                storage=$(awk '{ gsub(/%/,"",$5); if ($2+0>0) printf "%d%%", ($3/$2)*100 }' "$storage_file" 2>/dev/null)
            fi
        fi

        [[ -n "$battery" ]] && battery="${battery}%"
    fi

    echo "${device_status}|${name:-$serial}|${model:--}|${battery:--}|${android_ver:--}|${storage:--}"
}

print_android_header() {
    echo ""
    echo -e " ${BOLD}ANDROID DEVICES (adb)${NC}"

    local fmt="  ${BOLD}%-6s  %-24s %-16s %5s  %7s"
    local sep_len=63
    local -a cols=("STATUS" "DEVICE" "MODEL" "BAT" "ANDROID")

    if [[ "$SHOW_DISK" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("STORE")
    fi
    fmt+="${NC}\n"

    _separator "$sep_len"
    printf "$fmt" "${cols[@]}"
}

print_android_row() {
    local status="$1" name="$2" model="$3" battery="$4" android="$5" storage="$6"

    local status_icon status_color status_text
    case "$status" in
        UP)
            status_icon="${GREEN}●${NC}"
            status_color="${GREEN}"
            status_text="UP"
            ;;
        UNAUTH)
            status_icon="${YELLOW}●${NC}"
            status_color="${YELLOW}"
            status_text="AUTH"
            ;;
        *)
            status_icon="${RED}●${NC}"
            status_color="${RED}"
            status_text="DOWN"
            ;;
    esac

    local fmt="  ${status_icon} ${status_color}%4s${NC}  %-24s %-16s %5s  %7s"
    local -a vals=("$status_text" "$name" "$model" "$battery" "$android")

    if [[ "$SHOW_DISK" == true ]]; then
        fmt+="  %5s"
        vals+=("$storage")
    fi
    fmt+="\n"

    printf "$fmt" "${vals[@]}"
}

# =============================================================================
# Local workstation helpers (reads /proc and df directly)
# =============================================================================

get_local_cpu_temp() {
    local hwmon_dir name
    for hwmon_dir in /sys/class/hwmon/hwmon*/; do
        [[ -f "${hwmon_dir}name" ]] || continue
        name=$(<"${hwmon_dir}name")
        if [[ "$name" == "k10temp" || "$name" == "coretemp" ]]; then
            if [[ -f "${hwmon_dir}temp1_input" ]]; then
                local millideg
                millideg=$(<"${hwmon_dir}temp1_input")
                echo "$(( millideg / 1000 ))C"
                return
            fi
        fi
    done
    echo "-"
}

get_local_metrics() {
    local cpu_pct mem_pct disk_pct temp age
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

    # Temperature (only with --temp)
    if [[ "$SHOW_TEMP" == true ]]; then
        temp=$(get_local_cpu_temp)
    else
        temp="-"
    fi

    # Uptime/age from /proc/uptime
    local uptime_secs
    uptime_secs=$(awk '{ printf "%d", $1 }' /proc/uptime)
    age=$(format_uptime_age "$uptime_secs")

    echo "UP|${hostname}|${cpu_pct}|${mem_pct}|${disk_pct}|${temp}|${age}"
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

# Build separator line of given length
_separator() {
    local len="$1"
    local sep=""
    for (( i=0; i<len; i++ )); do sep+="─"; done
    echo -e " ${DIM}${sep}${NC}"
}

print_cluster_header() {
    local ctx="$1"
    local label="$2"
    echo ""
    echo -e " ${BOLD}CLUSTER: ${ctx} (${label})${NC}"

    local fmt="  ${BOLD}%-6s  %-24s %-11s %5s  %5s"
    local sep_len=59
    local -a cols=("STATUS" "NODE" "ROLE" "CPU" "MEM")

    if [[ "$SHOW_DISK" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("DISK")
    fi
    if [[ "$SHOW_TEMP" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("TEMP")
    fi
    # AGE is always shown for cluster (free from kubectl)
    fmt+="  %5s"
    sep_len=$((sep_len + 7))
    cols+=("AGE")

    fmt+="${NC}\n"

    _separator "$sep_len"
    printf "$fmt" "${cols[@]}"
}

print_node_row() {
    local status="$1" name="$2" role="$3" cpu="$4" mem="$5" disk="$6" temp="$7" age="$8"

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

    local fmt="  ${status_icon} ${status_color}%4s${NC}  %-24s %-11s %5s  %5s"
    local -a vals=("$status_text" "$name" "$role" "$cpu" "$mem")

    if [[ "$SHOW_DISK" == true ]]; then
        fmt+="  %5s"
        vals+=("$disk")
    fi
    if [[ "$SHOW_TEMP" == true ]]; then
        fmt+="  %5s"
        vals+=("$temp")
    fi
    # AGE always shown for cluster
    fmt+="  %5s"
    vals+=("$age")

    fmt+="\n"

    printf "$fmt" "${vals[@]}"
}

print_mac_header() {
    echo ""
    echo -e " ${BOLD}MAC MACHINES (external)${NC}"

    local fmt="  ${BOLD}%-6s  %-24s %-16s %5s  %5s  %5s"
    local sep_len=71
    local -a cols=("STATUS" "MACHINE" "IP" "CPU" "MEM" "DISK")

    if [[ "$SHOW_TEMP" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("TEMP")
    fi
    if [[ "$SHOW_AGE" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("AGE")
    fi
    fmt+="${NC}\n"

    _separator "$sep_len"
    printf "$fmt" "${cols[@]}"
}

print_mac_row() {
    local status="$1" name="$2" ip="$3" cpu="$4" mem="$5" disk="$6" temp="$7" age="$8"

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

    local fmt="  ${status_icon} ${status_color}%4s${NC}  %-24s %-16s %5s  %5s  %5s"
    local -a vals=("$status_text" "$name" "$ip" "$cpu" "$mem" "$disk")

    if [[ "$SHOW_TEMP" == true ]]; then
        fmt+="  %5s"
        vals+=("$temp")
    fi
    if [[ "$SHOW_AGE" == true ]]; then
        fmt+="  %5s"
        vals+=("$age")
    fi
    fmt+="\n"

    printf "$fmt" "${vals[@]}"
}

print_standalone_header() {
    echo ""
    echo -e " ${BOLD}STANDALONE DEVICES (ping/node_exporter)${NC}"

    local fmt="  ${BOLD}%-6s  %-24s %-16s %5s  %5s  %5s"
    local sep_len=71
    local -a cols=("STATUS" "DEVICE" "IP" "CPU" "MEM" "DISK")

    if [[ "$SHOW_TEMP" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("TEMP")
    fi
    if [[ "$SHOW_AGE" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("AGE")
    fi
    fmt+="${NC}\n"

    _separator "$sep_len"
    printf "$fmt" "${cols[@]}"
}

print_local_header() {
    echo ""
    echo -e " ${BOLD}LOCAL WORKSTATION${NC}"

    local fmt="  ${BOLD}%-6s  %-24s %5s  %5s  %5s"
    local sep_len=54
    local -a cols=("STATUS" "HOSTNAME" "CPU" "MEM" "DISK")

    if [[ "$SHOW_TEMP" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("TEMP")
    fi
    if [[ "$SHOW_AGE" == true ]]; then
        fmt+="  %5s"
        sep_len=$((sep_len + 7))
        cols+=("AGE")
    fi
    fmt+="${NC}\n"

    _separator "$sep_len"
    printf "$fmt" "${cols[@]}"
}

print_local_row() {
    local status="$1" name="$2" cpu="$3" mem="$4" disk="$5" temp="$6" age="$7"

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

    local fmt="  ${status_icon} ${status_color}%4s${NC}  %-24s %5s  %5s  %5s"
    local -a vals=("$status_text" "$name" "$cpu" "$mem" "$disk")

    if [[ "$SHOW_TEMP" == true ]]; then
        fmt+="  %5s"
        vals+=("$temp")
    fi
    if [[ "$SHOW_AGE" == true ]]; then
        fmt+="  %5s"
        vals+=("$age")
    fi
    fmt+="\n"

    printf "$fmt" "${vals[@]}"
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

truncate_text() {
    local text="$1"
    local max_len="$2"

    if [[ "${#text}" -le "$max_len" ]]; then
        printf "%s" "$text"
        return
    fi

    if [[ "$max_len" -le 3 ]]; then
        printf "%.*s" "$max_len" "$text"
        return
    fi

    printf "%s..." "${text:0:$((max_len - 3))}"
}

print_apps_header() {
    local ctx="$1"
    local label="$2"

    echo ""
    echo -e " ${BOLD}CLUSTER APPS: ${ctx} (${label})${NC}"
    _separator 166
    printf "  ${BOLD}%-16s %-28s %-11s %-7s %-28s %-16s %-26s %-16s %-20s${NC}\n" \
        "NAMESPACE" "APP" "KIND" "READY" "NODES" "POD IPS" "SERVICE" "EXTERNAL" "HOSTS"
}

print_app_row() {
    local namespace="$1"
    local app="$2"
    local kind="$3"
    local ready="$4"
    local nodes="$5"
    local pod_ips="$6"
    local service="$7"
    local external="$8"
    local hosts="$9"

    printf "  %-16s %-28s %-11s %-7s %-28s %-16s %-26s %-16s %-20s\n" \
        "$(truncate_text "$namespace" 16)" \
        "$(truncate_text "$app" 28)" \
        "$(truncate_text "$kind" 11)" \
        "$(truncate_text "$ready" 7)" \
        "$(truncate_text "$nodes" 28)" \
        "$(truncate_text "$pod_ips" 16)" \
        "$(truncate_text "$service" 26)" \
        "$(truncate_text "$external" 16)" \
        "$(truncate_text "$hosts" 20)"
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
        local label="${entry##*|}"
        if [[ "$SHOW_ALL" != true && "$label" != "local" ]]; then
            continue
        fi
        fetch_k8s_data "$ctx" "$TMPDIR_INFRA" &
        pids+=($!)
    done

    for entry in "${MAC_MACHINES[@]}"; do
        local mac_name="${entry%%|*}"
        local mac_ip="${entry##*|}"
        fetch_mac_metrics "$mac_name" "$mac_ip" "$TMPDIR_INFRA" &
        pids+=($!)
    done

    for entry in "${STANDALONE_DEVICES[@]}"; do
        local sd_name="${entry%%|*}"
        local sd_ip="${entry##*|}"
        fetch_mac_metrics "$sd_name" "$sd_ip" "$TMPDIR_INFRA" &
        pids+=($!)
    done

    fetch_android_devices "$TMPDIR_INFRA" &
    pids+=($!)

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

        if [[ "$SHOW_ALL" != true && "$label" != "local" ]]; then
            continue
        fi

        print_cluster_header "$ctx" "$label"

        local node_data
        node_data=$(parse_k8s_nodes "$ctx" "$TMPDIR_INFRA")

        if [[ "$node_data" == "UNREACHABLE" ]]; then
            echo -e "  ${YELLOW}Context unreachable${NC}"
        else
            while IFS='|' read -r status name role cpu mem disk temp age; do
                print_node_row "$status" "$name" "$role" "$cpu" "$mem" "$disk" "$temp" "$age"
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

        IFS='|' read -r status name ip cpu mem disk temp age <<< "$mac_data"
        print_mac_row "$status" "$name" "$ip" "$cpu" "$mem" "$disk" "$temp" "$age"
        total=$((total + 1))
        if [[ "$status" == "UP" ]]; then
            healthy=$((healthy + 1))
        else
            down=$((down + 1))
        fi
    done

    # Standalone devices
    print_standalone_header
    for entry in "${STANDALONE_DEVICES[@]}"; do
        local sd_name="${entry%%|*}"
        local sd_ip="${entry##*|}"

        local sd_data
        sd_data=$(parse_standalone_metrics "$sd_name" "$sd_ip" "$TMPDIR_INFRA")

        IFS='|' read -r status name ip cpu mem disk temp age <<< "$sd_data"
        print_mac_row "$status" "$name" "$ip" "$cpu" "$mem" "$disk" "$temp" "$age"
        total=$((total + 1))
        if [[ "$status" == "UP" ]]; then
            healthy=$((healthy + 1))
        else
            down=$((down + 1))
        fi
    done

    # Android devices (only if adb found and devices file exists)
    if [[ -s "$TMPDIR_INFRA/adb.devices" ]]; then
        print_android_header
        while IFS='|' read -r serial dev_status; do
            local android_data
            android_data=$(parse_android_metrics "$serial" "$dev_status" "$TMPDIR_INFRA")
            IFS='|' read -r a_status a_name a_model a_battery a_android a_storage <<< "$android_data"
            print_android_row "$a_status" "$a_name" "$a_model" "$a_battery" "$a_android" "$a_storage"
            total=$((total + 1))
            if [[ "$a_status" == "UP" ]]; then
                healthy=$((healthy + 1))
            else
                down=$((down + 1))
            fi
        done < "$TMPDIR_INFRA/adb.devices"
    fi

    # Local workstation
    print_local_header
    local local_data
    local_data=$(get_local_metrics)
    IFS='|' read -r status name cpu mem disk temp age <<< "$local_data"
    print_local_row "$status" "$name" "$cpu" "$mem" "$disk" "$temp" "$age"
    total=$((total + 1))
    healthy=$((healthy + 1))

    print_summary "$total" "$healthy" "$down"
}

cmd_apps() {
    TMPDIR_INFRA=$(mktemp -d)

    local pids=()
    for entry in "${K8S_CONTEXTS[@]}"; do
        local ctx="${entry%%|*}"
        local label="${entry##*|}"

        if [[ "$SHOW_ALL" != true && "$label" != "local" ]]; then
            continue
        fi

        fetch_apps_data "$ctx" "$TMPDIR_INFRA" &
        pids+=($!)
    done

    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    print_header

    local total_apps=0
    for entry in "${K8S_CONTEXTS[@]}"; do
        local ctx="${entry%%|*}"
        local label="${entry##*|}"

        if [[ "$SHOW_ALL" != true && "$label" != "local" ]]; then
            continue
        fi

        print_apps_header "$ctx" "$label"

        local app_data
        app_data=$(parse_apps_context "$ctx" "$TMPDIR_INFRA")
        if [[ "$app_data" == "UNREACHABLE" ]]; then
            echo -e "  ${YELLOW}Context unreachable${NC}"
            continue
        fi

        local cluster_count=0
        while IFS='|' read -r namespace app kind ready nodes pod_ips service external hosts; do
            [[ -z "$namespace" ]] && continue
            print_app_row "$namespace" "$app" "$kind" "$ready" "$nodes" "$pod_ips" "$service" "$external" "$hosts"
            cluster_count=$((cluster_count + 1))
            total_apps=$((total_apps + 1))
        done <<< "$app_data"

        echo ""
        echo "  Apps: $cluster_count"
    done

    echo ""
    echo " Summary: ${total_apps} workloads"
    echo ""
}

cmd_help() {
    cat <<'EOF'
infra — Homelab device/node health CLI

USAGE:
    infra [command] [options]

COMMANDS:
    nodes    Show all devices and their health (default)
    apps     Show Kubernetes apps, host nodes, and exposure details
    help     Show this help message

OPTIONS:
    --all,  -a   Include remote clusters (e.g. DigitalOcean)
    --disk, -d   Include disk/storage column (cluster nodes, Android /data)
    --temp, -t   Include CPU temperature column (slower, scrapes node_exporter)
    --age,  -g   Include uptime/age column for Mac and local devices

EXAMPLES:
    infra            # show local devices only (cluster: CPU/MEM/AGE, others: CPU/MEM/DISK)
    infra apps       # show live Kubernetes app inventory for local contexts
    infra --all      # include remote clusters
    infra apps --all # include remote clusters in app inventory
    infra --disk     # add disk usage for cluster nodes and Android storage
    infra --temp     # include temperature readings
    infra --age      # include uptime/age for Mac and local
    infra help       # show this help

DATA SOURCES:
    Kubernetes nodes   kubectl get nodes / top nodes (per context)
    Kubernetes apps    kubectl get deploy,statefulset,daemonset,replicaset,pods,svc,ingress
    Mac machines       node_exporter metrics via HTTP (:9100)
    Android devices    adb shell getprop / dumpsys battery / df (auto-discovered)
    Disk (K8s nodes)   node_exporter :9100 (root filesystem)
    Temperature        node_exporter :9100 / /sys/class/hwmon (with --temp)
    Age/Uptime         kubectl age (K8s), node_boot_time (Mac), /proc/uptime (local)
EOF
}

# =============================================================================
# Main
# =============================================================================

cmd="nodes"
for arg in "$@"; do
    case "$arg" in
        --temp|-t) SHOW_TEMP=true ;;
        --all|-a) SHOW_ALL=true ;;
        --age|-g) SHOW_AGE=true ;;
        --disk|-d) SHOW_DISK=true ;;
        help|--help|-h) cmd="help" ;;
        nodes) cmd="nodes" ;;
        apps) cmd="apps" ;;
        *)
            log_error "Unknown argument: $arg"
            cmd_help
            exit 1
            ;;
    esac
done

case "$cmd" in
    nodes) cmd_nodes ;;
    apps)  cmd_apps ;;
    help)  cmd_help ;;
esac
