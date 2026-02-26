#!/usr/bin/env bash
#
# PXE Boot Server Control Script
# Manages dnsmasq (TFTP + Proxy DHCP) and HTTP server for network booting
#
# Usage: pxe-server <command> [options]
#
# Commands:
#   start     Start TFTP and HTTP servers
#   stop      Stop all servers
#   restart   Restart servers
#   status    Show server status and boot file availability
#   logs      Tail server logs (dnsmasq|http|all)
#   prepare   Download and prepare CachyOS boot images
#   config    Show/edit configuration
#   help      Show this help message
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
source "$SCRIPT_DIR/base_functions.sh"

# =============================================================================
# Configuration
# =============================================================================

# Auto-detect network settings
SERVER_IP="${PXE_SERVER_IP:-$(get_local_ip)}"
SERVER_INTERFACE="${PXE_INTERFACE:-$(get_lan_interface)}"
SERVER_SUBNET="${PXE_SUBNET:-$(get_network_address)}"
HTTP_PORT="${PXE_HTTP_PORT:-9080}"

# =============================================================================
# Helper Functions
# =============================================================================

generate_dnsmasq_config() {
    local config_template="$PXE_CONFIG_DIR/dnsmasq.conf"
    local config_runtime="$PXE_LOG_DIR/dnsmasq.conf"

    ensure_runtime_dir

    # Replace placeholders with actual values
    sed -e "s|@@INTERFACE@@|$SERVER_INTERFACE|g" \
        -e "s|@@SUBNET@@|$SERVER_SUBNET|g" \
        -e "s|@@TFTP_ROOT@@|$PXE_BOOT_DIR|g" \
        -e "s|@@HTTP_URL@@|http://$SERVER_IP:$HTTP_PORT|g" \
        -e "s|@@LOG_FILE@@|$PXE_DNSMASQ_LOG|g" \
        "$config_template" > "$config_runtime"

    echo "$config_runtime"
}

start_dnsmasq() {
    if process_running "$PXE_DNSMASQ_PID"; then
        log_warning "dnsmasq already running (PID: $(cat "$PXE_DNSMASQ_PID"))"
        return 0
    fi

    log_info "Starting dnsmasq (TFTP + Proxy DHCP)..."

    local config
    config=$(generate_dnsmasq_config)

    # dnsmasq needs root for DHCP (port 67) and TFTP (port 69)
    if ! sudo dnsmasq --conf-file="$config" --pid-file="$PXE_DNSMASQ_PID"; then
        log_error "Failed to start dnsmasq"
        return 1
    fi

    log_success "dnsmasq started (PID: $(cat "$PXE_DNSMASQ_PID"))"
}

stop_dnsmasq() {
    # Kill all dnsmasq processes started by this script (using our config file)
    local pids
    pids=$(pgrep -f "dnsmasq.*pxe-server" 2>/dev/null)

    if [[ -n "$pids" ]]; then
        for pid in $pids; do
            if sudo kill "$pid" 2>/dev/null; then
                log_info "dnsmasq stopped (PID: $pid)"
            fi
        done
        # Give processes time to die
        sleep 1
        # Force kill any remaining
        pids=$(pgrep -f "dnsmasq.*pxe-server" 2>/dev/null)
        if [[ -n "$pids" ]]; then
            sudo kill -9 $pids 2>/dev/null
            log_warning "Force killed remaining dnsmasq processes"
        fi
    fi

    rm -f "$PXE_DNSMASQ_PID"
}

start_http() {
    if process_running "$PXE_HTTP_PID"; then
        log_warning "HTTP server already running (PID: $(cat "$PXE_HTTP_PID"))"
        return 0
    fi

    log_info "Starting HTTP server on port $HTTP_PORT..."

    ensure_runtime_dir

    # Start Python HTTP server in background
    cd "$PXE_HTTP_DIR"
    python3 -m http.server "$HTTP_PORT" --bind 0.0.0.0 &>"$PXE_HTTP_LOG" &
    echo $! > "$PXE_HTTP_PID"

    # Wait a moment and verify it started
    sleep 1
    if process_running "$PXE_HTTP_PID"; then
        log_success "HTTP server started (PID: $(cat "$PXE_HTTP_PID"))"
    else
        log_error "HTTP server failed to start"
        cat "$PXE_HTTP_LOG"
        return 1
    fi
}

