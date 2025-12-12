// WiFi Configuration
pub const WIFI_SSID: &str = env!("WIFI_SSID");
pub const WIFI_PASSWORD: &str = env!("WIFI_PASSWORD");

// MQTT Configuration
pub const MQTT_BROKER: &str = env!("MQTT_BROKER");
pub const MQTT_PORT: u16 = 1883;
pub const MQTT_CLIENT_ID: &str = "esp32-smart-switch-01";
pub const MQTT_USERNAME: Option<&str> = option_env!("MQTT_USERNAME");
pub const MQTT_PASSWORD: Option<&str> = option_env!("MQTT_PASSWORD");

// Topics
pub const MQTT_STATE_TOPIC: &str = "home/switch/livingroom/state";
pub const MQTT_COMMAND_TOPIC: &str = "home/switch/livingroom/set";
pub const MQTT_AVAILABILITY_TOPIC: &str = "home/switch/livingroom/available";

// GPIO Configuration
pub const RELAY_PIN: u8 = 2;   // GPIO2 - Relay control
pub const BUTTON_PIN: u8 = 0;  // GPIO0 - Physical button (boot button on most boards)
pub const LED_PIN: u8 = 2;     // GPIO2 - Built-in LED (same as relay for simple boards)

// Timing
pub const DEBOUNCE_MS: u64 = 50;
pub const MQTT_KEEPALIVE_SECS: u16 = 30;
