#!/usr/bin/env bash
#
# PXE Profile: Desktop
#
# Full desktop environment with Hyprland, Sunshine streaming,
# development tools, and all creature comforts.
#

set -euo pipefail

echo "[Profile: Desktop] Applying desktop-specific configuration..."

# Additional packages for desktop
DESKTOP_PACKAGES=(
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

    # Streaming
    sunshine

    # Utilities
    btop
    lazygit
    ripgrep
    fd
    fzf

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
    echo "[Profile: Desktop] Installing desktop packages..."
    pacman -S --needed --noconfirm "${DESKTOP_PACKAGES[@]}" 2>/dev/null || true
fi

# Enable desktop-specific services
echo "[Profile: Desktop] Enabling services..."
systemctl enable bluetooth 2>/dev/null || true
systemctl enable cups 2>/dev/null || true
systemctl enable docker 2>/dev/null || true

# Add user to required groups
if id kblack0610 &>/dev/null; then
    usermod -aG docker,video,audio,input kblack0610 2>/dev/null || true
fi

echo "[Profile: Desktop] Configuration complete"
