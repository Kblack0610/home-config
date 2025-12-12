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
}

// Global state - shared between tasks
static SWITCH_STATE: AtomicBool = AtomicBool::new(false);
static STATE_CHANGED: AtomicBool = AtomicBool::new(false);

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

                // Send MQTT CONNECT packet
                let client_id = b"esp32-switch";
                let connect_packet = build_mqtt_connect(client_id);

                match socket.write_all(&connect_packet).await {
                    Ok(()) => {
                        info!("MQTT CONNECT sent");

                        // Read CONNACK response
                        let mut buf = [0u8; 4];
                        match socket.read_exact(&mut buf).await {
                            Ok(()) => {
                                if buf[0] == 0x20 && buf[3] == 0x00 {
                                    info!("MQTT connected successfully!");

                                    // Main MQTT loop - publish state periodically
                                    loop {
                                        let state = if SWITCH_STATE.load(Ordering::SeqCst) { "ON" } else { "OFF" };
                                        let topic = "home/switch/livingroom/state";
                                        let publish = build_mqtt_publish(topic, state.as_bytes());

                                        if socket.write_all(&publish).await.is_err() {
                                            warn!("MQTT publish failed, reconnecting...");
                                            break;
                                        }

                                        info!("Published: {} = {}", topic, state);
                                        Timer::after(Duration::from_secs(5)).await;
                                    }
                                } else {
                                    error!("MQTT CONNACK failed: {:02x} {:02x}", buf[2], buf[3]);
                                }
                            }
                            Err(_) => error!("Failed to read CONNACK"),
                        }
                    }
                    Err(_) => error!("Failed to send CONNECT"),
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

// Build a minimal MQTT PUBLISH packet (QoS 0)
fn build_mqtt_publish(topic: &str, payload: &[u8]) -> heapless::Vec<u8, 128> {
    let mut packet = heapless::Vec::new();

    let remaining_len = 2 + topic.len() + payload.len();

    let _ = packet.push(0x30); // PUBLISH packet type
    let _ = packet.push(remaining_len as u8);

    // Topic
    let _ = packet.push(0x00);
    let _ = packet.push(topic.len() as u8);
    let _ = packet.extend_from_slice(topic.as_bytes());

    // Payload
    let _ = packet.extend_from_slice(payload);

    packet
}

fn parse_ip(s: &str) -> Option<Ipv4Address> {
    let mut parts = s.split('.');
    let a: u8 = parts.next()?.parse().ok()?;
    let b: u8 = parts.next()?.parse().ok()?;
    let c: u8 = parts.next()?.parse().ok()?;
    let d: u8 = parts.next()?.parse().ok()?;
    Some(Ipv4Address::new(a, b, c, d))
}
