#!/usr/bin/env bash
#
# PXE Profile: Cluster Node
#
# Minimal setup for a k3s worker node: SSH, networking and the storage clients.
# Ansible joins it to the cluster afterwards.
# No GUI, no dev tools beyond essentials.
#

set -euo pipefail

echo "[Profile: Cluster] Applying cluster node configuration..."

# Minimal packages for a k3s worker
CLUSTER_PACKAGES=(
    # Shell
    zsh

    # Essentials
    neovim
    git
    curl
    wget
    openssh
    jq

    # Monitoring
    btop
    htop

    # Networking
    tailscale
    nfs-utils
    open-iscsi
)

# Install packages
if command -v pacman &>/dev/null; then
    echo "[Profile: Cluster] Installing cluster node packages..."
    pacman -S --needed --noconfirm "${CLUSTER_PACKAGES[@]}" 2>/dev/null || true
fi

# Enable services
echo "[Profile: Cluster] Enabling services..."
systemctl enable sshd 2>/dev/null || true
systemctl enable NetworkManager 2>/dev/null || true
systemctl enable tailscaled 2>/dev/null || true
systemctl enable iscsid 2>/dev/null || true

# Disable GUI
systemctl set-default multi-user.target 2>/dev/null || true

# SSH hardening
echo "[Profile: Cluster] Configuring SSH..."
if [[ -f /etc/ssh/sshd_config ]]; then
    sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
    sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
fi

# Add user to groups
if id kblack0610 &>/dev/null; then
    usermod -aG wheel kblack0610 2>/dev/null || true
fi

# k3s is not installed here. Ansible joins the node (roles/k3s-agent), at the
# one pinned version every node runs; an install here was unpinned and put
# hp-victus and asus-laptop a minor version ahead of the server.

echo "[Profile: Cluster] Configuration complete"
