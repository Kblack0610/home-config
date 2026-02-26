#!/usr/bin/env bash
#
# PXE Post-Install Provisioning Script
#
# This script runs after CachyOS base installation to:
# 1. Create user account
# 2. Clone dotfiles repository
# 3. Run installation/provisioning scripts
# 4. Apply profile-specific configuration
# 5. Enable essential services
#
# It reads parameters from kernel command line:
#   pxe_server=<ip>         - PXE server IP
#   pxe_profile=<name>      - Profile to apply (desktop, laptop, headless)
#   pxe_autoinstall=<0|1>   - Whether to auto-install
#
# Usage (from live environment):
#   curl -sL http://<pxe-server>:9080/kickstart/auto-provision.sh | bash
#

set -euo pipefail

# =============================================================================
# Argument Parsing
# =============================================================================

FORCE_CHROOT=false
for arg in "$@"; do
    case "$arg" in
        --chroot) FORCE_CHROOT=true ;;
    esac
done

# =============================================================================
# Configuration
# =============================================================================

# Read kernel command line parameters
get_cmdline_param() {
    local param="$1"
    local default="${2:-}"
    local value
    value=$(cat /proc/cmdline | tr ' ' '\n' | grep "^${param}=" | cut -d= -f2 | head -1)
    echo "${value:-$default}"
}

PXE_SERVER=$(get_cmdline_param "pxe_server" "192.168.1.100")
PXE_PROFILE=$(get_cmdline_param "pxe_profile" "desktop")
PXE_AUTOINSTALL=$(get_cmdline_param "pxe_autoinstall" "0")

# User configuration
TARGET_USER="kblack0610"
TARGET_HOME="/home/$TARGET_USER"
DOTFILES_REPO="https://github.com/kblack0610/.dotfiles.git"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# =============================================================================
# Logging
# =============================================================================

log()         { echo -e "${BLUE}[PXE]${NC} $*"; }
log_success() { echo -e "${GREEN}[PXE OK]${NC} $*"; }
log_warning() { echo -e "${YELLOW}[PXE WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[PXE ERROR]${NC} $*" >&2; }

log_section() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $*${NC}"
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo ""
}

# =============================================================================
# Pre-flight Checks
# =============================================================================

preflight_check() {
    log_section "Pre-flight Check"

    log "PXE Server:    $PXE_SERVER"
    log "Profile:       $PXE_PROFILE"
    log "Auto-install:  $PXE_AUTOINSTALL"
    log "Target User:   $TARGET_USER"
    log "Force chroot:  $FORCE_CHROOT"
    echo ""

    # Determine environment
    if [[ "$FORCE_CHROOT" == "true" ]]; then
        # Called with --chroot from disk-install.sh's arch-chroot
        # We're already inside the chroot — run commands directly
        log "Detected: Running in chroot (--chroot flag)"
        INSTALL_ROOT=""
        IN_CHROOT=true
    elif [[ -d "/mnt" ]] && mountpoint -q /mnt 2>/dev/null; then
        log "Detected: Running in live environment with /mnt mounted"
        INSTALL_ROOT="/mnt"
        IN_CHROOT=false
    elif [[ "$(stat -c %d:%i /)" != "$(stat -c %d:%i /proc/1/root/.)" ]] 2>/dev/null; then
        log "Detected: Running in chroot (auto-detected)"
        INSTALL_ROOT=""
        IN_CHROOT=true
    else
        log "Detected: Running on installed system"
        INSTALL_ROOT=""
        IN_CHROOT=false
    fi

    # Check network connectivity
    if ! ping -c 1 -W 3 "$PXE_SERVER" &>/dev/null; then
        log_warning "Cannot reach PXE server at $PXE_SERVER"
    fi

    # Check if git is available
    if ! command -v git &>/dev/null; then
        log_error "git is not installed"
        return 1
    fi
}

# =============================================================================
# User Setup
# =============================================================================

