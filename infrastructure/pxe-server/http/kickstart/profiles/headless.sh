#!/usr/bin/env bash
#
# PXE Profile: Headless
#
# Minimal server/headless configuration with SSH, Docker,
# and essential utilities. No GUI.
#

set -euo pipefail

echo "[Profile: Headless] Applying headless-specific configuration..."

# Packages for headless server
HEADLESS_PACKAGES=(
    # Shell & terminal
    zsh
    tmux

    # Development & tools
    neovim
    git
    docker
    docker-compose

    # Utilities
    btop
    ripgrep
    fd
    fzf
    jq
    yq

    # Networking
    curl
    wget
    openssh
    tailscale

    # System
    htop
    ncdu
)

# Install packages if pacman is available
if command -v pacman &>/dev/null; then
    echo "[Profile: Headless] Installing headless packages..."
    pacman -S --needed --noconfirm "${HEADLESS_PACKAGES[@]}" 2>/dev/null || true
fi

# Enable headless-specific services
echo "[Profile: Headless] Enabling services..."
systemctl enable sshd 2>/dev/null || true
systemctl enable docker 2>/dev/null || true
systemctl enable tailscaled 2>/dev/null || true

# Disable GUI-related services
systemctl disable bluetooth 2>/dev/null || true
systemctl disable cups 2>/dev/null || true

# Add user to required groups
if id kblack0610 &>/dev/null; then
    usermod -aG docker kblack0610 2>/dev/null || true
fi

# SSH hardening
echo "[Profile: Headless] Configuring SSH..."
if [[ -f /etc/ssh/sshd_config ]]; then
    # Disable password auth (require keys)
    sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
    sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
fi

# Set default target to multi-user (no GUI)
systemctl set-default multi-user.target 2>/dev/null || true

echo "[Profile: Headless] Configuration complete"
echo "[Profile: Headless] NOTE: Password auth disabled - ensure SSH keys are set up"
