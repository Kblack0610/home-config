#![no_std]
#![no_main]
#![deny(
    clippy::mem_forget,
    reason = "mem::forget is generally not safe with esp_hal types"
)]
#![deny(clippy::large_stack_frames)]

extern crate alloc;

use core::sync::atomic::{AtomicBool, Ordering};

use defmt::{error, info, warn};
use embassy_executor::Spawner;
use embassy_net::{tcp::TcpSocket, Config, Ipv4Address, StackResources};
use embassy_time::{Duration, Instant, Timer};
use embedded_io_async::{Read, Write};
use esp_hal::clock::CpuClock;
use esp_hal::gpio::{Input, InputConfig, Level, Output, OutputConfig, Pull};
use esp_hal::timer::timg::TimerGroup;
use esp_radio::wifi::{WifiController, WifiDevice};
use static_cell::StaticCell;
use {esp_backtrace as _, esp_println as _};

// Configuration - set via environment variables at compile time
mod config {
    pub const WIFI_SSID: &str = env!("WIFI_SSID");
    pub const WIFI_PASSWORD: &str = env!("WIFI_PASSWORD");
    pub const MQTT_BROKER: &str = env!("MQTT_BROKER");
    pub const MQTT_PORT: u16 = 1883;
    pub const DEBOUNCE_MS: u64 = 50;

    // MQTT Topics
    pub const STATE_TOPIC: &str = "home/switch/livingroom/state";
    pub const COMMAND_TOPIC: &str = "home/switch/livingroom/set";
    pub const AVAILABILITY_TOPIC: &str = "home/switch/livingroom/available";

    // Home Assistant Discovery
    pub const HA_DISCOVERY_PREFIX: &str = "homeassistant";
    pub const DEVICE_ID: &str = "esp32_switch_livingroom";
    pub const DEVICE_NAME: &str = "Living Room Switch";
}

// Global state - shared between tasks
static SWITCH_STATE: AtomicBool = AtomicBool::new(false);
static STATE_CHANGED: AtomicBool = AtomicBool::new(false);
static PUBLISH_REQUESTED: AtomicBool = AtomicBool::new(false);

esp_bootloader_esp_idf::esp_app_desc!();

// Network stack resources
static RESOURCES: StaticCell<StackResources<3>> = StaticCell::new();
static RADIO_INIT: StaticCell<esp_radio::Controller<'static>> = StaticCell::new();

