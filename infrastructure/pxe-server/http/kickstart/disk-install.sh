#!/usr/bin/env bash
#
# PXE Automated Disk Installation for CachyOS
#
# Performs a fully unattended installation:
#   1. Detect target disk (largest internal non-USB/non-removable)
#   2. Partition: EFI (512M) + root (remainder)
#   3. Format: FAT32 (EFI) + ext4 (root)
#   4. Pacstrap base system
#   5. Configure: timezone, locale, hostname, fstab
#   6. Install systemd-boot
#   7. Run auto-provision.sh for user/dotfiles/profile setup
#   8. Reboot
#
# Reads kernel command line parameters:
#   pxe_server=<ip>         - PXE server IP
#   pxe_profile=<name>      - Profile to apply (desktop, laptop, headless)
#   pxe_autoinstall=<0|1>   - Must be 1 to proceed
#   pxe_dryrun=<0|1>        - Log commands without executing destructive ops
#   pxe_disk=<auto|sdX>     - Target disk (auto = largest internal)
#
# Safety layers:
#   - pxe_autoinstall=1 required
#   - Live environment detection (/run/archiso)
#   - UEFI firmware check (/sys/firmware/efi)
#   - 10-second abort countdown
#   - Dry-run mode (pxe_dryrun=1)
#   - USB/removable disk exclusion
#

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

get_cmdline_param() {
    local param="$1"
    local default="${2:-}"
    local value
    value=$(tr ' ' '\n' < /proc/cmdline | grep "^${param}=" | cut -d= -f2 | head -1)
    echo "${value:-$default}"
}

PXE_SERVER=$(get_cmdline_param "pxe_server" "192.168.1.2")
PXE_PROFILE=$(get_cmdline_param "pxe_profile" "desktop")
PXE_AUTOINSTALL=$(get_cmdline_param "pxe_autoinstall" "0")
PXE_DRYRUN=$(get_cmdline_param "pxe_dryrun" "0")
PXE_DISK=$(get_cmdline_param "pxe_disk" "auto")
PXE_FORCE=$(get_cmdline_param "pxe_force" "0")

# Installation target
INSTALL_ROOT="/mnt"

# Timezone
TIMEZONE="America/Los_Angeles"

# Locale
LOCALE="en_US.UTF-8"

# Base packages to install
BASE_PACKAGES=(
    base
    linux-cachyos
    linux-cachyos-headers
    linux-firmware
    networkmanager
    openssh
    sudo
    vim
    base-devel
    git
    stow
    zsh
    efibootmgr
    dosfstools
)

# =============================================================================
# Logging
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()         { echo -e "${BLUE}[DISK]${NC} $*"; }
log_success() { echo -e "${GREEN}[DISK OK]${NC} $*"; }
log_warning() { echo -e "${YELLOW}[DISK WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[DISK ERROR]${NC} $*" >&2; }

log_section() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $*${NC}"
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo ""
}

# Run a command, or log it if dry-run mode
run() {
    if [[ "$PXE_DRYRUN" == "1" ]]; then
        log "[DRY-RUN] $*"
        return 0
    fi
    "$@"
}

# =============================================================================
# Safety Checks
# =============================================================================

