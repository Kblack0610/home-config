#!/usr/bin/env bash
set -euo pipefail

# Deploy pi-monitor to Raspberry Pi devices
# Usage: ./deploy-pi.sh <pi-hostname-or-ip> [--install]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FLEET_DIR="$(dirname "$SCRIPT_DIR")"
PI_MONITOR_DIR="$FLEET_DIR/agents/pi-monitor"

usage() {
    echo "Usage: $0 <host> [options]"
    echo ""
    echo "Options:"
    echo "  --install    Install systemd service and enable on boot"
    echo "  --arm6       Target ARMv6 (Pi Zero v1) instead of default ARM64"
    echo ""
    echo "Examples:"
    echo "  $0 192.168.1.80              # Build and deploy binary"
    echo "  $0 pi-zero-1 --install       # Deploy + install systemd service"
    echo "  $0 192.168.1.80 --arm6       # Build for Pi Zero v1 (32-bit)"
    echo ""
    exit 1
}

[[ $# -lt 1 ]] && usage

HOST="$1"
shift

INSTALL=false
TARGET="aarch64-unknown-linux-gnu"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install) INSTALL=true ;;
        --arm6) TARGET="arm-unknown-linux-gnueabihf" ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
    shift
done

BINARY_NAME="pi-monitor"

echo "Cross-compiling $BINARY_NAME for $TARGET..."
cd "$PI_MONITOR_DIR"
cross build --release --target "$TARGET" 2>&1

BINARY="$PI_MONITOR_DIR/target/$TARGET/release/$BINARY_NAME"
if [[ ! -f "$BINARY" ]]; then
    echo "Error: binary not found at $BINARY"
    echo "Make sure 'cross' is installed: cargo install cross"
    exit 1
fi

echo "Deploying to $HOST..."
scp "$BINARY" "$HOST:/tmp/$BINARY_NAME"
ssh "$HOST" "sudo mv /tmp/$BINARY_NAME /usr/local/bin/$BINARY_NAME && sudo chmod +x /usr/local/bin/$BINARY_NAME"

if $INSTALL; then
    echo "Installing systemd service..."
    scp "$PI_MONITOR_DIR/pi-monitor.service" "$HOST:/tmp/pi-monitor.service"
    ssh "$HOST" bash -s <<'REMOTE'
        sudo mv /tmp/pi-monitor.service /etc/systemd/system/pi-monitor.service
        sudo useradd --system --no-create-home pi-monitor 2>/dev/null || true
        sudo systemctl daemon-reload
        sudo systemctl enable pi-monitor
        sudo systemctl restart pi-monitor
        echo "Service status:"
        sudo systemctl status pi-monitor --no-pager
REMOTE
else
    echo "Restarting service (if already installed)..."
    ssh "$HOST" "sudo systemctl restart pi-monitor 2>/dev/null || echo 'Service not installed. Use --install to set up systemd service.'"
fi

echo "Done! Test with: curl http://$HOST:9200/metrics"
