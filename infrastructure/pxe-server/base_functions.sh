#!/usr/bin/env bash
#
# PXE Server - Base Functions
# Shared utilities for logging, network helpers, and common operations
#

# Determine script location
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_NAME="pxe-server"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Logging functions - all go to stderr to avoid polluting function return values
log_info()    { echo -e "${BLUE}[INFO]${NC} $*" >&2; }
log_success() { echo -e "${GREEN}[OK]${NC} $*" >&2; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_debug()   { [[ "${DEBUG:-0}" == "1" ]] && echo -e "${CYAN}[DEBUG]${NC} $*" >&2; }

log_section() {
    echo "" >&2
    echo -e "${BOLD}${BLUE}=== $* ===${NC}" >&2
    echo "" >&2
}

# Network helpers
get_local_ip() {
    # Get primary LAN IP (route to internet)
    ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[\d.]+' || \
        hostname -I | awk '{print $1}'
}

get_lan_interface() {
    # Get interface used for LAN (default route)
    ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+' || \
        ip route | grep default | awk '{print $5}' | head -1
}

get_lan_subnet() {
    local iface
    iface=$(get_lan_interface)
    ip addr show "$iface" 2>/dev/null | grep -oP 'inet \K[\d.]+/\d+' | head -1
}

get_network_address() {
    # Convert host IP to network address (e.g., 192.168.1.2/24 -> 192.168.1.0)
    local ip_cidr
    ip_cidr=$(get_lan_subnet)
    local ip="${ip_cidr%/*}"
    local cidr="${ip_cidr#*/}"

    # Calculate network address using bitwise AND with netmask
    IFS='.' read -r o1 o2 o3 o4 <<< "$ip"
    local mask=$((0xFFFFFFFF << (32 - cidr) & 0xFFFFFFFF))
    local m1=$((mask >> 24 & 255))
    local m2=$((mask >> 16 & 255))
    local m3=$((mask >> 8 & 255))
    local m4=$((mask & 255))

    echo "$((o1 & m1)).$((o2 & m2)).$((o3 & m3)).$((o4 & m4))"
}

# Check if a port is in use
port_in_use() {
    local port="$1"
    ss -tuln | grep -q ":${port} "
}

# Check if a process is running by PID file
process_running() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

# Wait for a service to be ready
wait_for_port() {
    local port="$1"
    local timeout="${2:-30}"
    local count=0

    while ! port_in_use "$port" && [[ $count -lt $timeout ]]; do
        sleep 1
        ((count++))
    done

    [[ $count -lt $timeout ]]
}

# Require root/sudo for certain operations
require_sudo() {
    if [[ $EUID -ne 0 ]]; then
        if ! sudo -n true 2>/dev/null; then
            log_error "This operation requires sudo privileges"
            return 1
        fi
    fi
}

# Check if command exists
command_exists() {
    command -v "$1" &>/dev/null
}

# Ensure dependencies are installed
check_dependencies() {
    local deps=("$@")
    local missing=()

    for dep in "${deps[@]}"; do
        if ! command_exists "$dep"; then
            missing+=("$dep")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing[*]}"
        log_info "Install with: sudo pacman -S ${missing[*]}"
        return 1
    fi

    return 0
}

# Configuration paths
PXE_CONFIG_DIR="$SUITE_DIR/config"
PXE_BOOT_DIR="$SUITE_DIR/boot"
PXE_HTTP_DIR="$SUITE_DIR/http"
PXE_IMAGES_DIR="$SUITE_DIR/images"

# Runtime paths
PXE_LOG_DIR="/tmp/pxe-server"
PXE_DNSMASQ_PID="$PXE_LOG_DIR/dnsmasq.pid"
PXE_HTTP_PID="$PXE_LOG_DIR/http.pid"
PXE_DNSMASQ_LOG="$PXE_LOG_DIR/dnsmasq.log"
PXE_HTTP_LOG="$PXE_LOG_DIR/http.log"

# Default ports
PXE_HTTP_PORT="${PXE_HTTP_PORT:-9080}"
PXE_TFTP_PORT="${PXE_TFTP_PORT:-69}"

# Ensure runtime directory exists
ensure_runtime_dir() {
    mkdir -p "$PXE_LOG_DIR"
}
