#!/usr/bin/env bash
#
# PXE Server Status Collector for infra-dash
#
# Outputs JSON with server status for infra-dash integration.
#

set -euo pipefail

# Resolve symlinks to get actual script location
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

# Runtime paths
PXE_LOG_DIR="/tmp/pxe-server"
DNSMASQ_PID="$PXE_LOG_DIR/dnsmasq.pid"
HTTP_PID="$PXE_LOG_DIR/http.pid"
HTTP_PORT="${PXE_HTTP_PORT:-9080}"
HTTP_ROOT="$SCRIPT_DIR/../http"

# Check service status
dnsmasq_running=false
http_running=false

if [[ -f "$DNSMASQ_PID" ]] && kill -0 "$(cat "$DNSMASQ_PID")" 2>/dev/null; then
    dnsmasq_running=true
fi

if [[ -f "$HTTP_PID" ]] && kill -0 "$(cat "$HTTP_PID")" 2>/dev/null; then
    http_running=true
fi

# Determine overall status
status="down"
if $dnsmasq_running && $http_running; then
    status="up"
elif $dnsmasq_running || $http_running; then
    status="warning"
fi

# Check boot files
boot_files_ok=true
[[ -f "$HTTP_ROOT/cachyos/vmlinuz-linux-cachyos" ]] || boot_files_ok=false
[[ -f "$HTTP_ROOT/cachyos/initramfs-linux-cachyos.img" ]] || boot_files_ok=false
[[ -f "$HTTP_ROOT/cachyos/airootfs.sfs" ]] || boot_files_ok=false

# Count recent boot attempts (last hour from dnsmasq log)
boot_count=0
if [[ -f "$PXE_LOG_DIR/dnsmasq.log" ]]; then
    boot_count=$(grep -c "DHCPACK" "$PXE_LOG_DIR/dnsmasq.log" 2>/dev/null || echo 0)
fi

# Output JSON
cat <<EOF
{
  "status": "$status",
  "details": {
    "dnsmasq": $dnsmasq_running,
    "http": $http_running,
    "http_port": $HTTP_PORT,
    "boot_files_ready": $boot_files_ok,
    "recent_boots": $boot_count
  }
}
EOF