stop_http() {
    if [[ -f "$PXE_HTTP_PID" ]]; then
        local pid
        pid=$(cat "$PXE_HTTP_PID")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            log_info "HTTP server stopped (PID: $pid)"
        fi
        rm -f "$PXE_HTTP_PID"
    fi
}

check_boot_files() {
    local all_ok=true

    echo ""
    log_info "Boot files:"

    # UEFI boot files
    if [[ -f "$PXE_BOOT_DIR/uefi/ipxe.efi" ]]; then
        echo "  [OK] uefi/ipxe.efi"
    else
        echo "  [MISSING] uefi/ipxe.efi"
        all_ok=false
    fi

    # BIOS boot files
    local bios_files=("pxelinux.0" "ldlinux.c32" "menu.c32" "ipxe.lkrn")
    for f in "${bios_files[@]}"; do
        if [[ -f "$PXE_BOOT_DIR/bios/$f" ]]; then
            echo "  [OK] bios/$f"
        else
            echo "  [MISSING] bios/$f"
            all_ok=false
        fi
    done

    # iPXE menu
    if [[ -f "$PXE_HTTP_DIR/ipxe/menu.ipxe" ]]; then
        echo "  [OK] ipxe/menu.ipxe"
    else
        echo "  [MISSING] ipxe/menu.ipxe"
        all_ok=false
    fi

    echo ""
    log_info "CachyOS images:"

    # CachyOS boot files
    local cachyos_files=("vmlinuz-linux-cachyos" "initramfs-linux-cachyos.img" "airootfs.sfs")
    for f in "${cachyos_files[@]}"; do
        if [[ -f "$PXE_HTTP_DIR/cachyos/$f" ]]; then
            local size
            size=$(du -h "$PXE_HTTP_DIR/cachyos/$f" | cut -f1)
            echo "  [OK] cachyos/$f ($size)"
        else
            echo "  [MISSING] cachyos/$f - run 'pxe-server prepare'"
            all_ok=false
        fi
    done

    $all_ok
}

# =============================================================================
# Commands
# =============================================================================

cmd_start() {
    log_section "Starting PXE Boot Server"

    log_info "Server IP: $SERVER_IP"
    log_info "Interface: $SERVER_INTERFACE"
    log_info "HTTP Port: $HTTP_PORT"

    # Check dependencies
    if ! check_dependencies dnsmasq python3; then
        exit 1
    fi

    start_dnsmasq
    start_http

    log_section "PXE Server Running"
    echo "TFTP:  tftp://$SERVER_IP (port 69)"
    echo "HTTP:  http://$SERVER_IP:$HTTP_PORT"
    echo "Menu:  http://$SERVER_IP:$HTTP_PORT/ipxe/menu.ipxe"
    echo ""
    log_info "Monitor logs: pxe-server logs all"
}

cmd_stop() {
    log_section "Stopping PXE Boot Server"

    stop_dnsmasq
    stop_http

    log_success "PXE server stopped"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    log_section "PXE Server Status"

    # Check services
    local dnsmasq_status="stopped"
    local http_status="stopped"

    if process_running "$PXE_DNSMASQ_PID"; then
        dnsmasq_status="${GREEN}running${NC} (PID: $(cat "$PXE_DNSMASQ_PID"))"
    else
        dnsmasq_status="${RED}stopped${NC}"
    fi

    if process_running "$PXE_HTTP_PID"; then
        http_status="${GREEN}running${NC} (PID: $(cat "$PXE_HTTP_PID"))"
    else
        http_status="${RED}stopped${NC}"
    fi

    echo -e "dnsmasq (TFTP+DHCP): $dnsmasq_status"
    echo -e "HTTP server:         $http_status"
    echo ""
    echo "Server IP:    $SERVER_IP"
    echo "Interface:    $SERVER_INTERFACE"
    echo "HTTP Port:    $HTTP_PORT"

    check_boot_files || true
}

