use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    #[serde(default = "default_device_id")]
    pub device_id: String,
    #[serde(default = "default_listen_port")]
    pub listen_port: u16,
    #[serde(default = "default_mqtt_host")]
    pub mqtt_host: String,
    #[serde(default = "default_mqtt_port")]
    pub mqtt_port: u16,
    #[serde(default = "default_publish_interval_secs")]
    pub publish_interval_secs: u64,
    #[serde(default = "default_version")]
    pub firmware_version: String,
}

fn default_device_id() -> String {
    hostname().unwrap_or_else(|| "pi-unknown".into())
}
fn default_listen_port() -> u16 { 9200 }
fn default_mqtt_host() -> String { "192.168.1.20".into() }
fn default_mqtt_port() -> u16 { 31883 }
fn default_publish_interval_secs() -> u64 { 30 }
fn default_version() -> String { env!("CARGO_PKG_VERSION").into() }

fn hostname() -> Option<String> {
    std::fs::read_to_string("/etc/hostname")
        .ok()
        .map(|s| s.trim().to_string())
}

pub fn load() -> Config {
    let path = "/etc/pi-monitor.toml";
    match std::fs::read_to_string(path) {
        Ok(content) => toml::from_str(&content).unwrap_or_else(|e| {
            eprintln!("warn: failed to parse {path}: {e}, using defaults");
            toml::from_str("").unwrap()
        }),
        Err(_) => {
            eprintln!("info: no config at {path}, using defaults");
            toml::from_str("").unwrap()
        }
    }
}
