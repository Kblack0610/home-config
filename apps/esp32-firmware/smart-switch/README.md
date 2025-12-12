# ESP32 Smart Switch

A Rust-based smart switch firmware for ESP32, designed as an alternative to ESPHome with full Home Assistant integration.

## Features

- **WiFi Connectivity** - Connects to configured WiFi network with auto-reconnect
- **MQTT Support** - Publishes state and receives commands via MQTT
- **Home Assistant Auto-Discovery** - Automatically registers with Home Assistant
- **Physical Button** - Toggle switch via GPIO0 button (with debouncing)
- **Relay Control** - Controls relay on GPIO2
- **Status LED** - Connection status indication on GPIO4
- **Last Will Testament** - Reports offline status on disconnection

## Quick Start

```bash
# 1. Install ESP32 toolchain (one-time setup)
cargo install espup espflash
espup install
source ~/export-esp.sh

# 2. Configure
cp .env.example .env
# Edit .env with your WiFi and MQTT settings

# 3. Build and flash
./build.sh flash
```

**For detailed setup instructions, see [docs/SETUP.md](docs/SETUP.md)**

## Status LED Patterns

| Pattern | Meaning |
|---------|---------|
| Fast blink (100ms) | WiFi connecting |
| Slow blink (500ms) | WiFi OK, MQTT connecting |
| Solid on | Fully connected |

## MQTT Interface

| Topic | Direction | Payload |
|-------|-----------|---------|
| `home/switch/livingroom/state` | Publish | `ON` / `OFF` |
| `home/switch/livingroom/set` | Subscribe | `ON` / `OFF` / `TOGGLE` |
| `home/switch/livingroom/available` | Publish | `online` / `offline` |

## Hardware

| GPIO | Function |
|------|----------|
| GPIO0 | Button input (active low with pull-up) |
| GPIO2 | Relay output |
| GPIO4 | Status LED |

See [docs/SETUP.md](docs/SETUP.md) for wiring diagrams and hardware details.

## Configuration

Edit `.env` for network settings:

```env
WIFI_SSID=your_wifi_ssid
WIFI_PASSWORD=your_wifi_password
MQTT_BROKER=192.168.1.100
```

Edit `src/config.rs` for device-specific settings (device name, topics, etc.)

## Project Structure

```
smart-switch/
├── Cargo.toml          # Rust dependencies
├── build.sh            # Build/flash helper script
├── .env.example        # Environment template
├── README.md           # This file
├── docs/
│   └── SETUP.md        # Detailed setup guide
└── src/
    ├── config.rs       # Configuration constants
    └── bin/
        └── main.rs     # Main firmware code
```

## Documentation

- **[Setup Guide](docs/SETUP.md)** - Complete setup instructions for new devices
- **[Configuration](docs/SETUP.md#configuration)** - How to customize settings
- **[Multiple Devices](docs/SETUP.md#adding-multiple-switches)** - Setting up additional switches
- **[Troubleshooting](docs/SETUP.md#troubleshooting)** - Common issues and solutions

## Tech Stack

- **[esp-hal](https://github.com/esp-rs/esp-hal)** - Hardware abstraction layer
- **[esp-radio](https://github.com/esp-rs/esp-wifi)** - WiFi driver
- **[embassy](https://embassy.dev/)** - Async runtime for embedded
- **[embassy-net](https://docs.embassy.dev/embassy-net/)** - TCP/IP networking

## License

MIT
