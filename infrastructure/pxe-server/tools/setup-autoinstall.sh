#!/usr/bin/env bash
#
# Setup & Test PXE Autoinstall
#
# Injects the autoinstall service into the CachyOS squashfs,
# starts the PXE server, and optionally runs tests.
#
# Usage: setup-autoinstall.sh [command]
#
# Commands:
#   inject      Inject autoinstall service into squashfs (default)
#   test-dryrun Run QEMU dry-run test (no disk writes)
#   test-full   Run QEMU full install test (writes to virtual disk)
#   wol <MAC>   Wake HP Victus and watch logs
#   all <MAC>   Full pipeline: inject -> start server -> dry-run -> wol
#   status      Show current state of everything
#

set -euo pipefail

# Resolve script location
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
source "$SCRIPT_DIR/../base_functions.sh"

# =============================================================================
# Paths
# =============================================================================

SFS="$PXE_HTTP_DIR/cachyos/airootfs.sfs"
SERVICE_FILE="$PXE_HTTP_DIR/kickstart/pxe-autoinstall.service"
TMP_DIR="/tmp/cachyos-squashfs-edit"
VM_DISK="/tmp/pxe-test-disk.qcow2"

# =============================================================================
# Commands
# =============================================================================

cmd_status() {
    log_section "Autoinstall Status"

    # Squashfs
    if [[ -f "$SFS" ]]; then
        local sfs_size
        sfs_size=$(du -h "$SFS" | cut -f1)
        log_info "airootfs.sfs: $sfs_size"

        # Check if service is already injected
        if unsquashfs -l "$SFS" 2>/dev/null | grep -q "pxe-autoinstall.service"; then
            log_success "Autoinstall service: INJECTED"
        else
            log_warning "Autoinstall service: NOT INJECTED (run: setup-autoinstall.sh inject)"
        fi
    else
        log_error "airootfs.sfs: MISSING (run: prepare-images.sh first)"
    fi

    if [[ -f "${SFS}.orig" ]]; then
        log_info "Original backup: ${SFS}.orig ($(du -h "${SFS}.orig" | cut -f1))"
    fi

    echo ""

    # PXE server
    bash "$SUITE_DIR/pxe-server.sh" status 2>&1 || true

    echo ""

    # VM disk
    if [[ -f "$VM_DISK" ]]; then
        log_info "VM test disk: $VM_DISK ($(du -h "$VM_DISK" | cut -f1))"
    else
        log_info "VM test disk: not created yet"
    fi

    # Dependencies
    echo ""
    log_info "Dependencies:"
    for cmd in unsquashfs mksquashfs qemu-system-x86_64 wakeonlan; do
        if command_exists "$cmd"; then
            log_success "  $cmd"
        else
            log_error "  $cmd - MISSING"
        fi
    done
}