safety_checks() {
    log_section "Safety Checks"

    log "PXE Server:     $PXE_SERVER"
    log "Profile:        $PXE_PROFILE"
    log "Auto-install:   $PXE_AUTOINSTALL"
    log "Dry-run:        $PXE_DRYRUN"
    log "Disk selection: $PXE_DISK"
    log "Force install:  $PXE_FORCE"
    echo ""

    # Must have pxe_autoinstall=1
    if [[ "$PXE_AUTOINSTALL" != "1" ]]; then
        log_error "pxe_autoinstall is not set to 1. Aborting."
        exit 1
    fi

    # Must be in live environment
    if [[ ! -d "/run/archiso" ]]; then
        log_error "Not running in a live environment (/run/archiso missing). Aborting."
        exit 1
    fi

    # Prevent boot-loop: skip if target disk already has a valid installation
    if [[ "$PXE_FORCE" == "1" ]]; then
        log_warning "pxe_force=1 — skipping boot-loop prevention check"
    else
        local candidate_disk
        candidate_disk=$(lsblk -dpno NAME,TYPE,TRAN,RM \
            | awk '$2 == "disk" && $4 == "0" && $3 != "usb" {print $1}' \
            | head -1)
        if [[ -n "$candidate_disk" ]]; then
            local part1
            if [[ "$candidate_disk" == *nvme* ]] || [[ "$candidate_disk" == *mmcblk* ]]; then
                part1="${candidate_disk}p1"
            else
                part1="${candidate_disk}1"
            fi
            if [[ -b "$part1" ]] && blkid "$part1" 2>/dev/null | grep -q 'TYPE="vfat"'; then
                local tmp_mnt="/tmp/pxe-boot-check"
                mkdir -p "$tmp_mnt"
                if mount -o ro "$part1" "$tmp_mnt" 2>/dev/null; then
                    if [[ -f "$tmp_mnt/loader/loader.conf" ]] || [[ -f "$tmp_mnt/EFI/systemd/systemd-bootx64.efi" ]]; then
                        umount "$tmp_mnt" 2>/dev/null
                        rmdir "$tmp_mnt" 2>/dev/null
                        log_warning "Existing installation detected on $candidate_disk"
                        log_warning "systemd-boot already installed - skipping to prevent boot-loop"
                        log_warning "To force reinstall, pass pxe_force=1"
                        exit 0
                    fi
                    umount "$tmp_mnt" 2>/dev/null
                fi
                rmdir "$tmp_mnt" 2>/dev/null
            fi
        fi
    fi

    # Must be root
    if [[ $EUID -ne 0 ]]; then
        log_error "Must be run as root. Aborting."
        exit 1
    fi

    # Must be UEFI
    if [[ ! -d "/sys/firmware/efi" ]]; then
        log_error "UEFI firmware not detected (/sys/firmware/efi missing)."
        log_error "This script only supports UEFI systems."
        exit 1
    fi

    # Network check
    if ! ping -c 1 -W 5 "$PXE_SERVER" &>/dev/null; then
        log_error "Cannot reach PXE server at $PXE_SERVER"
        exit 1
    fi

    log_success "All safety checks passed"
}

# =============================================================================
# Abort Countdown
# =============================================================================

abort_countdown() {
    local seconds=10

    echo ""
    log_warning "╔══════════════════════════════════════════════╗"
    log_warning "║  DESTRUCTIVE OPERATION IN ${seconds} SECONDS          ║"
    log_warning "║  This will WIPE the target disk entirely!    ║"
    log_warning "║                                              ║"
    log_warning "║  Press Ctrl+C to abort                       ║"
    log_warning "║  Or: touch /tmp/pxe-abort                    ║"
    log_warning "╚══════════════════════════════════════════════╝"
    echo ""

    for ((i = seconds; i > 0; i--)); do
        if [[ -f /tmp/pxe-abort ]]; then
            log_error "Abort file detected. Stopping."
            rm -f /tmp/pxe-abort
            exit 1
        fi
        echo -ne "\r  ${YELLOW}Starting in ${i}...${NC}  "
        sleep 1
    done
    echo ""
    echo ""
}

# =============================================================================
# Disk Detection
# =============================================================================

detect_disk() {
    log_section "Disk Detection"

    if [[ "$PXE_DISK" != "auto" ]]; then
        # User specified a disk
        local disk="/dev/$PXE_DISK"
        if [[ ! -b "$disk" ]]; then
            log_error "Specified disk $disk does not exist"
            exit 1
        fi
        TARGET_DISK="$disk"
        log "Using specified disk: $TARGET_DISK"
        return 0
    fi

    # Auto-detect: largest internal (non-USB, non-removable) disk
    log "Auto-detecting target disk..."
    log "Scanning block devices..."
    echo ""

    # List all disks with relevant info
    lsblk -dpno NAME,SIZE,TYPE,TRAN,RM | while read -r line; do
        log "  $line"
    done
    echo ""

    # Filter: TYPE=disk, RM=0 (not removable), TRAN!=usb
    TARGET_DISK=$(lsblk -dpno NAME,SIZE,TYPE,TRAN,RM \
        | awk '$3 == "disk" && $5 == "0" && $4 != "usb" {print $1, $2}' \
        | sort -k2 -h \
        | tail -1 \
        | awk '{print $1}')

    if [[ -z "${TARGET_DISK:-}" ]]; then
        log_error "No suitable internal disk found"
        log_error "Criteria: non-removable, non-USB disk"
        exit 1
    fi

    local disk_size
    disk_size=$(lsblk -dpno SIZE "$TARGET_DISK" | tr -d ' ')
    log_success "Selected disk: $TARGET_DISK ($disk_size)"
}