setup_user() {
    log_section "Setting Up User: $TARGET_USER"

    local user_home="${INSTALL_ROOT}${TARGET_HOME}"

    # Check if user already exists (bootstrap_access may have created it)
    if grep -q "^${TARGET_USER}:" "${INSTALL_ROOT:-}/etc/passwd" 2>/dev/null; then
        log "User $TARGET_USER already exists — skipping creation"
    else
        log "Creating user $TARGET_USER..."

        if [[ -n "$INSTALL_ROOT" ]]; then
            arch-chroot "$INSTALL_ROOT" useradd -m -G wheel,docker,input,video,audio -s /bin/zsh "$TARGET_USER" 2>/dev/null || \
            arch-chroot "$INSTALL_ROOT" useradd -m -G wheel -s /bin/bash "$TARGET_USER"
        else
            useradd -m -G wheel,docker,input,video,audio -s /bin/zsh "$TARGET_USER" 2>/dev/null || \
            useradd -m -G wheel -s /bin/bash "$TARGET_USER"
        fi

        log_success "User created"

        # Only set password for newly created users
        log "Setting temporary password (please change on first login)..."
        if [[ -n "$INSTALL_ROOT" ]]; then
            echo "$TARGET_USER:changeme" | arch-chroot "$INSTALL_ROOT" chpasswd
        else
            echo "$TARGET_USER:changeme" | chpasswd
        fi
    fi

    # Ensure passwordless sudo for initial setup
    log "Enabling passwordless sudo for wheel group..."
    if [[ -f "${INSTALL_ROOT:-}/etc/sudoers.d/wheel-nopasswd" ]]; then
        log "Passwordless sudo already configured"
    else
        echo "%wheel ALL=(ALL:ALL) NOPASSWD: ALL" > "${INSTALL_ROOT:-}/etc/sudoers.d/wheel-nopasswd"
        chmod 440 "${INSTALL_ROOT:-}/etc/sudoers.d/wheel-nopasswd"
    fi

    log_success "User setup complete"
}

# =============================================================================
# Dotfiles Setup
# =============================================================================

setup_dotfiles() {
    log_section "Setting Up Dotfiles"

    local user_home="${INSTALL_ROOT}${TARGET_HOME}"
    local dotfiles_dir="$user_home/.dotfiles"

    # Clone dotfiles
    if [[ -d "$dotfiles_dir/.git" ]]; then
        log "Dotfiles already exist, pulling latest..."
        if [[ -n "$INSTALL_ROOT" ]]; then
            arch-chroot "$INSTALL_ROOT" sudo -u "$TARGET_USER" git -C "$TARGET_HOME/.dotfiles" pull || true
        else
            sudo -u "$TARGET_USER" git -C "$dotfiles_dir" pull || true
        fi
    else
        log "Cloning dotfiles from $DOTFILES_REPO..."
        if [[ -n "$INSTALL_ROOT" ]]; then
            arch-chroot "$INSTALL_ROOT" sudo -u "$TARGET_USER" git clone --depth 1 "$DOTFILES_REPO" "$TARGET_HOME/.dotfiles"
        else
            sudo -u "$TARGET_USER" git clone --depth 1 "$DOTFILES_REPO" "$dotfiles_dir"
        fi
        log_success "Dotfiles cloned"
    fi

    # Run stow to create symlinks
    log "Running stow to create symlinks..."
    if [[ -n "$INSTALL_ROOT" ]]; then
        arch-chroot "$INSTALL_ROOT" bash -c "cd $TARGET_HOME/.dotfiles && sudo -u $TARGET_USER stow . 2>/dev/null" || true
    else
        (cd "$dotfiles_dir" && sudo -u "$TARGET_USER" stow . 2>/dev/null) || true
    fi

    log_success "Dotfiles setup complete"
}

# =============================================================================
# Profile Application
# =============================================================================