#[allow(clippy::large_stack_frames)]
#[esp_rtos::main]
async fn main(spawner: Spawner) -> ! {
    info!("Smart Switch starting...");

    let hal_config = esp_hal::Config::default().with_cpu_clock(CpuClock::max());
    let peripherals = esp_hal::init(hal_config);

    esp_alloc::heap_allocator!(#[esp_hal::ram(reclaimed)] size: 98768);

    let timg0 = TimerGroup::new(peripherals.TIMG0);
    esp_rtos::start(timg0.timer0);

    // Initialize GPIO
    let relay = Output::new(peripherals.GPIO2, Level::Low, OutputConfig::default());
    let button = Input::new(peripherals.GPIO0, InputConfig::default().with_pull(Pull::Up));

    // Initialize Wi-Fi
    let radio_init = RADIO_INIT.init(esp_radio::init().expect("Failed to initialize radio"));
    let (wifi_controller, interfaces) =
        esp_radio::wifi::new(radio_init, peripherals.WIFI, Default::default())
            .expect("Failed to initialize Wi-Fi");

    let wifi_sta = interfaces.sta;

    // Create network stack
    let resources = RESOURCES.init(StackResources::new());
    let dhcp_config = Config::dhcpv4(Default::default());
    let seed = Instant::now().as_millis() as u64;
    let (stack, runner) = embassy_net::new(wifi_sta, dhcp_config, resources, seed);

    // Spawn background tasks
    spawner.spawn(wifi_task(wifi_controller)).ok();
    spawner.spawn(net_task(runner)).ok();
    spawner.spawn(button_task(button)).ok();
    spawner.spawn(relay_task(relay)).ok();

    // Wait for WiFi connection
    info!("Waiting for WiFi connection...");
    loop {
        if stack.is_link_up() {
            break;
        }
        Timer::after(Duration::from_millis(500)).await;
    }

    info!("WiFi connected, waiting for IP...");
    loop {
        if let Some(ip_config) = stack.config_v4() {
            info!("Got IP: {}", ip_config.address);
            break;
        }
        Timer::after(Duration::from_millis(500)).await;
    }

    // Run simple TCP connection test to MQTT broker
    info!("Network ready! Starting MQTT connection loop...");

    let mut rx_buffer = [0u8; 1024];
    let mut tx_buffer = [0u8; 1024];

    loop {
        // Report state changes
        if STATE_CHANGED.swap(false, Ordering::SeqCst) {
            let state = if SWITCH_STATE.load(Ordering::SeqCst) { "ON" } else { "OFF" };
            info!("Switch state changed: {}", state);
        }

        // Try connecting to MQTT broker
        let mut socket = TcpSocket::new(stack, &mut rx_buffer, &mut tx_buffer);
        socket.set_timeout(Some(Duration::from_secs(10)));

        let broker_ip = parse_ip(config::MQTT_BROKER).unwrap_or(Ipv4Address::new(192, 168, 1, 100));
        info!("Attempting TCP connection to {}:{}", broker_ip, config::MQTT_PORT);

        match socket.connect((broker_ip, config::MQTT_PORT)).await {
            Ok(()) => {
                info!("TCP connected to MQTT broker!");

                // Send MQTT CONNECT packet with Last Will Testament for availability
                let client_id = b"esp32-switch";
                let connect_packet = build_mqtt_connect_with_lwt(
                    client_id,
                    config::AVAILABILITY_TOPIC,
                    b"offline",
                );

                if socket.write_all(&connect_packet).await.is_err() {
                    error!("Failed to send CONNECT");
                    continue;
                }
                info!("MQTT CONNECT sent");

                // Read CONNACK response
                let mut buf = [0u8; 4];
                if socket.read_exact(&mut buf).await.is_err() {
                    error!("Failed to read CONNACK");
                    continue;
                }

                if buf[0] != 0x20 || buf[3] != 0x00 {
                    error!("MQTT CONNACK failed: {:02x} {:02x}", buf[2], buf[3]);
                    continue;
                }
                info!("MQTT connected successfully!");

                // Publish Home Assistant discovery config
                let discovery_topic = ha_discovery_topic();
                let discovery_payload = build_ha_discovery_payload();
                let discovery_packet = build_mqtt_publish(&discovery_topic, &discovery_payload);
                if socket.write_all(&discovery_packet).await.is_err() {
                    warn!("Failed to publish HA discovery");
                }
                info!("Published Home Assistant discovery config");

                // Publish availability = online
                let avail_packet = build_mqtt_publish(config::AVAILABILITY_TOPIC, b"online");
                if socket.write_all(&avail_packet).await.is_err() {
                    warn!("Failed to publish availability");
                }
                info!("Published availability: online");

                // Subscribe to command topic
                let subscribe_packet = build_mqtt_subscribe(config::COMMAND_TOPIC, 1);
                if socket.write_all(&subscribe_packet).await.is_err() {
                    error!("Failed to send SUBSCRIBE");
                    continue;
                }
                info!("Subscribed to {}", config::COMMAND_TOPIC);

                // Read SUBACK response
                let mut suback_buf = [0u8; 5];
                if socket.read_exact(&mut suback_buf).await.is_err() {
                    warn!("Failed to read SUBACK, continuing anyway...");
                }

                // Publish initial state
                PUBLISH_REQUESTED.store(true, Ordering::SeqCst);

                // Main MQTT loop - handle incoming messages and publish state changes
                let mut read_buf = [0u8; 256];
                let mut last_publish = Instant::now();

                loop {
                    // Check if we need to publish state
                    let should_publish = PUBLISH_REQUESTED.swap(false, Ordering::SeqCst)
                        || last_publish.elapsed() > Duration::from_secs(30);

                    if should_publish {
                        let state = if SWITCH_STATE.load(Ordering::SeqCst) { "ON" } else { "OFF" };
                        let publish = build_mqtt_publish(config::STATE_TOPIC, state.as_bytes());

                        if socket.write_all(&publish).await.is_err() {
                            warn!("MQTT publish failed, reconnecting...");
                            break;
                        }

                        info!("Published: {} = {}", config::STATE_TOPIC, state);
                        last_publish = Instant::now();
                    }

                    // Try to read incoming messages (non-blocking check with short timeout)
                    socket.set_timeout(Some(Duration::from_millis(100)));
                    match socket.read(&mut read_buf).await {
                        Ok(0) => {
                            // Connection closed
                            warn!("MQTT connection closed");
                            break;
                        }
                        Ok(n) => {
                            // Process received MQTT packet
                            if let Some((topic, payload)) = parse_mqtt_publish(&read_buf[..n]) {
                                info!("Received: {} = {}", topic, payload);
                                handle_mqtt_command(topic, payload);
                            }
                        }
                        Err(_) => {
                            // Timeout or error - just continue polling
                        }
                    }
                    socket.set_timeout(Some(Duration::from_secs(10)));

                    // Small delay to prevent busy loop
                    Timer::after(Duration::from_millis(50)).await;
                }
            }
            Err(_) => {
                warn!("TCP connection failed");
            }
        }

        Timer::after(Duration::from_secs(5)).await;
    }
}

#[embassy_executor::task]
async fn wifi_task(mut controller: WifiController<'static>) {
    use esp_radio::wifi::{ClientConfig, ModeConfig};

    info!("WiFi task: connecting to '{}'", config::WIFI_SSID);

    // Set up WiFi configuration
    let client_config = ClientConfig::default()
        .with_ssid(alloc::string::String::from(config::WIFI_SSID))
        .with_password(alloc::string::String::from(config::WIFI_PASSWORD));
    let wifi_config = ModeConfig::Client(client_config);

    loop {
        // Set configuration
        if let Err(e) = controller.set_config(&wifi_config) {
            error!("Failed to set WiFi config: {:?}", e);
            Timer::after(Duration::from_secs(5)).await;
            continue;
        }

        // Start WiFi
        if let Err(e) = controller.start() {
            error!("Failed to start WiFi: {:?}", e);
            Timer::after(Duration::from_secs(5)).await;
            continue;
        }

        // Connect
        match controller.connect() {
            Ok(()) => {
                info!("WiFi connected!");
                // Stay connected
                loop {
                    match controller.is_connected() {
                        Ok(true) => {}
                        Ok(false) => {
                            warn!("WiFi disconnected!");
                            break;
                        }
                        Err(e) => {
                            error!("WiFi status error: {:?}", e);
                            break;
                        }
                    }
                    Timer::after(Duration::from_secs(5)).await;
                }
            }
            Err(e) => {
                error!("WiFi connection failed: {:?}", e);
                Timer::after(Duration::from_secs(5)).await;
            }
        }
    }
}

#[embassy_executor::task]
async fn net_task(mut runner: embassy_net::Runner<'static, WifiDevice<'static>>) {
    runner.run().await;
}

#[embassy_executor::task]
async fn button_task(button: Input<'static>) {
    let mut last_state = button.is_high();

    loop {
        let current_state = button.is_high();

        // Detect falling edge (button pressed - active low with pull-up)
        if last_state && !current_state {
            Timer::after(Duration::from_millis(config::DEBOUNCE_MS)).await;

            if button.is_low() {
                let new_state = !SWITCH_STATE.load(Ordering::SeqCst);
                SWITCH_STATE.store(new_state, Ordering::SeqCst);
                STATE_CHANGED.store(true, Ordering::SeqCst);
                info!("Button pressed! New state: {}", if new_state { "ON" } else { "OFF" });
            }
        }

        last_state = current_state;
        Timer::after(Duration::from_millis(10)).await;
    }
}

#[embassy_executor::task]
async fn relay_task(mut relay: Output<'static>) {
    let mut last_state = false;

    loop {
        let current_state = SWITCH_STATE.load(Ordering::SeqCst);

        if current_state != last_state {
            if current_state {
                relay.set_high();
                info!("Relay ON");
            } else {
                relay.set_low();
                info!("Relay OFF");
            }
            last_state = current_state;
        }

        Timer::after(Duration::from_millis(50)).await;
    }
}

// Build a minimal MQTT v3.1.1 CONNECT packet
#[allow(dead_code)]
fn build_mqtt_connect(client_id: &[u8]) -> heapless::Vec<u8, 64> {
    let mut packet = heapless::Vec::new();

    let remaining_len = 10 + 2 + client_id.len();
    let _ = packet.push(0x10); // CONNECT packet type
    let _ = packet.push(remaining_len as u8);

    // Protocol name "MQTT"
    let _ = packet.push(0x00);
    let _ = packet.push(0x04);
    let _ = packet.extend_from_slice(b"MQTT");

    // Protocol level (4 = MQTT 3.1.1)
    let _ = packet.push(0x04);

    // Connect flags: Clean session
    let _ = packet.push(0x02);

    // Keep alive (60 seconds)
    let _ = packet.push(0x00);
    let _ = packet.push(0x3C);

    // Client ID
    let _ = packet.push(0x00);
    let _ = packet.push(client_id.len() as u8);
    let _ = packet.extend_from_slice(client_id);

    packet
}

// Build MQTT CONNECT packet with Last Will and Testament (LWT)
fn build_mqtt_connect_with_lwt(
    client_id: &[u8],
    will_topic: &str,
    will_message: &[u8],
) -> heapless::Vec<u8, 128> {
    let mut packet = heapless::Vec::new();

    let remaining_len = 10
        + 2 + client_id.len()     // client ID
        + 2 + will_topic.len()    // will topic
        + 2 + will_message.len(); // will message

    let _ = packet.push(0x10); // CONNECT packet type
    let _ = packet.push(remaining_len as u8);

    // Protocol name "MQTT"
    let _ = packet.push(0x00);
    let _ = packet.push(0x04);
    let _ = packet.extend_from_slice(b"MQTT");

    // Protocol level (4 = MQTT 3.1.1)
    let _ = packet.push(0x04);

    // Connect flags: Clean session (0x02) + Will flag (0x04) + Will retain (0x20)
    let _ = packet.push(0x26);

    // Keep alive (60 seconds)
    let _ = packet.push(0x00);
    let _ = packet.push(0x3C);

    // Client ID
    let _ = packet.push(0x00);
    let _ = packet.push(client_id.len() as u8);
    let _ = packet.extend_from_slice(client_id);

    // Will topic
    let _ = packet.push(0x00);
    let _ = packet.push(will_topic.len() as u8);
    let _ = packet.extend_from_slice(will_topic.as_bytes());

    // Will message
    let _ = packet.push(0x00);
    let _ = packet.push(will_message.len() as u8);
    let _ = packet.extend_from_slice(will_message);

    packet
}

// Build a minimal MQTT PUBLISH packet (QoS 0)
fn build_mqtt_publish(topic: &str, payload: &[u8]) -> heapless::Vec<u8, 640> {
    let mut packet = heapless::Vec::new();

    let remaining_len = 2 + topic.len() + payload.len();

    let _ = packet.push(0x30); // PUBLISH packet type

    // Encode remaining length (can be multi-byte for larger packets)
    if remaining_len < 128 {
        let _ = packet.push(remaining_len as u8);
    } else {
        // Two-byte encoding for lengths 128-16383
        let _ = packet.push((remaining_len % 128) as u8 | 0x80);
        let _ = packet.push((remaining_len / 128) as u8);
    }

    // Topic
    let _ = packet.push(0x00);
    let _ = packet.push(topic.len() as u8);
    let _ = packet.extend_from_slice(topic.as_bytes());

    // Payload
    let _ = packet.extend_from_slice(payload);

    packet
}

// Build a minimal MQTT SUBSCRIBE packet (QoS 0)
fn build_mqtt_subscribe(topic: &str, packet_id: u16) -> heapless::Vec<u8, 128> {
    let mut packet = heapless::Vec::new();

    let remaining_len = 2 + 2 + topic.len() + 1; // packet_id + topic_len + topic + qos

    let _ = packet.push(0x82); // SUBSCRIBE packet type with QoS 1 flag
    let _ = packet.push(remaining_len as u8);

    // Packet identifier
    let _ = packet.push((packet_id >> 8) as u8);
    let _ = packet.push(packet_id as u8);

    // Topic filter
    let _ = packet.push(0x00);
    let _ = packet.push(topic.len() as u8);
    let _ = packet.extend_from_slice(topic.as_bytes());

    // Requested QoS (0)
    let _ = packet.push(0x00);

    packet
}

// Parse an incoming MQTT PUBLISH packet, returns (topic, payload) if valid
fn parse_mqtt_publish(data: &[u8]) -> Option<(&str, &str)> {
    if data.is_empty() {
        return None;
    }

    // Check if this is a PUBLISH packet (0x30-0x3F)
    let packet_type = data[0] & 0xF0;
    if packet_type != 0x30 {
        return None;
    }

    if data.len() < 4 {
        return None;
    }

    // Get remaining length (simple single-byte for now)
    let remaining_len = data[1] as usize;
    if data.len() < 2 + remaining_len {
        return None;
    }

    // Get topic length
    let topic_len = ((data[2] as usize) << 8) | (data[3] as usize);
    if data.len() < 4 + topic_len {
        return None;
    }

    // Extract topic
    let topic = core::str::from_utf8(&data[4..4 + topic_len]).ok()?;

    // Extract payload (everything after topic, accounting for QoS packet ID if present)
    let qos = (data[0] >> 1) & 0x03;
    let payload_start = if qos > 0 {
        4 + topic_len + 2 // Skip 2-byte packet identifier for QoS 1/2
    } else {
        4 + topic_len
    };

    if payload_start > 2 + remaining_len {
        return None;
    }

    let payload = core::str::from_utf8(&data[payload_start..2 + remaining_len]).ok()?;

    Some((topic, payload))
}

// Handle incoming MQTT commands
fn handle_mqtt_command(topic: &str, payload: &str) {
    // Check if this is for our command topic
    if topic != config::COMMAND_TOPIC {
        return;
    }

    let payload_upper = payload.trim();

    let new_state = match payload_upper {
        "ON" | "on" | "1" | "true" | "TRUE" => Some(true),
        "OFF" | "off" | "0" | "false" | "FALSE" => Some(false),
        "TOGGLE" | "toggle" => Some(!SWITCH_STATE.load(Ordering::SeqCst)),
        _ => {
            warn!("Unknown command payload: {}", payload);
            None
        }
    };

    if let Some(state) = new_state {
        let current = SWITCH_STATE.load(Ordering::SeqCst);
        if state != current {
            SWITCH_STATE.store(state, Ordering::SeqCst);
            STATE_CHANGED.store(true, Ordering::SeqCst);
            PUBLISH_REQUESTED.store(true, Ordering::SeqCst);
            info!("Command received: setting switch to {}", if state { "ON" } else { "OFF" });
        }
    }
}

// Build Home Assistant MQTT Discovery topic
fn ha_discovery_topic() -> heapless::String<128> {
    let mut topic = heapless::String::new();
    let _ = core::fmt::write(
        &mut topic,
        format_args!(
            "{}/switch/{}/config",
            config::HA_DISCOVERY_PREFIX,
            config::DEVICE_ID
        ),
    );
    topic
}

// Build Home Assistant MQTT Discovery payload (JSON)
fn build_ha_discovery_payload() -> heapless::Vec<u8, 512> {
    let mut payload = heapless::Vec::new();

    // Build JSON manually to avoid serde dependency
    let _ = payload.extend_from_slice(br#"{"name":""#);
    let _ = payload.extend_from_slice(config::DEVICE_NAME.as_bytes());
    let _ = payload.extend_from_slice(br#"","state_topic":""#);
    let _ = payload.extend_from_slice(config::STATE_TOPIC.as_bytes());
    let _ = payload.extend_from_slice(br#"","command_topic":""#);
    let _ = payload.extend_from_slice(config::COMMAND_TOPIC.as_bytes());
    let _ = payload.extend_from_slice(br#"","availability_topic":""#);
    let _ = payload.extend_from_slice(config::AVAILABILITY_TOPIC.as_bytes());
    let _ = payload.extend_from_slice(br#"","payload_on":"ON","payload_off":"OFF""#);
    let _ = payload.extend_from_slice(br#","unique_id":""#);
    let _ = payload.extend_from_slice(config::DEVICE_ID.as_bytes());
    let _ = payload.extend_from_slice(br#"","device":{"identifiers":[""#);
    let _ = payload.extend_from_slice(config::DEVICE_ID.as_bytes());
    let _ = payload.extend_from_slice(br#""],"name":""#);
    let _ = payload.extend_from_slice(config::DEVICE_NAME.as_bytes());
    let _ = payload.extend_from_slice(br#"","model":"ESP32 DIY Switch","manufacturer":"DIY"}}"#);

    payload
}

fn parse_ip(s: &str) -> Option<Ipv4Address> {
    let mut parts = s.split('.');
    let a: u8 = parts.next()?.parse().ok()?;
    let b: u8 = parts.next()?.parse().ok()?;
    let c: u8 = parts.next()?.parse().ok()?;
    let d: u8 = parts.next()?.parse().ok()?;
    Some(Ipv4Address::new(a, b, c, d))
}
