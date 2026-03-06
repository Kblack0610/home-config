#!/usr/bin/env bash
set -euo pipefail

# IoT Fleet Firmware Flash Tool
# Usage: ./flash.sh <firmware-name> [--release] [--monitor]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FLEET_DIR="$(dirname "$SCRIPT_DIR")"

usage() {
    echo "Usage: $0 <firmware> [options]"
    echo ""
    echo "Firmware targets:"
    echo "  esp32-switch      ESP32 smart switch (Xtensa)"
    echo "  esp32c3-sensor    ESP32-C3 sensor (RISC-V)"
    echo ""
    echo "Options:"
    echo "  --release    Build in release mode"
    echo "  --monitor    Open serial monitor after flash"
    echo ""
    exit 1
}

[[ $# -lt 1 ]] && usage

FIRMWARE="$1"
shift

RELEASE=""
MONITOR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --release) RELEASE="--release" ;;
        --monitor) MONITOR="--monitor" ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
    shift
done

FIRMWARE_DIR="$FLEET_DIR/firmware/$FIRMWARE"
if [[ ! -d "$FIRMWARE_DIR" ]]; then
    echo "Error: firmware directory not found: $FIRMWARE_DIR"
    echo "Available firmware:"
    ls -1 "$FLEET_DIR/firmware/" 2>/dev/null || echo "  (none)"
    exit 1
fi

echo "Building $FIRMWARE..."
cd "$FIRMWARE_DIR"
cargo build $RELEASE 2>&1

echo "Flashing $FIRMWARE..."
# espflash is configured via .cargo/config.toml runner
cargo run $RELEASE 2>&1
