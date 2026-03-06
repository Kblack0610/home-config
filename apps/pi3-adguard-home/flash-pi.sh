#!/bin/bash
# Flash and provision a Raspberry Pi SD card for AdGuard Home
# Usage: sudo ./flash-pi.sh /dev/sdX [hostname]
#
# This script:
# 1. Downloads Raspberry Pi OS Lite (if not cached)
# 2. Flashes it to the SD card
# 3. Copies custom.toml for first-boot config (user, SSH key, hostname)
# 4. Copies the AdGuard Home setup files
#
# After booting, SSH in and run:
#   cd ~/adguard-home && sudo ./setup.sh && docker compose up -d

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE_DIR="${SCRIPT_DIR}/.cache"
HOSTNAME="${2:-pi3-adguard}"

# Raspberry Pi OS Lite (64-bit) — update URL as needed
IMAGE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2024-11-19/2024-11-19-raspios-bookworm-arm64-lite.img.xz"
IMAGE_FILE="${CACHE_DIR}/raspios-lite-arm64.img.xz"
IMAGE_RAW="${CACHE_DIR}/raspios-lite-arm64.img"

usage() {
    echo "Usage: sudo $0 /dev/sdX [hostname]"
    echo ""
    echo "  /dev/sdX    Target SD card device (NOT a partition like /dev/sdX1)"
    echo "  hostname    Optional hostname (default: pi3-adguard)"
    echo ""
    echo "WARNING: This will ERASE the target device!"
    exit 1
}

# Check args
[[ $# -lt 1 ]] && usage
DEVICE="$1"

# Safety checks
if [[ $EUID -ne 0 ]]; then
    echo "Error: Must run as root (sudo)"
    exit 1
fi

if [[ ! -b "$DEVICE" ]]; then
    echo "Error: $DEVICE is not a block device"
    exit 1
fi

if [[ "$DEVICE" == *"nvme"* ]] || [[ "$DEVICE" == *"sda"* ]]; then
    echo "Error: Refusing to flash $DEVICE — looks like a system drive!"
    exit 1
fi

# Confirm
echo "=== Raspberry Pi SD Card Flasher ==="
echo ""
echo "  Target:   $DEVICE"
echo "  Hostname: $HOSTNAME"
echo "  User:     kblack0610 (SSH key pre-configured)"
echo ""
lsblk "$DEVICE" 2>/dev/null || true
echo ""
read -p "This will ERASE $DEVICE. Continue? [y/N] " -n 1 -r
echo
[[ ! $REPLY =~ ^[Yy]$ ]] && exit 0

# Unmount any mounted partitions
echo "[1/5] Unmounting partitions..."
for part in "${DEVICE}"*; do
    umount "$part" 2>/dev/null || true
done

# Download image if needed
mkdir -p "$CACHE_DIR"
if [[ ! -f "$IMAGE_RAW" ]]; then
    if [[ ! -f "$IMAGE_FILE" ]]; then
        echo "[2/5] Downloading Raspberry Pi OS Lite..."
        curl -L -o "$IMAGE_FILE" "$IMAGE_URL"
    fi
    echo "[2/5] Extracting image..."
    xz -dk "$IMAGE_FILE"
    mv "${IMAGE_FILE%.xz}" "$IMAGE_RAW" 2>/dev/null || true
else
    echo "[2/5] Using cached image."
fi

# Flash
echo "[3/5] Flashing to $DEVICE..."
dd if="$IMAGE_RAW" of="$DEVICE" bs=4M status=progress conv=fsync
sync

# Wait for partitions to appear
sleep 2
partprobe "$DEVICE" 2>/dev/null || true
sleep 2

# Determine partition naming (sdb1 vs sdb-part1)
if [[ -b "${DEVICE}1" ]]; then
    BOOT_PART="${DEVICE}1"
    ROOT_PART="${DEVICE}2"
elif [[ -b "${DEVICE}p1" ]]; then
    BOOT_PART="${DEVICE}p1"
    ROOT_PART="${DEVICE}p2"
else
    echo "Error: Cannot find partitions on $DEVICE"
    exit 1
fi

# Mount and configure boot partition
echo "[4/5] Configuring boot partition..."
BOOT_MNT=$(mktemp -d)
mount "$BOOT_PART" "$BOOT_MNT"

# Copy custom.toml with hostname override
sed "s/hostname = .*/hostname = \"${HOSTNAME}\"/" "$SCRIPT_DIR/custom.toml" > "$BOOT_MNT/custom.toml"

# Ensure SSH is enabled (belt and suspenders)
touch "$BOOT_MNT/ssh"

umount "$BOOT_MNT"
rmdir "$BOOT_MNT"

# Mount and configure root partition
echo "[5/5] Copying AdGuard Home files..."
ROOT_MNT=$(mktemp -d)
mount "$ROOT_PART" "$ROOT_MNT"

# Pre-create the adguard-home directory
DEPLOY_DIR="$ROOT_MNT/home/kblack0610/adguard-home"
mkdir -p "$DEPLOY_DIR"
cp "$SCRIPT_DIR/docker-compose.yml" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/setup.sh" "$DEPLOY_DIR/"
chmod +x "$DEPLOY_DIR/setup.sh"

# Set ownership (UID 1000 = first user created by custom.toml)
chown -R 1000:1000 "$ROOT_MNT/home/kblack0610"

umount "$ROOT_MNT"
rmdir "$ROOT_MNT"

echo ""
echo "=== Done! ==="
echo ""
echo "Insert the SD card into the Pi and boot it."
echo "After ~60 seconds, SSH in:"
echo "  ssh kblack0610@${HOSTNAME}.local"
echo ""
echo "Then start AdGuard Home:"
echo "  cd ~/adguard-home"
echo "  sudo ./setup.sh"
echo "  docker compose up -d"
