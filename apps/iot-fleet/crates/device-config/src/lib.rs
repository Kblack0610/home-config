//! MQTT topic builders and configuration helpers for IoT fleet devices.
//!
//! Provides a standard topic naming convention so all devices in the fleet
//! use consistent, predictable MQTT topics. All operations are `no_std`
//! compatible using `heapless::String` for fixed-size topic buffers.

#![no_std]

use heapless::String;

/// Standard MQTT topic suffixes used across the fleet.
pub mod topics {
    /// Device state (e.g., "ON", "OFF", sensor readings)
    pub const STATE: &str = "state";
    /// Command input (e.g., "ON", "OFF" for switches)
    pub const SET: &str = "set";
    /// Availability for LWT ("online" / "offline")
    pub const AVAILABLE: &str = "available";
    /// Device health metrics JSON
    pub const METRICS: &str = "metrics";
}

/// Build a standard MQTT topic: `home/{device_type}/{device_id}/{suffix}`
///
/// # Examples
///
/// ```
/// use device_config::build_topic;
///
/// let topic = build_topic("switch", "esp32-01", "state");
/// assert_eq!(topic.as_str(), "home/switch/esp32-01/state");
/// ```
pub fn build_topic(device_type: &str, device_id: &str, suffix: &str) -> String<128> {
    let mut t = String::new();
    let _ = t.push_str("home/");
    let _ = t.push_str(device_type);
    let _ = t.push('/');
    let _ = t.push_str(device_id);
    let _ = t.push('/');
    let _ = t.push_str(suffix);
    t
}

/// Build the metrics topic: `home/metrics/{device_id}`
///
/// Metrics use a flat namespace separate from device-type topics so that
/// a single subscriber can collect telemetry from all device types.
pub fn metrics_topic(device_id: &str) -> String<128> {
    let mut t = String::new();
    let _ = t.push_str("home/metrics/");
    let _ = t.push_str(device_id);
    t
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn standard_topic_format() {
        let t = build_topic("switch", "esp32-01", topics::STATE);
        assert_eq!(t.as_str(), "home/switch/esp32-01/state");
    }

    #[test]
    fn command_topic() {
        let t = build_topic("switch", "esp32-01", topics::SET);
        assert_eq!(t.as_str(), "home/switch/esp32-01/set");
    }

    #[test]
    fn availability_topic() {
        let t = build_topic("light", "esp32-02", topics::AVAILABLE);
        assert_eq!(t.as_str(), "home/light/esp32-02/available");
    }

    #[test]
    fn metrics_topic_format() {
        let t = metrics_topic("pi3-adguard");
        assert_eq!(t.as_str(), "home/metrics/pi3-adguard");
    }
}
