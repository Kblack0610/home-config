#!/usr/bin/env bash
#
# Create an iPXE USB Boot Image
#
# Builds a custom iPXE EFI binary with an embedded script that
# auto-chains to your PXE server, then packages it as a bootable
# disk image you can dd onto a USB drive.
#
# Use case: machines where you can't enable BIOS Network Stack
# (broken screens, locked firmware, etc.) but can boot from USB.
#
# Usage: make-usb.sh [options]
#
# Options:
#   --output <path>   Output image path (default: /tmp/ipxe-boot.img)
#   --flash <device>  Write image directly to USB device (e.g., /dev/sdb)
#   --server <ip>     PXE server IP (default: auto-detect)
#   --port <port>     PXE HTTP port (default: 9080)
#   --help            Show this help message
#

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
source "$SCRIPT_DIR/../base_functions.sh"

# =============================================================================
# Configuration
# =============================================================================

OUTPUT_IMG="/tmp/ipxe-boot.img"
FLASH_DEV=""
SERVER_IP="${PXE_SERVER_IP:-$(get_local_ip)}"
HTTP_PORT="${PXE_HTTP_PORT:-9080}"
BUILD_DIR="/tmp/ipxe-build"
IMG_SIZE_MB=16

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output|-o)  OUTPUT_IMG="$2"; shift 2 ;;
        --flash|-f)   FLASH_DEV="$2"; shift 2 ;;
        --server|-s)  SERVER_IP="$2"; shift 2 ;;
        --port|-p)    HTTP_PORT="$2"; shift 2 ;;
        --help|-h)    show_help; exit 0 ;;
        *)            shift ;;
    esac
done

# =============================================================================
# Functions
# =============================================================================

show_help() {
    cat <<'EOF'
iPXE USB Boot Image Builder

Creates a bootable USB image that bypasses BIOS Network Stack by loading
iPXE from USB, which handles all networking and chains to your PXE server.

Usage: make-usb.sh [options]

Options:
  --output <path>   Output image path (default: /tmp/ipxe-boot.img)
  --flash <device>  Write image directly to USB device (e.g., /dev/sdb)
  --server <ip>     PXE server IP (default: auto-detect)
  --port <port>     PXE HTTP port (default: 9080)

Examples:
  make-usb.sh                              # Build image to /tmp/ipxe-boot.img
  make-usb.sh --flash /dev/sdb             # Build and write to USB
  make-usb.sh --server 192.168.1.2         # Specify PXE server IP

After building, write to USB with:
  sudo dd if=/tmp/ipxe-boot.img of=/dev/sdX bs=1M status=progress

Requirements: git, make, gcc, mtools (mformat, mcopy)
EOF
}

check_build_deps() {
    log_info "Checking build dependencies..."
    local deps=(git make gcc mformat mcopy)
    local missing=()

    for dep in "${deps[@]}"; do
        if ! command_exists "$dep"; then
            missing+=("$dep")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing[*]}"
        log_info "Install with: sudo pacman -S base-devel git mtools"
        exit 1
    fi
}

clone_ipxe() {
    if [[ -d "$BUILD_DIR/src" ]]; then
        log_info "iPXE source already exists at $BUILD_DIR"
        return 0
    fi

    log_info "Cloning iPXE source..."
    git clone --depth 1 https://github.com/ipxe/ipxe.git "$BUILD_DIR"
    log_success "iPXE source cloned"
}

build_ipxe() {
    log_section "Building Custom iPXE EFI Binary"

    local script_file="$BUILD_DIR/src/chain-pxe.ipxe"
    local menu_url="http://${SERVER_IP}:${HTTP_PORT}/ipxe/menu.ipxe"

    log_info "PXE Server: ${SERVER_IP}:${HTTP_PORT}"
    log_info "Chain URL:  $menu_url"

    # Write the embedded script
    cat > "$script_file" <<IPXE
#!ipxe

echo
echo ==========================================
echo  iPXE USB Boot - PXE Server Chainloader
echo ==========================================
echo  Server: ${SERVER_IP}:${HTTP_PORT}
echo ==========================================
echo

:retry
dhcp || goto retry_wait
echo DHCP acquired: \${net0/ip}
echo

echo Chaining to PXE server...
chain ${menu_url} || goto failed

:retry_wait
echo DHCP failed, retrying in 3 seconds...
sleep 3
goto retry

:failed
echo
echo Failed to reach PXE server at ${SERVER_IP}!
echo Dropping to iPXE shell...
echo
echo Manual commands:
echo   dhcp
echo   chain ${menu_url}
echo
shell
IPXE

    log_info "Compiling iPXE with embedded chainload script..."
    make -C "$BUILD_DIR/src" bin-x86_64-efi/ipxe.efi EMBED=chain-pxe.ipxe -j"$(nproc)" 2>&1 | tail -3

    if [[ ! -f "$BUILD_DIR/src/bin-x86_64-efi/ipxe.efi" ]]; then
        log_error "Build failed"
        exit 1
    fi

    log_success "iPXE EFI binary built"
}

