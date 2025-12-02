#!/bin/bash
# AdGuard Home Pi 3 Setup Script
# Run this on your Raspberry Pi 3 before starting docker-compose

set -e

echo "=== AdGuard Home Pi 3 Setup ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo ./setup.sh)"
  exit 1
fi

# Step 1: Disable systemd-resolved (it blocks port 53)
echo "[1/4] Checking for systemd-resolved..."
if systemctl is-active --quiet systemd-resolved; then
  echo "  -> Stopping and disabling systemd-resolved..."
  systemctl stop systemd-resolved
  systemctl disable systemd-resolved

  # Fix /etc/resolv.conf
  rm -f /etc/resolv.conf
  echo "nameserver 1.1.1.1" > /etc/resolv.conf
  echo "nameserver 8.8.8.8" >> /etc/resolv.conf
  echo "  -> Done. Using temporary DNS (1.1.1.1, 8.8.8.8)"
else
  echo "  -> systemd-resolved not running, skipping."
fi

# Step 2: Create data directories
echo "[2/4] Creating data directories..."
mkdir -p ./data/conf ./data/work
chmod 755 ./data ./data/conf ./data/work
echo "  -> Done."

# Step 3: Check if Docker is installed
echo "[3/4] Checking Docker..."
if ! command -v docker &> /dev/null; then
  echo "  -> Docker not found. Installing..."
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker $SUDO_USER
  echo "  -> Docker installed. You may need to log out and back in."
else
  echo "  -> Docker is installed."
fi

# Step 4: Check if docker-compose is available
echo "[4/4] Checking docker-compose..."
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
  echo "  -> Installing docker-compose plugin..."
  apt-get update && apt-get install -y docker-compose-plugin
else
  echo "  -> docker-compose is available."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Start AdGuard Home:"
echo "     docker compose up -d"
echo ""
echo "  2. Open the setup wizard:"
echo "     http://$(hostname -I | awk '{print $1}'):3000"
echo ""
echo "  3. Configure your router's DHCP to use this Pi's IP as DNS"
echo ""