cmd_inject() {
    log_section "Injecting Autoinstall Service into SquashFS"

    # Preflight
    if [[ ! -f "$SFS" ]]; then
        log_error "airootfs.sfs not found. Run prepare-images.sh first."
        exit 1
    fi

    if [[ ! -f "$SERVICE_FILE" ]]; then
        log_error "pxe-autoinstall.service not found at: $SERVICE_FILE"
        exit 1
    fi

    if ! command_exists unsquashfs || ! command_exists mksquashfs; then
        log_error "Missing squashfs-tools. Install: sudo pacman -S squashfs-tools"
        exit 1
    fi

    # Check if already injected
    if unsquashfs -l "$SFS" 2>/dev/null | grep -q "pxe-autoinstall.service"; then
        log_warning "Service already injected into squashfs"
        read -rp "Re-inject? [y/N] " answer
        [[ "$answer" != "y" && "$answer" != "Y" ]] && return 0
    fi

    # Backup original
    if [[ ! -f "${SFS}.orig" ]]; then
        log_info "Backing up original squashfs..."
        cp "$SFS" "${SFS}.orig"
        log_success "Backup saved: ${SFS}.orig"
    fi

    # Extract
    log_info "Extracting squashfs (this takes a few minutes)..."
    sudo rm -rf "$TMP_DIR"
    sudo unsquashfs -d "$TMP_DIR" "$SFS"

    # Inject service
    log_info "Copying pxe-autoinstall.service..."
    sudo cp "$SERVICE_FILE" "$TMP_DIR/etc/systemd/system/pxe-autoinstall.service"
    sudo chmod 644 "$TMP_DIR/etc/systemd/system/pxe-autoinstall.service"

    # Enable it
    sudo mkdir -p "$TMP_DIR/etc/systemd/system/multi-user.target.wants"
    sudo ln -sf /etc/systemd/system/pxe-autoinstall.service \
        "$TMP_DIR/etc/systemd/system/multi-user.target.wants/pxe-autoinstall.service"
    log_success "Service injected and enabled"

    # Repack
    log_info "Repacking squashfs with zstd (this takes several minutes)..."
    sudo rm -f "$SFS"
    sudo mksquashfs "$TMP_DIR" "$SFS" -comp zstd -Xcompression-level 15 -b 1M

    sudo chmod 644 "$SFS"
    sudo rm -rf "$TMP_DIR"

    # Summary
    local orig_size new_size
    orig_size=$(du -h "${SFS}.orig" | cut -f1)
    new_size=$(du -h "$SFS" | cut -f1)
    log_success "Injection complete!"
    log_info "  Original: $orig_size (xz)"
    log_info "  Modified: $new_size (zstd)"
}

cmd_ensure_server() {
    # Make sure PXE server is fully running
    local status
    status=$(bash "$SUITE_DIR/pxe-server.sh" status 2>&1)

    if echo "$status" | grep -q "dnsmasq.*stopped"; then
        log_info "Starting PXE server..."
        bash "$SUITE_DIR/pxe-server.sh" start
    else
        log_info "PXE server already running"
    fi
}

cmd_test_dryrun() {
    log_section "QEMU Dry-Run Test"

    cmd_ensure_server

    log_info "Launching QEMU with virtual disk..."
    log_info "Select [5] Dry Run from the menu"
    log_warning "Press Ctrl+A then X to exit QEMU"
    echo ""

    bash "$SCRIPT_DIR/test-vm.sh" uefi --network --disk
}

cmd_test_full() {
    log_section "QEMU Full Install Test"

    cmd_ensure_server

    # Fresh disk for full test
    if [[ -f "$VM_DISK" ]]; then
        log_warning "Existing VM disk found: $VM_DISK"
        read -rp "Delete and create fresh disk? [y/N] " answer
        if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
            rm -f "$VM_DISK"
        fi
    fi

    log_info "Launching QEMU with virtual disk..."
    log_info "Select a profile (Desktop/Laptop/Headless) from the menu"
    log_info "Or let it timeout to Desktop (30s)"
    log_warning "Press Ctrl+A then X to exit QEMU"
    echo ""

    bash "$SCRIPT_DIR/test-vm.sh" uefi --network --disk
}

cmd_wol() {
    local mac="${1:-}"

    if [[ -z "$mac" ]]; then
        log_error "MAC address required. Usage: setup-autoinstall.sh wol AA:BB:CC:DD:EE:FF"
        exit 1
    fi

    log_section "Wake-on-LAN: HP Victus"

    cmd_ensure_server

    log_info "Sending WoL magic packet to $mac..."
    wakeonlan "$mac"
    log_success "WoL packet sent"

    echo ""
    log_info "The Victus should now:"
    log_info "  1. Power on"
    log_info "  2. PXE boot -> iPXE menu"
    log_info "  3. Timeout (30s) -> Desktop profile"
    log_info "  4. Boot live CachyOS"
    log_info "  5. Autoinstall service triggers"
    log_info "  6. disk-install.sh runs (10s countdown)"
    log_info "  7. Partition, install, configure, reboot"
    echo ""
    log_info "Monitor progress with:"
    log_info "  tail -f /tmp/pxe-server/http.log"
    echo ""

    read -rp "Tail HTTP logs now? [Y/n] " answer
    if [[ "$answer" != "n" && "$answer" != "N" ]]; then
        tail -f /tmp/pxe-server/http.log
    fi
}