# =============================================================================
# Partitioning
# =============================================================================

partition_disk() {
    log_section "Partitioning: $TARGET_DISK"

    log "Wiping existing partition table..."
    run sgdisk --zap-all "$TARGET_DISK"

    log "Creating EFI partition (512M)..."
    run sgdisk -n 1:0:+512M -t 1:EF00 -c 1:"EFI" "$TARGET_DISK"

    log "Creating root partition (remaining space)..."
    run sgdisk -n 2:0:0 -t 2:8300 -c 2:"root" "$TARGET_DISK"

    # Inform kernel of partition changes
    run partprobe "$TARGET_DISK" 2>/dev/null || true
    sleep 2

    # Determine partition naming (nvme vs sd)
    if [[ "$TARGET_DISK" == *nvme* ]] || [[ "$TARGET_DISK" == *mmcblk* ]]; then
        EFI_PART="${TARGET_DISK}p1"
        ROOT_PART="${TARGET_DISK}p2"
    else
        EFI_PART="${TARGET_DISK}1"
        ROOT_PART="${TARGET_DISK}2"
    fi

    log_success "Partitions created:"
    log "  EFI:  $EFI_PART (512M, FAT32)"
    log "  Root: $ROOT_PART (ext4)"
}

# =============================================================================
# Formatting
# =============================================================================

format_partitions() {
    log_section "Formatting Partitions"

    log "Formatting EFI partition as FAT32..."
    run mkfs.fat -F32 "$EFI_PART"

    log "Formatting root partition as ext4..."
    run mkfs.ext4 -F "$ROOT_PART"

    log_success "Formatting complete"
}

# =============================================================================
# Mount
# =============================================================================

mount_partitions() {
    log_section "Mounting Partitions"

    log "Mounting root -> $INSTALL_ROOT"
    run mount "$ROOT_PART" "$INSTALL_ROOT"

    log "Creating and mounting EFI -> $INSTALL_ROOT/boot"
    run mkdir -p "$INSTALL_ROOT/boot"
    run mount "$EFI_PART" "$INSTALL_ROOT/boot"

    log_success "Partitions mounted"
}

# =============================================================================
# Pacstrap
# =============================================================================

install_base() {
    log_section "Installing Base System"

    log "Packages: ${BASE_PACKAGES[*]}"
    echo ""

    run pacstrap -K "$INSTALL_ROOT" "${BASE_PACKAGES[@]}"

    log_success "Base system installed"
}

# =============================================================================
# Generate fstab
# =============================================================================

generate_fstab() {
    log_section "Generating fstab"

    if [[ "$PXE_DRYRUN" == "1" ]]; then
        log "[DRY-RUN] genfstab -U $INSTALL_ROOT >> $INSTALL_ROOT/etc/fstab"
    else
        genfstab -U "$INSTALL_ROOT" >> "$INSTALL_ROOT/etc/fstab"
    fi

    log_success "fstab generated"
    if [[ "$PXE_DRYRUN" != "1" ]] && [[ -f "$INSTALL_ROOT/etc/fstab" ]]; then
        log "Contents:"
        cat "$INSTALL_ROOT/etc/fstab"
    fi
}

# =============================================================================
# System Configuration (in chroot)
# =============================================================================

