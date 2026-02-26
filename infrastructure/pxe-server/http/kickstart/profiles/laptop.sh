#!/usr/bin/env bash
#
# PXE Profile: Laptop
#
# Optimized for laptops with battery management, no Sunshine streaming,
# and power-efficient settings.
#

set -euo pipefail

echo "[Profile: Laptop] Applying laptop-specific configuration..."

# Additional packages for laptop
LAPTOP_PACKAGES=(
    # Window manager & display
    hyprland
    xdg-desktop-portal-hyprland
    waybar
    wofi
    dunst

    # Terminal & shell
    kitty
    zsh
    starship

    # Development
    neovim
    git
    docker
    docker-compose

    # Power management
    tlp
    powertop

    # Utilities
    btop
    lazygit
    ripgrep
    fd
    fzf
    brightnessctl

    # Audio/Video
    pipewire
    pipewire-pulse
    wireplumber

    # Fonts
    ttf-jetbrains-mono-nerd
    noto-fonts
    noto-fonts-emoji
)

# Install packages if pacman is available
if command -v pacman &>/dev/null; then
    echo "[Profile: Laptop] Installing laptop packages..."
    pacman -S --needed --noconfirm "${LAPTOP_PACKAGES[@]}" 2>/dev/null || true
fi

# Enable laptop-specific services
echo "[Profile: Laptop] Enabling services..."
systemctl enable bluetooth 2>/dev/null || true
systemctl enable tlp 2>/dev/null || true
systemctl enable docker 2>/dev/null || true

# Disable services not needed on laptop
systemctl disable cups 2>/dev/null || true  # Enable manually if needed

# Add user to required groups
if id kblack0610 &>/dev/null; then
    usermod -aG docker,video,audio,input kblack0610 2>/dev/null || true
fi

# Laptop-specific power settings
echo "[Profile: Laptop] Configuring power management..."
if [[ -d /etc/tlp.d ]]; then
    cat > /etc/tlp.d/99-custom.conf <<'EOF'
# Custom TLP settings for laptop
CPU_SCALING_GOVERNOR_ON_AC=performance
CPU_SCALING_GOVERNOR_ON_BAT=powersave
CPU_ENERGY_PERF_POLICY_ON_AC=performance
CPU_ENERGY_PERF_POLICY_ON_BAT=power
EOF
fi

echo "[Profile: Laptop] Configuration complete"