apply_profile() {
    log_section "Applying Profile: $PXE_PROFILE"

    # Try to fetch and run profile-specific script from PXE server
    local profile_url="http://$PXE_SERVER:9080/kickstart/profiles/${PXE_PROFILE}.sh"

    log "Checking for profile script at $profile_url..."

    if curl -sLf "$profile_url" -o /tmp/profile-script.sh 2>/dev/null; then
        log "Running profile script..."
        chmod +x /tmp/profile-script.sh

        if [[ -n "$INSTALL_ROOT" ]]; then
            cp /tmp/profile-script.sh "${INSTALL_ROOT}/tmp/profile-script.sh"
            arch-chroot "$INSTALL_ROOT" bash /tmp/profile-script.sh
            rm -f "${INSTALL_ROOT}/tmp/profile-script.sh"
        else
            bash /tmp/profile-script.sh
        fi

        rm -f /tmp/profile-script.sh
        log_success "Profile script executed"
    else
        log "No profile script found, using defaults"
    fi

    # Set the startup profile if profile-switch exists
    local profile_switch="${INSTALL_ROOT}${TARGET_HOME}/.local/bin/profile-switch"
    if [[ -x "$profile_switch" ]]; then
        log "Setting startup profile to $PXE_PROFILE..."
        if [[ -n "$INSTALL_ROOT" ]]; then
            arch-chroot "$INSTALL_ROOT" sudo -u "$TARGET_USER" "$TARGET_HOME/.local/bin/profile-switch" "$PXE_PROFILE" 2>/dev/null || true
        else
            sudo -u "$TARGET_USER" "$profile_switch" "$PXE_PROFILE" 2>/dev/null || true
        fi
    fi

    log_success "Profile applied"
}

# =============================================================================
# Service Configuration
# =============================================================================

enable_services() {
    log_section "Enabling Services"

    local services=(
        "NetworkManager"
        "sshd"
    )

    # Profile-specific services
    case "$PXE_PROFILE" in
        desktop)
            services+=("bluetooth" "cups")
            ;;
        headless)
            services+=("docker")
            ;;
    esac

    for service in "${services[@]}"; do
        log "Enabling $service..."
        if [[ -n "$INSTALL_ROOT" ]]; then
            arch-chroot "$INSTALL_ROOT" systemctl enable "$service" 2>/dev/null || \
                log_warning "$service not available"
        else
            systemctl enable "$service" 2>/dev/null || \
                log_warning "$service not available"
        fi
    done

    # Enable user services if applicable
    if [[ "$PXE_PROFILE" != "headless" ]]; then
        log "Setting up user services..."
        # These will be enabled on first login
    fi

    log_success "Services enabled"
}

# =============================================================================
# Cleanup & Finalization
# =============================================================================

finalize() {
    log_section "Finalizing Installation"

    # Remove passwordless sudo (require password after setup)
    log "Removing passwordless sudo..."
    rm -f "${INSTALL_ROOT}/etc/sudoers.d/wheel-nopasswd"

    # Standard sudoers for wheel
    echo "%wheel ALL=(ALL:ALL) ALL" > "${INSTALL_ROOT}/etc/sudoers.d/wheel"
    chmod 440 "${INSTALL_ROOT}/etc/sudoers.d/wheel"

    # Generate machine-id if needed
    if [[ ! -s "${INSTALL_ROOT}/etc/machine-id" ]]; then
        log "Generating machine-id..."
        if [[ -n "$INSTALL_ROOT" ]]; then
            arch-chroot "$INSTALL_ROOT" systemd-machine-id-setup
        else
            systemd-machine-id-setup
        fi
    fi

    # Update package database
    log "Updating package database..."
    if [[ -n "$INSTALL_ROOT" ]]; then
        arch-chroot "$INSTALL_ROOT" pacman -Sy --noconfirm 2>/dev/null || true
    else
        pacman -Sy --noconfirm 2>/dev/null || true
    fi

    log_success "Finalization complete"
}

# =============================================================================
# Main
# =============================================================================

main() {
    log_section "PXE Auto-Provisioning"

    # Skip if not auto-install mode
    if [[ "$PXE_AUTOINSTALL" != "1" ]]; then
        log "Auto-install not enabled (pxe_autoinstall=$PXE_AUTOINSTALL)"
        log "Run this script manually after installation if needed."
        return 0
    fi

    preflight_check
    setup_user
    setup_dotfiles
    apply_profile
    enable_services
    finalize

    log_section "Provisioning Complete!"
    echo ""
    log "The system is ready."
    log "Default password: changeme (PLEASE CHANGE ON FIRST LOGIN)"
    echo ""
    log "You can now reboot into your configured system."
    echo ""
}

main "$@"