configure_system() {
    log_section "Configuring System"

    # Generate hostname: {profile}-{last4 of MAC}
    local mac4
    mac4=$(ip link show | grep -A1 'state UP' | grep ether | awk '{print $2}' | tr -d ':' | tail -c 5 | head -c 4)
    local hostname="${PXE_PROFILE}-${mac4:-0000}"

    log "Timezone:  $TIMEZONE"
    log "Locale:    $LOCALE"
    log "Hostname:  $hostname"
    echo ""

    if [[ "$PXE_DRYRUN" == "1" ]]; then
        log "[DRY-RUN] Would configure timezone, locale, hostname in chroot"
        return 0
    fi

    # Timezone
    arch-chroot "$INSTALL_ROOT" ln -sf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
    arch-chroot "$INSTALL_ROOT" hwclock --systohc

    # Locale
    echo "$LOCALE UTF-8" > "$INSTALL_ROOT/etc/locale.gen"
    arch-chroot "$INSTALL_ROOT" locale-gen
    echo "LANG=$LOCALE" > "$INSTALL_ROOT/etc/locale.conf"

    # Hostname
    echo "$hostname" > "$INSTALL_ROOT/etc/hostname"

    # Hosts file
    cat > "$INSTALL_ROOT/etc/hosts" <<EOF
127.0.0.1   localhost
::1         localhost
127.0.1.1   $hostname.localdomain $hostname
EOF

    log_success "System configured"
}

# =============================================================================
# Bootstrap Access (guaranteed login regardless of auto-provision outcome)
# =============================================================================

bootstrap_access() {
    log_section "Bootstrapping Access"

    if [[ "$PXE_DRYRUN" == "1" ]]; then
        log "[DRY-RUN] Would bootstrap root/user access, SSH keys, and services"
        return 0
    fi

    # Root password
    log "Setting root password..."
    echo "root:changeme" | arch-chroot "$INSTALL_ROOT" chpasswd

    # Create user (with fallback groups if some don't exist yet)
    log "Creating user kblack0610..."
    if ! grep -q "^kblack0610:" "$INSTALL_ROOT/etc/passwd"; then
        arch-chroot "$INSTALL_ROOT" useradd -m -G wheel -s /bin/zsh kblack0610
        # Add optional groups individually (don't fail if they don't exist)
        for grp in docker input video audio; do
            arch-chroot "$INSTALL_ROOT" usermod -aG "$grp" kblack0610 2>/dev/null || true
        done
        log_success "User created"
    else
        log "User kblack0610 already exists"
    fi

    # User password
    log "Setting user password..."
    echo "kblack0610:changeme" | arch-chroot "$INSTALL_ROOT" chpasswd

    # Passwordless sudo
    log "Enabling passwordless sudo for wheel..."
    echo "%wheel ALL=(ALL:ALL) NOPASSWD: ALL" > "$INSTALL_ROOT/etc/sudoers.d/wheel-nopasswd"
    chmod 440 "$INSTALL_ROOT/etc/sudoers.d/wheel-nopasswd"

    # SSH key injection
    log "Injecting SSH authorized key..."
    local ssh_dir="$INSTALL_ROOT/home/kblack0610/.ssh"
    mkdir -p "$ssh_dir"
    # Embed the workstation's public key
    cat > "$ssh_dir/authorized_keys" <<'SSHEOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM6yTlK3GCzXC2+njcPtTucjwxb53Sb0+JT+TuTD78Jh kblack0610@gmail.com
SSHEOF
    chmod 700 "$ssh_dir"
    chmod 600 "$ssh_dir/authorized_keys"
    arch-chroot "$INSTALL_ROOT" chown -R kblack0610:kblack0610 /home/kblack0610/.ssh

    # Enable essential services
    log "Enabling sshd and NetworkManager..."
    arch-chroot "$INSTALL_ROOT" systemctl enable sshd 2>/dev/null || true
    arch-chroot "$INSTALL_ROOT" systemctl enable NetworkManager 2>/dev/null || true

    log_success "Bootstrap access configured (root + kblack0610 + SSH key + services)"
}

# =============================================================================
# Bootloader (systemd-boot)
# =============================================================================