create_image() {
    log_section "Creating Bootable USB Image"

    local efi_binary="$BUILD_DIR/src/bin-x86_64-efi/ipxe.efi"

    # Create empty image with space for GPT + EFI partition
    log_info "Creating ${IMG_SIZE_MB}MB GPT disk image..."
    dd if=/dev/zero of="$OUTPUT_IMG" bs=1M count="$IMG_SIZE_MB" 2>/dev/null

    # Create GPT partition table with EFI System Partition using sfdisk
    log_info "Creating GPT partition table with EFI System Partition..."
    sfdisk "$OUTPUT_IMG" <<SFDISK 2>/dev/null
label: gpt
type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="EFI System"
SFDISK

    # GPT standard: first partition starts at sector 2048
    local part_start=2048
    local offset_bytes=$((part_start * 512))

    log_info "EFI partition at sector $part_start (offset ${offset_bytes} bytes)"

    # Format the EFI partition as FAT32 using mtools with offset
    # mtools needs MTOOLS_SKIP_CHECK to handle partition images
    MTOOLS_SKIP_CHECK=1 mformat -i "$OUTPUT_IMG@@$offset_bytes" -F -v IPXEBOOT ::

    # Create EFI boot structure and copy iPXE
    MTOOLS_SKIP_CHECK=1 mmd -i "$OUTPUT_IMG@@$offset_bytes" ::/EFI
    MTOOLS_SKIP_CHECK=1 mmd -i "$OUTPUT_IMG@@$offset_bytes" ::/EFI/BOOT
    MTOOLS_SKIP_CHECK=1 mcopy -i "$OUTPUT_IMG@@$offset_bytes" "$efi_binary" ::/EFI/BOOT/BOOTX64.EFI

    # Verify
    log_info "Verifying image contents..."
    MTOOLS_SKIP_CHECK=1 mdir -i "$OUTPUT_IMG@@$offset_bytes" ::/EFI/BOOT/ 2>&1 | grep -q "BOOTX64" || {
        log_error "Verification failed - BOOTX64.EFI not found in image"
        exit 1
    }

    local img_size
    img_size=$(du -h "$OUTPUT_IMG" | cut -f1)
    log_success "Image created: $OUTPUT_IMG ($img_size)"
    log_info "Partition table: GPT with EFI System Partition (FAT32)"
}

flash_usb() {
    if [[ -z "$FLASH_DEV" ]]; then
        return 0
    fi

    if [[ ! -b "$FLASH_DEV" ]]; then
        log_error "Device $FLASH_DEV does not exist"
        exit 1
    fi

    # Safety check - don't write to a mounted device
    if mount | grep -q "$FLASH_DEV"; then
        log_error "$FLASH_DEV is mounted. Unmount it first."
        exit 1
    fi

    log_warning "This will ERASE $FLASH_DEV"
    read -rp "Continue? [y/N] " answer
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
        log_info "Cancelled"
        return 0
    fi

    log_info "Writing image to $FLASH_DEV..."
    sudo dd if="$OUTPUT_IMG" of="$FLASH_DEV" bs=1M status=progress
    sync

    log_success "USB drive ready: $FLASH_DEV"
}

# =============================================================================
# Main
# =============================================================================

main() {
    log_section "iPXE USB Boot Image Builder"

    check_build_deps
    clone_ipxe
    build_ipxe
    create_image
    flash_usb

    log_section "Done!"
    echo "Image: $OUTPUT_IMG"
    echo ""
    echo "To write to USB:"
    echo "  sudo dd if=$OUTPUT_IMG of=/dev/sdX bs=1M status=progress"
    echo ""
    echo "Boot flow:"
    echo "  USB -> iPXE -> DHCP -> http://${SERVER_IP}:${HTTP_PORT}/ipxe/menu.ipxe"
    echo ""
}

main "$@"
