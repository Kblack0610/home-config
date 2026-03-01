#!/usr/bin/env bash
#
# PXE Profile: Cluster Node
#
# Minimal setup for k3s worker node. SSH, networking, and k3s agent.
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

# Install k3s agent
# Reads K3S_URL and K3S_TOKEN from environment or kernel cmdline
K3S_URL="${K3S_URL:-https://192.168.1.20:6443}"
K3S_TOKEN="${K3S_TOKEN:-}"

# Try to get token from kernel cmdline if not set
if [[ -z "$K3S_TOKEN" ]] && [[ -f /proc/cmdline ]]; then
    K3S_TOKEN=$(tr ' ' '\n' < /proc/cmdline | grep "^k3s_token=" | cut -d= -f2 | head -1)
fi

if [[ -n "$K3S_TOKEN" ]]; then
    echo "[Profile: Cluster] Installing k3s agent..."
    curl -sfL https://get.k3s.io | K3S_URL="$K3S_URL" K3S_TOKEN="$K3S_TOKEN" sh - || {
        echo "[Profile: Cluster] k3s install failed - will need manual setup"
        echo "[Profile: Cluster] Run: curl -sfL https://get.k3s.io | K3S_URL=$K3S_URL K3S_TOKEN=<token> sh -"
    }
else
    echo "[Profile: Cluster] No K3S_TOKEN provided - skipping k3s install"
    echo "[Profile: Cluster] After boot, run:"
    echo "  curl -sfL https://get.k3s.io | K3S_URL=$K3S_URL K3S_TOKEN=<token> sh -"
fi

echo "[Profile: Cluster] Configuration complete"