install_bootloader() {
    log_section "Installing Bootloader (systemd-boot)"

    if [[ "$PXE_DRYRUN" == "1" ]]; then
        log "[DRY-RUN] Would install systemd-boot and create loader entries"
        return 0
    fi

    # Install systemd-boot
    arch-chroot "$INSTALL_ROOT" bootctl install

    # Loader configuration
    cat > "$INSTALL_ROOT/boot/loader/loader.conf" <<EOF
default cachyos.conf
timeout 3
console-mode max
editor no
EOF

    # Get root partition PARTUUID
    local root_partuuid
    root_partuuid=$(blkid -s PARTUUID -o value "$ROOT_PART")

    # Boot entry
    mkdir -p "$INSTALL_ROOT/boot/loader/entries"
    cat > "$INSTALL_ROOT/boot/loader/entries/cachyos.conf" <<EOF
title   CachyOS
linux   /vmlinuz-linux-cachyos
initrd  /initramfs-linux-cachyos.img
options root=PARTUUID=$root_partuuid rw quiet
EOF

    # Fallback entry
    cat > "$INSTALL_ROOT/boot/loader/entries/cachyos-fallback.conf" <<EOF
title   CachyOS (fallback)
linux   /vmlinuz-linux-cachyos
initrd  /initramfs-linux-cachyos-fallback.img
options root=PARTUUID=$root_partuuid rw
EOF

    log_success "Bootloader installed"
    log "Root PARTUUID: $root_partuuid"
}

# =============================================================================
# Run Auto-Provision
# =============================================================================

run_provisioning() {
    log_section "Running Auto-Provisioning"

    local provision_url="http://$PXE_SERVER:9080/kickstart/auto-provision.sh"

    log "Fetching provisioning script from $provision_url..."

    if [[ "$PXE_DRYRUN" == "1" ]]; then
        log "[DRY-RUN] Would download and run auto-provision.sh in chroot"
        return 0
    fi

    if ! curl -sfL "$provision_url" -o "$INSTALL_ROOT/tmp/auto-provision.sh"; then
        log_warning "Failed to download auto-provision.sh"
        log_warning "Provisioning will need to be run manually after first boot"
        return 0
    fi

    chmod +x "$INSTALL_ROOT/tmp/auto-provision.sh"

    # Copy kernel cmdline params so auto-provision.sh can read them in chroot
    # The script reads /proc/cmdline, but in chroot it sees the host's cmdline - which is correct here
    log "Running provisioning in chroot..."
    arch-chroot "$INSTALL_ROOT" bash /tmp/auto-provision.sh --chroot || {
        log_warning "Auto-provisioning reported errors (non-fatal)"
    }

    rm -f "$INSTALL_ROOT/tmp/auto-provision.sh"

    log_success "Provisioning complete"
}

# =============================================================================
# Cleanup & Reboot
# =============================================================================

cleanup_and_reboot() {
    log_section "Installation Complete!"

    log "Unmounting partitions..."
    umount -R "$INSTALL_ROOT" 2>/dev/null || true

    echo ""
    log_success "╔══════════════════════════════════════════════╗"
    log_success "║  CachyOS installation complete!              ║"
    log_success "║  Profile: $PXE_PROFILE"
    log_success "║  The system will reboot in 5 seconds...      ║"
    log_success "╚══════════════════════════════════════════════╝"
    echo ""

    if [[ "$PXE_DRYRUN" == "1" ]]; then
        log "[DRY-RUN] Would reboot now"
        return 0
    fi

    sleep 5
    reboot
}

# =============================================================================
# Main
# =============================================================================

main() {
    log_section "PXE Automated Disk Installation"
    log "Started at: $(date)"

    safety_checks
    detect_disk

    if [[ "$PXE_DRYRUN" != "1" ]]; then
        abort_countdown
    else
        log "[DRY-RUN] Skipping abort countdown"
    fi

    partition_disk
    format_partitions
    mount_partitions
    install_base
    generate_fstab
    configure_system
    bootstrap_access
    install_bootloader
    run_provisioning
    cleanup_and_reboot
}

main "$@"