cmd_all() {
    local mac="${1:-}"

    log_section "Full Autoinstall Pipeline"

    # Step 1: Inject
    cmd_inject

    # Step 2: Start server
    cmd_ensure_server

    # Step 3: Dry-run in QEMU
    log_info "Ready for QEMU dry-run test"
    read -rp "Run QEMU dry-run? [Y/n] " answer
    if [[ "$answer" != "n" && "$answer" != "N" ]]; then
        cmd_test_dryrun
    fi

    # Step 4: WoL to Victus
    if [[ -n "$mac" ]]; then
        echo ""
        read -rp "Dry-run passed? Send WoL to Victus ($mac)? [y/N] " answer
        if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
            cmd_wol "$mac"
        fi
    else
        echo ""
        log_info "No MAC provided - skipping WoL"
        log_info "Run manually: setup-autoinstall.sh wol AA:BB:CC:DD:EE:FF"
    fi
}

# HP Victus MAC address
VICTUS_MAC="e0:73:e7:f3:5b:65"

cmd_verify() {
    log_section "Post-Install Verification"

    local target_ip=""

    # Step 1: Find Victus IP via ARP/neighbor table
    log_info "Looking for Victus MAC ($VICTUS_MAC) on the network..."
    target_ip=$(ip neigh show | grep -i "${VICTUS_MAC}" | awk '{print $1}' | head -1)

    # Fallback: nmap ping scan of local subnet
    if [[ -z "$target_ip" ]]; then
        log_warning "Not in ARP cache, running nmap ping scan..."
        local subnet
        subnet=$(get_lan_subnet)
        if [[ -n "$subnet" ]] && command_exists nmap; then
            # Ping scan to populate ARP, then check again
            sudo nmap -sn "$subnet" &>/dev/null
            target_ip=$(ip neigh show | grep -i "${VICTUS_MAC}" | awk '{print $1}' | head -1)
        fi
    fi

    if [[ -z "$target_ip" ]]; then
        log_error "Could not find Victus on the network"
        log_info "Make sure the machine has booted and is connected to the LAN"
        return 1
    fi

    log_success "Found Victus at: $target_ip"
    echo ""

    # Step 2: SSH in and run checks
    log_info "Connecting via SSH..."
    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
    local ssh_key="$HOME/.ssh/id_ed25519"
    local ssh_cmd="ssh $ssh_opts -i $ssh_key kblack0610@$target_ip"

    local checks=(
        "hostname"
        "whoami"
        "cat /etc/os-release | head -3"
        "systemctl is-active sshd"
        "systemctl is-active NetworkManager"
        "sudo -n whoami"
    )

    local pass=0
    local fail=0

    for check in "${checks[@]}"; do
        local result
        if result=$(eval "$ssh_cmd '$check'" 2>/dev/null); then
            log_success "$check → $result"
            ((pass++))
        else
            log_error "$check → FAILED"
            ((fail++))
        fi
    done

    echo ""
    if [[ $fail -eq 0 ]]; then
        log_success "All $pass checks passed!"
    else
        log_warning "$pass passed, $fail failed"
        return 1
    fi
}

