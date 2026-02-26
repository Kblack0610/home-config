#!/usr/bin/env bash
#
# CachyOS PXE Image Preparation
# Downloads the latest CachyOS ISO and extracts boot files for PXE
#
# Usage: prepare-images.sh [--force] [--no-inject]
#
# Options:
#   --force      Re-download and extract even if files exist
#   --no-inject  Skip injecting pxe-autoinstall.service into squashfs
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
source "$SCRIPT_DIR/../base_functions.sh"

# =============================================================================
# Configuration
# =============================================================================

# CachyOS mirror
CACHYOS_MIRROR="https://mirror.cachyos.org/ISO"
CACHYOS_EDITION="desktop"  # desktop, kde, gnome, etc.

# Local paths
IMAGES_DIR="$SUITE_DIR/images"
HTTP_CACHYOS_DIR="$PXE_HTTP_DIR/cachyos"
MOUNT_POINT="/tmp/cachyos-iso-mount"

# Parse arguments
FORCE_DOWNLOAD=false
NO_INJECT=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force|-f)
            FORCE_DOWNLOAD=true
            shift
            ;;
        --no-inject)
            NO_INJECT=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# =============================================================================
# Functions
# =============================================================================

get_latest_iso_info() {
    log_info "Fetching latest CachyOS ISO information..."

    local mirror_url="$CACHYOS_MIRROR/$CACHYOS_EDITION/"

    # Fetch the mirror listing to find dated directories (e.g., 260124/)
    local listing
    listing=$(curl -sL "$mirror_url" 2>/dev/null)

    if [[ -z "$listing" ]]; then
        log_error "Failed to fetch mirror listing from $mirror_url"
        return 1
    fi

    # Find the latest dated directory (format: YYMMDD/)
    local latest_dir
    latest_dir=$(echo "$listing" | grep -oP 'href="[0-9]{6}/"' | grep -oP '[0-9]{6}' | sort -V | tail -1)

    if [[ -z "$latest_dir" ]]; then
        log_error "Could not find dated directory on mirror"
        log_info "Mirror URL: $mirror_url"
        return 1
    fi

    log_info "Latest release directory: $latest_dir"

    # Now fetch that directory to find the ISO
    local dir_listing
    dir_listing=$(curl -sL "${mirror_url}${latest_dir}/" 2>/dev/null)

    # Extract ISO filename
    local iso_name
    iso_name=$(echo "$dir_listing" | grep -oP 'cachyos-'"$CACHYOS_EDITION"'-linux-[0-9]+\.iso' | head -1)

    if [[ -z "$iso_name" ]]; then
        # Try alternate naming patterns
        iso_name=$(echo "$dir_listing" | grep -oP 'cachyos-[^"]+\.iso' | grep -v '.sig' | head -1)
    fi

    if [[ -z "$iso_name" ]]; then
        log_error "Could not find CachyOS ISO in $latest_dir"
        return 1
    fi

    # Return both directory and filename separated by /
    echo "${latest_dir}/${iso_name}"
}

download_iso() {
    local iso_remote_path="$1"
    local iso_name="${iso_remote_path##*/}"
    local iso_url="$CACHYOS_MIRROR/$CACHYOS_EDITION/$iso_remote_path"
    local iso_local_path="$IMAGES_DIR/$iso_name"

    mkdir -p "$IMAGES_DIR"

    # Check if ISO already exists
    if [[ -f "$iso_local_path" ]] && [[ "$FORCE_DOWNLOAD" != "true" ]]; then
        log_info "ISO already exists: $iso_name"
        log_info "Use --force to re-download"
        return 0
    fi

    log_section "Downloading CachyOS ISO"
    log_info "URL: $iso_url"
    log_info "Destination: $iso_local_path"
    log_info "This may take a while (~3-4 GB)..."

    # Download with progress
    if ! curl -L --progress-bar -o "$iso_local_path.tmp" "$iso_url"; then
        log_error "Download failed"
        rm -f "$iso_local_path.tmp"
        return 1
    fi

    # Move to final location
    mv "$iso_local_path.tmp" "$iso_local_path"

    local size
    size=$(du -h "$iso_local_path" | cut -f1)
    log_success "Download complete: $size"
}