cmd_logs() {
    local log_type="${1:-all}"

    case "$log_type" in
        dnsmasq|dhcp|tftp)
            if [[ -f "$PXE_DNSMASQ_LOG" ]]; then
                tail -f "$PXE_DNSMASQ_LOG"
            else
                log_error "No dnsmasq log found. Is the server running?"
            fi
            ;;
        http)
            if [[ -f "$PXE_HTTP_LOG" ]]; then
                tail -f "$PXE_HTTP_LOG"
            else
                log_error "No HTTP log found. Is the server running?"
            fi
            ;;
        all)
            if [[ -f "$PXE_DNSMASQ_LOG" ]] || [[ -f "$PXE_HTTP_LOG" ]]; then
                tail -f "$PXE_DNSMASQ_LOG" "$PXE_HTTP_LOG" 2>/dev/null
            else
                log_error "No logs found. Is the server running?"
            fi
            ;;
        *)
            log_error "Unknown log type: $log_type"
            echo "Usage: pxe-server logs [dnsmasq|http|all]"
            exit 1
            ;;
    esac
}

cmd_prepare() {
    if [[ -x "$SUITE_DIR/tools/prepare-images.sh" ]]; then
        "$SUITE_DIR/tools/prepare-images.sh" "$@"
    else
        log_error "prepare-images.sh not found or not executable"
        exit 1
    fi
}

cmd_config() {
    local action="${1:-show}"

    case "$action" in
        show)
            log_section "Current Configuration"
            echo "Server IP:    $SERVER_IP"
            echo "Interface:    $SERVER_INTERFACE"
            echo "Subnet:       $SERVER_SUBNET"
            echo "HTTP Port:    $HTTP_PORT"
            echo ""
            echo "Override with environment variables:"
            echo "  PXE_SERVER_IP=$SERVER_IP"
            echo "  PXE_INTERFACE=$SERVER_INTERFACE"
            echo "  PXE_HTTP_PORT=$HTTP_PORT"
            ;;
        edit)
            ${EDITOR:-vim} "$PXE_CONFIG_DIR/dnsmasq.conf"
            ;;
        *)
            log_error "Unknown config action: $action"
            echo "Usage: pxe-server config [show|edit]"
            exit 1
            ;;
    esac
}

cmd_help() {
    cat <<'EOF'
PXE Boot Server Control

Usage: pxe-server <command> [options]

Commands:
  start            Start TFTP and HTTP servers
  stop             Stop all servers
  restart          Restart servers
  status           Show server status and boot file availability
  logs [type]      Tail server logs (dnsmasq|http|all)
  prepare          Download and prepare CachyOS boot images
  config [action]  Show or edit configuration (show|edit)
  help             Show this help message

Environment Variables:
  PXE_SERVER_IP    Override auto-detected server IP
  PXE_INTERFACE    Override auto-detected network interface
  PXE_HTTP_PORT    Override HTTP port (default: 8080)

Examples:
  pxe-server start              # Start the PXE server
  pxe-server status             # Check if everything is ready
  pxe-server logs dnsmasq       # Watch DHCP/TFTP activity
  pxe-server prepare            # Download CachyOS images

First-time setup:
  1. Run the install script:     ./install.sh
  2. Prepare CachyOS images:     pxe-server prepare
  3. Configure OpenWRT:          Add DHCP option 66 (see docs)
  4. Start the server:           pxe-server start
  5. Boot client from network:   F12/F2 at BIOS -> Network Boot
EOF
}

# =============================================================================
# Main
# =============================================================================

case "${1:-help}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    logs)    shift; cmd_logs "$@" ;;
    prepare) shift; cmd_prepare "$@" ;;
    config)  shift; cmd_config "$@" ;;
    help|--help|-h) cmd_help ;;
    *)
        log_error "Unknown command: $1"
        echo ""
        cmd_help
        exit 1
        ;;
esac