cmd_reinstall() {
    log_section "Full Autonomous Reinstall Cycle"

    # Step 1: Ensure PXE server is running
    cmd_ensure_server

    # Step 2: WoL the Victus
    log_info "Sending WoL to Victus ($VICTUS_MAC)..."
    if ! command_exists wakeonlan; then
        log_error "wakeonlan not installed. Run: sudo pacman -S wakeonlan"
        return 1
    fi
    wakeonlan "$VICTUS_MAC"
    log_success "WoL packet sent"

    # Step 3: Wait for live environment to boot (watch for airootfs.sfs request)
    echo ""
    log_info "Waiting for Victus to PXE boot and fetch airootfs.sfs..."
    log_info "(This typically takes 2-3 minutes)"
    local http_log="$PXE_HTTP_LOG"
    local wait_boot=0
    local max_boot=300  # 5 minutes max

    while [[ $wait_boot -lt $max_boot ]]; do
        if [[ -f "$http_log" ]] && grep -q "airootfs.sfs" "$http_log" 2>/dev/null; then
            log_success "Live environment is booting (airootfs.sfs requested)"
            break
        fi
        sleep 5
        ((wait_boot += 5))
        printf "\r  Waiting... %ds / %ds" "$wait_boot" "$max_boot"
    done
    echo ""

    if [[ $wait_boot -ge $max_boot ]]; then
        log_error "Timeout waiting for PXE boot"
        return 1
    fi

    # Step 4: Wait for install to complete and system to reboot
    # The install takes ~10-15 min, then the machine reboots with a new IP
    echo ""
    log_info "Waiting for installation to complete and Victus to reboot..."
    log_info "(This typically takes 10-15 minutes for pacstrap + configure)"
    local wait_install=0
    local max_install=1200  # 20 minutes max

    # Wait a minimum of 5 minutes before polling (pacstrap takes time)
    log_info "Minimum wait: 5 minutes for pacstrap..."
    sleep 300
    wait_install=300

    while [[ $wait_install -lt $max_install ]]; do
        # Check if Victus MAC appeared in ARP (means it rebooted into new system)
        local new_ip
        new_ip=$(ip neigh show | grep -i "${VICTUS_MAC}" | awk '{print $1}' | head -1)
        if [[ -n "$new_ip" ]]; then
            # Try SSH to confirm it's the new system (not still the live env)
            if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                   -o ConnectTimeout=5 -i "$HOME/.ssh/id_ed25519" \
                   "kblack0610@$new_ip" "test -f /etc/machine-id" 2>/dev/null; then
                log_success "Victus rebooted into new system at $new_ip"
                break
            fi
        fi
        sleep 15
        ((wait_install += 15))
        printf "\r  Waiting... %ds / %ds" "$wait_install" "$max_install"
    done
    echo ""

    if [[ $wait_install -ge $max_install ]]; then
        log_error "Timeout waiting for Victus to come back online"
        log_info "Try manually: setup-autoinstall.sh verify"
        return 1
    fi

    # Step 5: Run verification
    echo ""
    cmd_verify
}

show_help() {
    cat <<'EOF'
PXE Autoinstall Setup & Test

Usage: setup-autoinstall.sh [command] [args]

Commands:
  status                Show current state of squashfs, server, deps
  inject                Inject autoinstall service into squashfs
  test-dryrun           QEMU test with dry-run (no disk writes)
  test-full             QEMU test with full install to virtual disk
  wol <MAC>             Wake HP Victus via Wake-on-LAN
  all [MAC]             Full pipeline: inject -> server -> dry-run -> wol
  verify                SSH into Victus and verify install (find IP by MAC)
  reinstall             Full autonomous cycle: WoL -> install -> verify

Examples:
  setup-autoinstall.sh status
  setup-autoinstall.sh inject
  setup-autoinstall.sh test-dryrun
  setup-autoinstall.sh wol AA:BB:CC:DD:EE:FF
  setup-autoinstall.sh all AA:BB:CC:DD:EE:FF
  setup-autoinstall.sh verify
  setup-autoinstall.sh reinstall

Prerequisites:
  sudo pacman -S squashfs-tools qemu-full edk2-ovmf wakeonlan nmap
EOF
}

# =============================================================================
# Main
# =============================================================================

CMD="${1:-inject}"
shift || true

case "$CMD" in
    status)     cmd_status ;;
    inject)     cmd_inject ;;
    test-dryrun|dryrun|dry-run)
                cmd_test_dryrun ;;
    test-full|full)
                cmd_test_full ;;
    wol)        cmd_wol "$@" ;;
    all)        cmd_all "$@" ;;
    verify)     cmd_verify ;;
    reinstall)  cmd_reinstall ;;
    help|--help|-h)
                show_help ;;
    *)
        log_error "Unknown command: $CMD"
        show_help
        exit 1
        ;;
esac
