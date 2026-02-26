#!/usr/bin/env bash
#
# PXE Server Installation Script
#
# This script sets up the PXE server by:
# 1. Checking and installing dependencies
# 2. Downloading iPXE binaries
# 3. Setting up SYSLINUX for BIOS boot
# 4. Creating symlinks
# 5. Installing systemd service (optional)
#
# Usage: ./install.sh [--no-symlink] [--no-systemd]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/base_functions.sh"

# =============================================================================
# Configuration
# =============================================================================

BIN_DIR="$HOME/.local/bin"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

# Parse arguments
INSTALL_SYMLINK=true
INSTALL_SYSTEMD=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-symlink)
            INSTALL_SYMLINK=false
            shift
            ;;
        --no-systemd)
            INSTALL_SYSTEMD=false
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

check_dependencies() {
    log_section "Checking Dependencies"

    local deps=("dnsmasq" "python3" "curl")
    local missing=()

    for dep in "${deps[@]}"; do
        if command_exists "$dep"; then
            log_success "$dep installed"
        else
            log_warning "$dep missing"
            missing+=("$dep")
        fi
    done

    # Optional dependencies
    if command_exists syslinux; then
        log_success "syslinux installed (BIOS boot support)"
    else
        log_warning "syslinux not installed (BIOS boot will be limited)"
    fi

    if command_exists qemu-system-x86_64; then
        log_success "qemu installed (VM testing available)"
    else
        log_info "qemu not installed (VM testing unavailable)"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo ""
        log_warning "Missing required dependencies: ${missing[*]}"
        echo ""
        echo "Install with:"
        echo "  sudo pacman -S ${missing[*]}"
        echo ""
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

create_directories() {
    log_section "Creating Directories"

    local dirs=(
        "$PXE_BOOT_DIR/bios/pxelinux.cfg"
        "$PXE_BOOT_DIR/uefi"
        "$PXE_HTTP_DIR/cachyos"
        "$PXE_HTTP_DIR/ipxe"
        "$PXE_HTTP_DIR/kickstart/profiles"
        "$PXE_HTTP_DIR/utils"
        "$PXE_IMAGES_DIR"
        "$BIN_DIR"
    )

    for dir in "${dirs[@]}"; do
        mkdir -p "$dir"
        log_success "Created: ${dir#$HOME/}"
    done
}

download_ipxe() {
    log_section "Downloading iPXE Binaries"

    local ipxe_url="https://boot.ipxe.org"

    # UEFI binary
    local uefi_bin="$PXE_BOOT_DIR/uefi/ipxe.efi"
    if [[ -f "$uefi_bin" ]]; then
        log_info "ipxe.efi already exists"
    else
        log_info "Downloading ipxe.efi..."
        if curl -sL "$ipxe_url/ipxe.efi" -o "$uefi_bin"; then
            log_success "Downloaded ipxe.efi"
        else
            log_error "Failed to download ipxe.efi"
        fi
    fi

    # BIOS chainload kernel
    local bios_lkrn="$PXE_BOOT_DIR/bios/ipxe.lkrn"
    if [[ -f "$bios_lkrn" ]]; then
        log_info "ipxe.lkrn already exists"
    else
        log_info "Downloading ipxe.lkrn..."
        if curl -sL "$ipxe_url/ipxe.lkrn" -o "$bios_lkrn"; then
            log_success "Downloaded ipxe.lkrn"
        else
            log_error "Failed to download ipxe.lkrn"
        fi
    fi

    # UEFI SNP binary (alternative)
    local snp_bin="$PXE_BOOT_DIR/uefi/snponly.efi"
    if [[ ! -f "$snp_bin" ]]; then
        log_info "Downloading snponly.efi..."
        curl -sL "$ipxe_url/snponly.efi" -o "$snp_bin" 2>/dev/null || true
    fi
}

setup_syslinux() {
    log_section "Setting Up SYSLINUX (BIOS Boot)"

    if ! command_exists syslinux; then
        log_warning "syslinux not installed, skipping BIOS setup"
        log_info "Install with: sudo pacman -S syslinux"
        return 0
    fi

    # Find syslinux files
    local syslinux_dirs=(
        "/usr/lib/syslinux/bios"
        "/usr/share/syslinux"
        "/usr/lib/SYSLINUX"
    )

    local syslinux_dir=""
    for dir in "${syslinux_dirs[@]}"; do
        if [[ -d "$dir" ]] && [[ -f "$dir/pxelinux.0" ]]; then
            syslinux_dir="$dir"
            break
        fi
    done

    if [[ -z "$syslinux_dir" ]]; then
        log_warning "Could not find syslinux files"
        return 0
    fi

    log_info "Found syslinux at: $syslinux_dir"

    # Copy required files
    local files=("pxelinux.0" "ldlinux.c32" "menu.c32" "libutil.c32" "libcom32.c32")
    for f in "${files[@]}"; do
        if [[ -f "$syslinux_dir/$f" ]]; then
            cp "$syslinux_dir/$f" "$PXE_BOOT_DIR/bios/"
            log_success "Copied $f"
        fi
    done

    # Create PXELINUX config
    log_info "Creating PXELINUX configuration..."
    cat > "$PXE_BOOT_DIR/bios/pxelinux.cfg/default" <<'EOF'
# PXELINUX Configuration
# This chainloads to iPXE for the full boot menu

DEFAULT ipxe
PROMPT 0
TIMEOUT 30

LABEL ipxe
    MENU LABEL Boot iPXE
    KERNEL ipxe.lkrn
    APPEND dhcp && chain http://${next-server}:9080/ipxe/menu.ipxe
EOF

    log_success "PXELINUX configured"
}

install_symlink() {
    log_section "Installing Symlink"

    if [[ "$INSTALL_SYMLINK" != "true" ]]; then
        log_info "Skipping symlink installation (--no-symlink)"
        return 0
    fi

    local source="$SCRIPT_DIR/pxe-server.sh"
    local target="$BIN_DIR/pxe-server"

    if [[ -L "$target" ]]; then
        rm "$target"
    elif [[ -f "$target" ]]; then
        log_warning "File exists at $target (not a symlink)"
        read -p "Replace? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm "$target"
        else
            return 0
        fi
    fi

    ln -s "$source" "$target"
    log_success "Symlink created: pxe-server -> $source"
    log_info "Command available: pxe-server"
}

install_systemd_service() {
    log_section "Installing Systemd Service"

    if [[ "$INSTALL_SYSTEMD" != "true" ]]; then
        log_info "Skipping systemd installation (--no-systemd)"
        return 0
    fi

    mkdir -p "$SYSTEMD_USER_DIR"

    local service_file="$SYSTEMD_USER_DIR/pxe-server.service"

    cat > "$service_file" <<EOF
[Unit]
Description=PXE Boot Server
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
ExecStart=$SCRIPT_DIR/pxe-server.sh start
ExecStop=$SCRIPT_DIR/pxe-server.sh stop
ExecReload=$SCRIPT_DIR/pxe-server.sh restart
PIDFile=/tmp/pxe-server/dnsmasq.pid
Restart=on-failure
RestartSec=5
Environment="HOME=$HOME"

[Install]
WantedBy=default.target
EOF

    # Reload systemd
    systemctl --user daemon-reload

    log_success "Systemd service installed"
    log_info "Enable with: systemctl --user enable pxe-server"
    log_info "Start with:  systemctl --user start pxe-server"
}

print_next_steps() {
    log_section "Installation Complete!"

    local server_ip
    server_ip=$(get_local_ip)

    echo "Next steps:"
    echo ""
    echo "1. Prepare CachyOS images:"
    echo "   pxe-server prepare"
    echo ""
    echo "2. Configure OpenWRT router:"
    echo "   ssh root@<router-ip>"
    echo "   uci add_list dhcp.@dnsmasq[0].dhcp_option='66,$server_ip'"
    echo "   uci commit dhcp"
    echo "   /etc/init.d/dnsmasq restart"
    echo ""
    echo "3. Start the PXE server:"
    echo "   pxe-server start"
    echo ""
    echo "4. On target machine:"
    echo "   - Enter BIOS/UEFI (F2/F12/Del at boot)"
    echo "   - Select Network Boot / PXE"
    echo "   - Choose installation profile from menu"
    echo ""
    echo "Documentation: $SCRIPT_DIR/config/openwrt/README.md"
    echo ""
}

# =============================================================================
# Main
# =============================================================================

main() {
    log_section "PXE Server Installation"

    check_dependencies
    create_directories
    download_ipxe
    setup_syslinux
    install_symlink
    install_systemd_service
    print_next_steps
}

main "$@"
