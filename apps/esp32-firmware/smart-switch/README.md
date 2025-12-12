# ESP32 Smart Switch

A Rust-based smart switch firmware for ESP32, designed as an alternative to ESPHome with full Home Assistant integration.

## Features

- **WiFi Connectivity**: Connects to configured WiFi network
- **MQTT Support**: Publishes state and receives commands via MQTT
- **Home Assistant Auto-Discovery**: Automatically registers with Home Assistant
- **Physical Button**: Toggle switch via GPIO0 button (with debouncing)
- **Relay Control**: Controls relay on GPIO2
- **Status LED**: Connection status indication on GPIO4:
  - Fast blink: WiFi connecting
  - Slow blink: MQTT connecting
  - Solid: Fully connected
- **Last Will Testament**: Reports offline status on disconnection

## Hardware Requirements

- ESP32 development board (tested on ESP32-WROOM-32)
- Relay module connected to GPIO2
- Optional: External LED on GPIO4 for status indication
- Optional: External button on GPIO0 (most boards have boot button)

## Prerequisites

1. Install Rust and ESP32 toolchain:
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   cargo install espup espflash
   espup install
   source ~/export-esp.sh
   ```

2. Create a `.env` file with your configuration:
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

## Configuration

Edit `.env` with your settings:

```env
WIFI_SSID=your_wifi_ssid
WIFI_PASSWORD=your_wifi_password
MQTT_BROKER=192.168.1.100
```

For additional configuration (MQTT topics, device ID, GPIO pins), edit `src/config.rs`.

## Building

```bash
# Source ESP toolchain
source ~/export-esp.sh

# Build only
./build.sh

# Build and flash
./build.sh flash

# Monitor serial output
./build.sh monitor
```

## MQTT Topics

| Topic | Direction | Description |
|-------|-----------|-------------|
| `home/switch/livingroom/state` | Publish | Current switch state (ON/OFF) |
| `home/switch/livingroom/set` | Subscribe | Command to set state (ON/OFF/TOGGLE) |
| `home/switch/livingroom/available` | Publish | Availability status (online/offline) |

## Home Assistant Integration

The switch automatically registers with Home Assistant via MQTT Discovery. Ensure your Home Assistant has MQTT integration configured.

The device will appear as "Living Room Switch" under MQTT integration after first boot.

## GPIO Pin Configuration

| GPIO | Function | Notes |
|------|----------|-------|
| GPIO0 | Button Input | Active low with pull-up (boot button) |
| GPIO2 | Relay Output | Controls relay module |
| GPIO4 | Status LED | Connection status indicator |

## Troubleshooting

1. **Build fails**: Ensure `source ~/export-esp.sh` has been run
2. **WiFi won't connect**: Check SSID/password in `.env`
3. **MQTT fails**: Verify broker IP address is correct
4. **No HA discovery**: Check MQTT broker is connected to Home Assistant

## Project Structure

```
smart-switch/
├── Cargo.toml          # Dependencies and build config
├── build.sh            # Build helper script
├── .env.example        # Environment template
├── src/
│   ├── config.rs       # Configuration constants
│   └── bin/
│       └── main.rs     # Main firmware code
```

## License

MIT
