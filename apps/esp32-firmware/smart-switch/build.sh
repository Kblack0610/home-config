#!/bin/bash

# ESP32 Smart Switch Build Script
# Usage: ./build.sh [flash]

set -e

# Source ESP toolchain
if [ -f ~/export-esp.sh ]; then
    source ~/export-esp.sh
fi

# Load environment variables from .env if it exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Check required env vars
if [ -z "$WIFI_SSID" ] || [ -z "$WIFI_PASSWORD" ] || [ -z "$MQTT_BROKER" ]; then
    echo "ERROR: Missing required environment variables!"
    echo "Create a .env file with:"
    echo "  WIFI_SSID=your_ssid"
    echo "  WIFI_PASSWORD=your_password"
    echo "  MQTT_BROKER=192.168.1.100"
    exit 1
fi

echo "Building smart-switch for ESP32..."
echo "  WiFi SSID: $WIFI_SSID"
echo "  MQTT Broker: $MQTT_BROKER"

if [ "$1" == "flash" ]; then
    cargo build --release && espflash flash target/xtensa-esp32-none-elf/release/smart-switch --monitor
elif [ "$1" == "monitor" ]; then
    espflash monitor
else
    cargo build --release
fi