extract_boot_files() {
    local iso_path="$1"

    log_section "Extracting Boot Files"

    mkdir -p "$HTTP_CACHYOS_DIR" "$MOUNT_POINT"

    # Mount ISO (requires sudo)
    log_info "Mounting ISO..."
    if ! sudo mount -o loop,ro "$iso_path" "$MOUNT_POINT"; then
        log_error "Failed to mount ISO"
        return 1
    fi

    # Cleanup function
    cleanup() {
        sudo umount "$MOUNT_POINT" 2>/dev/null || true
        rmdir "$MOUNT_POINT" 2>/dev/null || true
    }
    trap cleanup EXIT

    # Find and copy kernel
    log_info "Copying kernel..."
    local kernel_src
    kernel_src=$(find "$MOUNT_POINT" -name "vmlinuz*" -type f | head -1)
    if [[ -z "$kernel_src" ]]; then
        log_error "Kernel not found in ISO"
        return 1
    fi
    cp "$kernel_src" "$HTTP_CACHYOS_DIR/vmlinuz-linux-cachyos"
    log_success "Kernel: $(basename "$kernel_src") -> vmlinuz-linux-cachyos"

    # Find and copy initramfs
    log_info "Copying initramfs..."
    local initrd_src
    initrd_src=$(find "$MOUNT_POINT" -name "initramfs*.img" -type f | head -1)
    if [[ -z "$initrd_src" ]]; then
        # Try alternative names
        initrd_src=$(find "$MOUNT_POINT" -name "initrd*" -type f | head -1)
    fi
    if [[ -z "$initrd_src" ]]; then
        log_error "Initramfs not found in ISO"
        return 1
    fi
    cp "$initrd_src" "$HTTP_CACHYOS_DIR/initramfs-linux-cachyos.img"
    log_success "Initramfs: $(basename "$initrd_src") -> initramfs-linux-cachyos.img"

    # Find and copy root filesystem (squashfs)
    log_info "Copying root filesystem (this may take a while)..."
    local rootfs_src
    rootfs_src=$(find "$MOUNT_POINT" -name "airootfs.sfs" -type f | head -1)
    if [[ -z "$rootfs_src" ]]; then
        # Try alternative locations
        rootfs_src=$(find "$MOUNT_POINT" -name "*.sfs" -type f | head -1)
    fi
    if [[ -z "$rootfs_src" ]]; then
        log_error "Root filesystem (squashfs) not found in ISO"
        return 1
    fi
    cp "$rootfs_src" "$HTTP_CACHYOS_DIR/airootfs.sfs"
    log_success "Rootfs: $(basename "$rootfs_src") -> airootfs.sfs"

    # Copy signature files if present
    if ls "$MOUNT_POINT"/arch/x86_64/*.sig &>/dev/null 2>&1; then
        log_info "Copying signature files..."
        cp "$MOUNT_POINT"/arch/x86_64/*.sig "$HTTP_CACHYOS_DIR/" 2>/dev/null || true
    fi

    # Set permissions
    chmod 644 "$HTTP_CACHYOS_DIR"/*

    # Unmount
    sudo umount "$MOUNT_POINT"
    rmdir "$MOUNT_POINT"
    trap - EXIT

    log_section "Extraction Complete"
    log_info "Boot files ready at: $HTTP_CACHYOS_DIR"
    echo ""
    ls -lh "$HTTP_CACHYOS_DIR/"
}

inject_autoinstall_service() {
    local sfs_path="$HTTP_CACHYOS_DIR/airootfs.sfs"
    local service_src="$PXE_HTTP_DIR/kickstart/pxe-autoinstall.service"
    local tmp_dir="/tmp/cachyos-squashfs-edit"

    log_section "Injecting Autoinstall Service into SquashFS"

    # Validate inputs
    if [[ ! -f "$sfs_path" ]]; then
        log_error "airootfs.sfs not found at: $sfs_path"
        return 1
    fi

    if [[ ! -f "$service_src" ]]; then
        log_error "pxe-autoinstall.service not found at: $service_src"
        return 1
    fi

    # Check for squashfs-tools
    if ! command_exists unsquashfs || ! command_exists mksquashfs; then
        log_error "squashfs-tools not installed"
        log_info "Install with: sudo pacman -S squashfs-tools"
        return 1
    fi

    # Backup original
    if [[ ! -f "${sfs_path}.orig" ]]; then
        log_info "Backing up original squashfs..."
        cp "$sfs_path" "${sfs_path}.orig"
        log_success "Backup: ${sfs_path}.orig"
    fi

    # Clean up any previous extraction
    sudo rm -rf "$tmp_dir"

    # Extract squashfs
    log_info "Extracting squashfs (this may take a while)..."
    sudo unsquashfs -d "$tmp_dir" "$sfs_path"

    # Cleanup on failure
    trap "sudo rm -rf '$tmp_dir'" ERR

    # Copy service file
    log_info "Injecting pxe-autoinstall.service..."
    sudo cp "$service_src" "$tmp_dir/etc/systemd/system/pxe-autoinstall.service"
    sudo chmod 644 "$tmp_dir/etc/systemd/system/pxe-autoinstall.service"

    # Enable the service (create symlink in multi-user.target.wants)
    sudo mkdir -p "$tmp_dir/etc/systemd/system/multi-user.target.wants"
    sudo ln -sf /etc/systemd/system/pxe-autoinstall.service \
        "$tmp_dir/etc/systemd/system/multi-user.target.wants/pxe-autoinstall.service"

    log_success "Service injected and enabled"

    # Repack squashfs
    log_info "Repacking squashfs with zstd compression (this will take a while)..."
    sudo rm -f "$sfs_path"
    sudo mksquashfs "$tmp_dir" "$sfs_path" -comp zstd -Xcompression-level 15 -b 1M

    # Set permissions
    sudo chmod 644 "$sfs_path"

    # Cleanup temp directory
    sudo rm -rf "$tmp_dir"
    trap - ERR

    # Show size comparison
    local orig_size new_size
    orig_size=$(du -h "${sfs_path}.orig" | cut -f1)
    new_size=$(du -h "$sfs_path" | cut -f1)
    log_success "Squashfs repacked"
    log_info "  Original: $orig_size"
    log_info "  Modified: $new_size"
}

verify_files() {
    log_section "Verifying Boot Files"

    local all_ok=true
    local required_files=(
        "vmlinuz-linux-cachyos"
        "initramfs-linux-cachyos.img"
        "airootfs.sfs"
    )

    for f in "${required_files[@]}"; do
        local path="$HTTP_CACHYOS_DIR/$f"
        if [[ -f "$path" ]]; then
            local size
            size=$(du -h "$path" | cut -f1)
            log_success "$f ($size)"
        else
            log_error "$f - MISSING"
            all_ok=false
        fi
    done

    if $all_ok; then
        echo ""
        log_success "All boot files ready!"
        log_info "Start PXE server with: pxe-server start"
    else
        echo ""
        log_error "Some files are missing. Run with --force to re-extract."
        return 1
    fi
}

# =============================================================================
# Main
# =============================================================================

main() {
    log_section "CachyOS PXE Image Preparation"

    # Check dependencies
    local deps=(curl)
    if [[ "$NO_INJECT" != "true" ]]; then
        deps+=(unsquashfs mksquashfs)
    fi
    if ! check_dependencies "${deps[@]}"; then
        exit 1
    fi

    # Check if already prepared (skip if not forcing)
    if [[ -f "$HTTP_CACHYOS_DIR/airootfs.sfs" ]] && [[ "$FORCE_DOWNLOAD" != "true" ]]; then
        log_info "CachyOS images already prepared."
        verify_files
        log_info "Use --force to re-download and extract."
        return 0
    fi

    # Get latest ISO info (returns path like "260124/cachyos-desktop-linux-260124.iso")
    local iso_path
    iso_path=$(get_latest_iso_info)
    local iso_name="${iso_path##*/}"
    log_info "Latest ISO: $iso_name"

    # Download ISO
    download_iso "$iso_path"

    # Extract boot files
    extract_boot_files "$IMAGES_DIR/$iso_name"

    # Inject autoinstall service into squashfs
    if [[ "$NO_INJECT" != "true" ]]; then
        inject_autoinstall_service
    else
        log_info "Skipping autoinstall service injection (--no-inject)"
    fi

    # Verify
    verify_files

    echo ""
    log_info "ISO retained at: $IMAGES_DIR/$iso_name"
    log_info "You can delete it to save space (~3-4GB)"
}

main "$@"
