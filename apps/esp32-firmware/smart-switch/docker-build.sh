#!/bin/bash

# ESP32 Smart Switch Docker Build Script
# Usage: ./docker-build.sh [build|flash|shell]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}ERROR: .env file not found!${NC}"
    echo "Create one with: cp .env.example .env"
    exit 1
fi

# Load environment variables
export $(grep -v '^#' .env | xargs)

# Build Docker image if it doesn't exist
build_image() {
    echo -e "${YELLOW}Building Docker image...${NC}"
    docker-compose build
    echo -e "${GREEN}Docker image built successfully!${NC}"
}

# Run cargo build inside container
build_firmware() {
    echo -e "${YELLOW}Building firmware...${NC}"
    echo "  WiFi SSID: $WIFI_SSID"
    echo "  MQTT Broker: $MQTT_BROKER"

    docker-compose run --rm esp32-dev \
        cargo build --release

    echo -e "${GREEN}Build complete!${NC}"
    echo "Binary: target/xtensa-esp32-none-elf/release/smart-switch"
}

# Flash firmware (requires USB passthrough)
flash_firmware() {
    echo -e "${YELLOW}Flashing firmware...${NC}"

    # Detect serial port
    if [ -e /dev/ttyUSB0 ]; then
        SERIAL_PORT="/dev/ttyUSB0"
    elif [ -e /dev/ttyACM0 ]; then
        SERIAL_PORT="/dev/ttyACM0"
    else
        echo -e "${RED}ERROR: No serial device found!${NC}"
        echo "Connect your ESP32 and try again."
        exit 1
    fi

    echo "Using serial port: $SERIAL_PORT"

    # First build
    build_firmware

    # Flash using host espflash (easier than USB passthrough)
    echo -e "${YELLOW}Flashing to device...${NC}"
    if command -v espflash &> /dev/null; then
        espflash flash target/xtensa-esp32-none-elf/release/smart-switch --monitor
    else
        echo -e "${RED}espflash not found on host!${NC}"
        echo "Install with: cargo install espflash"
        echo "Or flash manually: espflash flash target/xtensa-esp32-none-elf/release/smart-switch"
        exit 1
    fi
}

# Open interactive shell in container
open_shell() {
    echo -e "${YELLOW}Opening shell in container...${NC}"
    docker-compose run --rm esp32-dev /bin/bash
}

# Show help
show_help() {
    echo "ESP32 Smart Switch Docker Build Script"
    echo ""
    echo "Usage: ./docker-build.sh [command]"
    echo ""
    echo "Commands:"
    echo "  build    Build the firmware (default)"
    echo "  flash    Build and flash to connected ESP32"
    echo "  shell    Open interactive shell in container"
    echo "  image    Build/rebuild Docker image"
    echo "  help     Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./docker-build.sh build   # Just compile"
    echo "  ./docker-build.sh flash   # Compile and flash"
    echo "  ./docker-build.sh shell   # Debug environment"
}

# Main
case "${1:-build}" in
    build)
        build_image
        build_firmware
        ;;
    flash)
        build_image
        flash_firmware
        ;;
    shell)
        build_image
        open_shell
        ;;
    image)
        build_image
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        show_help
        exit 1
        ;;
esac
