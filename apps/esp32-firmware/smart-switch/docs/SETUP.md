# ESP32 Smart Switch - Complete Setup Guide

This guide walks you through setting up a new ESP32 device with the smart switch firmware from scratch.

## Table of Contents

1. [Hardware Requirements](#hardware-requirements)
2. [Development Environment Setup](#development-environment-setup)
3. [Hardware Wiring](#hardware-wiring)
4. [Configuration](#configuration)
5. [Building and Flashing](#building-and-flashing)
6. [Docker Development](#docker-development)
7. [Home Assistant Integration](#home-assistant-integration)
8. [Adding Multiple Switches](#adding-multiple-switches)
9. [Troubleshooting](#troubleshooting)

---

## Hardware Requirements

### Required Components

| Component | Description | Example |
|-----------|-------------|---------|
| ESP32 Board | Any ESP32 development board | ESP32-WROOM-32, ESP32-DevKitC |
| Relay Module | 5V or 3.3V relay module | SRD-05VDC-SL-C |
| Power Supply | 5V USB or dedicated PSU | USB cable or 5V/2A adapter |
| USB Cable | For programming | Micro-USB or USB-C (depends on board) |

### Optional Components

| Component | Description | Purpose |
|-----------|-------------|---------|
| External LED | 3mm or 5mm LED + 330Ω resistor | Status indicator |
| Momentary Button | Push button | Manual override |
| Enclosure | Project box | Protection |
| Terminal Block | Screw terminals | Easy wire connections |

### Recommended ESP32 Boards

1. **ESP32-DevKitC** - Best for development, has USB-to-serial built-in
2. **ESP32-WROOM-32** - Common and affordable
3. **NodeMCU-32S** - Good pin labeling, beginner friendly
4. **ESP32-S3** - Newer, more capable (requires config changes)

---

## Development Environment Setup

### Step 1: Install Rust

```bash
# Install Rust via rustup
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Follow the prompts, then reload your shell
source ~/.bashrc  # or source ~/.zshrc
```

### Step 2: Install ESP32 Toolchain

```bash
# Install espup (ESP Rust toolchain manager)
cargo install espup

# Install the ESP32 toolchain (this takes a few minutes)
espup install

# Install flashing tool
cargo install espflash

# Source the ESP environment (add to ~/.bashrc for persistence)
source ~/export-esp.sh
```

### Step 3: Verify Installation

```bash
# Check Rust version
rustc --version
# Should show: rustc 1.88.0 or higher

# Check espflash
espflash --version
# Should show: espflash 3.x.x
```

### Alternative: Docker-Based Development

If you prefer not to install the ESP32 toolchain globally, use Docker for a reproducible environment:

```bash
# 1. Install Docker and Docker Compose
# https://docs.docker.com/get-docker/

# 2. Configure your settings
cp .env.example .env
# Edit .env with your WiFi and MQTT settings

# 3. Build using Docker
./docker-build.sh build
```

See [Docker Development](#docker-development) section below for full details.

### Step 4: Clone/Navigate to Project

```bash
# If you have the home-config repo
cd ~/dev/home/home-config/apps/esp32-firmware/smart-switch

# Or clone just this project (if separated)
# git clone <repo-url>
# cd smart-switch
```

---

## Hardware Wiring

### Basic Wiring Diagram

```
                    ESP32-DevKitC
                   ┌─────────────┐
                   │             │
    Button ───────►│ GPIO0       │
    (to GND)       │             │
                   │ GPIO2  ─────┼──────► Relay IN
                   │             │
    Status LED ◄───│ GPIO4       │
    (via 330Ω)     │             │
                   │ 3.3V   ─────┼──────► Relay VCC (if 3.3V relay)
                   │             │
                   │ 5V     ─────┼──────► Relay VCC (if 5V relay)
                   │             │
                   │ GND    ─────┼──────► Relay GND, LED GND, Button
                   │             │
                   └─────────────┘
```

### Detailed Pin Connections

#### Relay Module (5V typical)

| Relay Pin | ESP32 Pin | Notes |
|-----------|-----------|-------|
| VCC | 5V (or 3.3V for logic-level relays) | Check your relay specs |
| GND | GND | Common ground |
| IN | GPIO2 | Control signal |

#### Status LED (Optional)

| LED Pin | Connection |
|---------|------------|
| Anode (+) | GPIO4 via 330Ω resistor |
| Cathode (-) | GND |

#### External Button (Optional)

| Button Pin | Connection |
|------------|------------|
| One leg | GPIO0 |
| Other leg | GND |

> **Note**: GPIO0 has an internal pull-up. The button connects to GND when pressed.

### Safety Notes for AC Switching

If connecting to mains AC voltage:

1. **Use a proper relay rated for your voltage/current**
2. **Ensure proper isolation** - Never touch relay contacts when powered
3. **Use an enclosure** - Protect all connections
4. **Consider using a pre-built smart relay module** - Much safer for beginners
5. **Local electrical codes apply** - Consult an electrician if unsure

---

## Configuration

### Step 1: Create Environment File

```bash
cd ~/dev/home/home-config/apps/esp32-firmware/smart-switch
cp .env.example .env
```

### Step 2: Edit Configuration

Edit `.env` with your network settings:

```bash
# WiFi Configuration
WIFI_SSID=YourWiFiNetworkName
WIFI_PASSWORD=YourWiFiPassword

# MQTT Broker Configuration (IP address of your MQTT server)
MQTT_BROKER=192.168.1.100
```

### Step 3: Customize Device Settings (Optional)

Edit `src/config.rs` for device-specific settings:

```rust
// Change these for each device
pub const MQTT_CLIENT_ID: &str = "esp32-smart-switch-01";  // Unique per device
pub const DEVICE_ID: &str = "esp32_switch_livingroom";      // Unique per device
pub const DEVICE_NAME: &str = "Living Room Switch";         // Display name in HA

// Change MQTT topics per device
pub const STATE_TOPIC: &str = "home/switch/livingroom/state";
pub const COMMAND_TOPIC: &str = "home/switch/livingroom/set";
pub const AVAILABILITY_TOPIC: &str = "home/switch/livingroom/available";
```

---

## Building and Flashing

### Step 1: Connect ESP32

1. Connect ESP32 to computer via USB
2. Identify the serial port:

```bash
# Linux
ls /dev/ttyUSB* /dev/ttyACM*

# macOS
ls /dev/cu.usbserial* /dev/cu.SLAB*
```

### Step 2: Build and Flash

```bash
# Source ESP environment (if not in ~/.bashrc)
source ~/export-esp.sh

# Build and flash with serial monitor
./build.sh flash
```

### Step 3: Monitor Output

Watch the serial output for connection status:

```
Smart Switch starting...
WiFi task: connecting to 'YourSSID'
WiFi connected!
Waiting for IP...
Got IP: 192.168.1.xxx
MQTT connected successfully!
Published Home Assistant discovery config
```

### Alternative: Manual Commands

```bash
# Just build (no flash)
cargo build --release

# Flash manually
espflash flash target/xtensa-esp32-none-elf/release/smart-switch

# Monitor only
espflash monitor
```

---

## Docker Development

Docker provides a reproducible development environment without installing the ESP32 toolchain on your host system.

### Prerequisites

- Docker Engine 20.10+
- Docker Compose v2
- USB access for flashing (Linux: add user to `dialout` group)

### Quick Start with Docker

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your WiFi/MQTT settings

# 2. Build firmware
./docker-build.sh build

# 3. Flash to ESP32 (requires espflash on host)
./docker-build.sh flash
```

### Docker Commands

| Command | Description |
|---------|-------------|
| `./docker-build.sh build` | Build Docker image and compile firmware |
| `./docker-build.sh flash` | Build and flash to connected ESP32 |
| `./docker-build.sh shell` | Open interactive shell in container |
| `./docker-build.sh image` | Build/rebuild Docker image only |
| `./docker-build.sh help` | Show help message |

### VS Code Dev Container

For the best development experience, use VS Code with the Dev Containers extension:

1. Install [VS Code](https://code.visualstudio.com/)
2. Install the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
3. Open this project folder in VS Code
4. Click "Reopen in Container" when prompted (or use Command Palette: `Dev Containers: Reopen in Container`)

The container includes:
- Pre-configured ESP32 Rust toolchain
- rust-analyzer with ESP32 target
- espflash for firmware deployment
- All necessary development tools

### Docker Files Overview

```
smart-switch/
├── Dockerfile              # ESP32 Rust development image
├── docker-compose.yml      # Container configuration
├── docker-build.sh         # Build helper script
└── .devcontainer/
    └── devcontainer.json   # VS Code Dev Container config
```

### Flashing from Docker

The Docker container builds the firmware, but flashing requires USB access. Two options:

**Option 1: Flash from host (recommended)**
```bash
# Build in Docker, flash from host
./docker-build.sh build

# Install espflash on host if needed
cargo install espflash

# Flash manually
espflash flash target/xtensa-esp32-none-elf/release/smart-switch --monitor
```

**Option 2: USB passthrough (Linux only)**

Edit `docker-compose.yml` to enable device access:
```yaml
services:
  esp32-dev:
    # ... existing config ...
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
    privileged: true
```

Then flash from inside the container:
```bash
./docker-build.sh shell
# Inside container:
espflash flash target/xtensa-esp32-none-elf/release/smart-switch --monitor
```

### Troubleshooting Docker

**Docker build fails**
```bash
# Rebuild image from scratch
docker-compose build --no-cache
```

**Permission denied on serial port**
```bash
# Add user to dialout group (Linux)
sudo usermod -a -G dialout $USER
# Log out and back in
```

**Volume mount issues**
```bash
# Clean up Docker volumes
docker-compose down -v
docker volume prune
```

---

## Home Assistant Integration

### Prerequisites

- Home Assistant with MQTT integration configured
- Mosquitto or other MQTT broker running

### Automatic Discovery

The switch automatically registers with Home Assistant via MQTT Discovery. After flashing:

1. Go to **Settings > Devices & Services > MQTT**
2. The device should appear as "Living Room Switch"
3. Click on it to see the switch entity

### Manual YAML Configuration (Alternative)

If auto-discovery doesn't work, add to `configuration.yaml`:

```yaml
mqtt:
  switch:
    - name: "Living Room Switch"
      state_topic: "home/switch/livingroom/state"
      command_topic: "home/switch/livingroom/set"
      availability_topic: "home/switch/livingroom/available"
      payload_on: "ON"
      payload_off: "OFF"
      unique_id: "esp32_switch_livingroom"
```

### Testing via MQTT

```bash
# Subscribe to state (in terminal 1)
mosquitto_sub -h YOUR_BROKER_IP -t "home/switch/livingroom/state"

# Send command (in terminal 2)
mosquitto_pub -h YOUR_BROKER_IP -t "home/switch/livingroom/set" -m "ON"
mosquitto_pub -h YOUR_BROKER_IP -t "home/switch/livingroom/set" -m "OFF"
mosquitto_pub -h YOUR_BROKER_IP -t "home/switch/livingroom/set" -m "TOGGLE"
```

---

## Adding Multiple Switches

For each additional ESP32 switch, you need unique identifiers.

### Option 1: Separate Config Files

Create device-specific config branches or copies:

```bash
# Copy project for new device
cp -r smart-switch smart-switch-bedroom

# Edit smart-switch-bedroom/src/config.rs with unique values
```

### Option 2: Environment Variables

Modify `src/config.rs` to use more environment variables:

```rust
pub const MQTT_CLIENT_ID: &str = env!("MQTT_CLIENT_ID");
pub const DEVICE_ID: &str = env!("DEVICE_ID");
pub const DEVICE_NAME: &str = env!("DEVICE_NAME");
pub const STATE_TOPIC: &str = env!("MQTT_STATE_TOPIC");
pub const COMMAND_TOPIC: &str = env!("MQTT_COMMAND_TOPIC");
pub const AVAILABILITY_TOPIC: &str = env!("MQTT_AVAILABILITY_TOPIC");
```

Then create `.env` files per device:

```bash
# .env.livingroom
WIFI_SSID=MyWiFi
WIFI_PASSWORD=secret
MQTT_BROKER=192.168.1.100
MQTT_CLIENT_ID=esp32-switch-livingroom
DEVICE_ID=esp32_switch_livingroom
DEVICE_NAME=Living Room Switch
MQTT_STATE_TOPIC=home/switch/livingroom/state
MQTT_COMMAND_TOPIC=home/switch/livingroom/set
MQTT_AVAILABILITY_TOPIC=home/switch/livingroom/available
```

### Recommended Naming Convention

| Location | DEVICE_ID | CLIENT_ID | Topics |
|----------|-----------|-----------|--------|
| Living Room | esp32_switch_livingroom | esp32-switch-01 | home/switch/livingroom/* |
| Bedroom | esp32_switch_bedroom | esp32-switch-02 | home/switch/bedroom/* |
| Kitchen | esp32_switch_kitchen | esp32-switch-03 | home/switch/kitchen/* |
| Garage | esp32_switch_garage | esp32-switch-04 | home/switch/garage/* |

---

## Troubleshooting

### Build Errors

**Error: "toolchain not found"**
```bash
source ~/export-esp.sh
```

**Error: "WIFI_SSID not set"**
```bash
# Create .env file
cp .env.example .env
# Edit with your settings
```

### Connection Issues

**WiFi won't connect**
- Check SSID spelling (case-sensitive)
- Verify password
- Ensure 2.4GHz network (ESP32 doesn't support 5GHz)
- Check router allows new devices

**MQTT fails to connect**
- Verify broker IP address
- Check broker is running: `mosquitto -v`
- Ensure no firewall blocking port 1883
- Try connecting with `mosquitto_pub` from same network

**Home Assistant doesn't see device**
- Check MQTT integration is configured in HA
- Verify HA is connected to same MQTT broker
- Check HA logs for MQTT errors
- Try restarting Home Assistant

### Hardware Issues

**Relay doesn't switch**
- Check wiring (VCC, GND, IN)
- Verify GPIO2 is outputting (LED on GPIO2 should light)
- Test relay with 3.3V directly to IN pin
- Some relays are active-LOW (inverted logic)

**Button doesn't work**
- GPIO0 is used for boot mode - hold button while uploading
- Verify button connects GPIO0 to GND when pressed
- Check serial output for "Button pressed!" message

**Status LED not blinking**
- Check LED polarity (long leg = positive)
- Verify 330Ω resistor in series
- Test LED with 3.3V directly

### Serial Monitor Issues

**No output in monitor**
- Check USB cable (some are charge-only)
- Try different USB port
- Verify correct serial port selected
- Reset ESP32 with EN button

---

## Quick Reference Card

```
┌──────────────────────────────────────────────────────────┐
│                  ESP32 Smart Switch                       │
├──────────────────────────────────────────────────────────┤
│  STATUS LED PATTERNS:                                     │
│    ○●○●○● Fast blink  = WiFi connecting                  │
│    ○──●──○ Slow blink = MQTT connecting                  │
│    ●────── Solid on   = Fully connected                  │
├──────────────────────────────────────────────────────────┤
│  MQTT COMMANDS:                                           │
│    ON     - Turn switch on                               │
│    OFF    - Turn switch off                              │
│    TOGGLE - Toggle current state                         │
├──────────────────────────────────────────────────────────┤
│  GPIO PINS:                                               │
│    GPIO0  - Button input (active low)                    │
│    GPIO2  - Relay output                                 │
│    GPIO4  - Status LED                                   │
├──────────────────────────────────────────────────────────┤
│  BUILD COMMANDS (native):                                 │
│    ./build.sh         - Build only                       │
│    ./build.sh flash   - Build and flash                  │
│    ./build.sh monitor - Serial monitor                   │
├──────────────────────────────────────────────────────────┤
│  BUILD COMMANDS (Docker):                                 │
│    ./docker-build.sh build  - Build in container         │
│    ./docker-build.sh flash  - Build and flash            │
│    ./docker-build.sh shell  - Interactive shell          │
└──────────────────────────────────────────────────────────┘
```
