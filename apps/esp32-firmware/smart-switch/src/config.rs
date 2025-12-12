// WiFi Configuration
pub const WIFI_SSID: &str = env!("WIFI_SSID");
pub const WIFI_PASSWORD: &str = env!("WIFI_PASSWORD");

// MQTT Configuration
pub const MQTT_BROKER: &str = env!("MQTT_BROKER");
pub const MQTT_PORT: u16 = 1883;
pub const MQTT_CLIENT_ID: &str = "esp32-smart-switch-01";
pub const MQTT_USERNAME: Option<&str> = option_env!("MQTT_USERNAME");
pub const MQTT_PASSWORD: Option<&str> = option_env!("MQTT_PASSWORD");
pub const MQTT_KEEPALIVE_SECS: u16 = 30;

// MQTT Topics
pub const STATE_TOPIC: &str = "home/switch/livingroom/state";
pub const COMMAND_TOPIC: &str = "home/switch/livingroom/set";
pub const AVAILABILITY_TOPIC: &str = "home/switch/livingroom/available";

// Home Assistant Discovery
pub const HA_DISCOVERY_PREFIX: &str = "homeassistant";
pub const DEVICE_ID: &str = "esp32_switch_livingroom";
pub const DEVICE_NAME: &str = "Living Room Switch";

// GPIO Configuration
pub const RELAY_PIN: u8 = 2;   // GPIO2 - Relay control
pub const BUTTON_PIN: u8 = 0;  // GPIO0 - Physical button (boot button on most boards)
pub const LED_PIN: u8 = 2;     // GPIO2 - Built-in LED (same as relay for simple boards)

// Timing
pub const DEBOUNCE_MS: u64 = 50;
