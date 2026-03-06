//! Home Assistant MQTT Discovery payload builder for embedded devices.
//!
//! Generates discovery topics and JSON payloads that Home Assistant uses to
//! auto-configure entities. All operations are `no_std` compatible using
//! fixed-size `heapless` buffers with manual JSON construction (no serde).

#![no_std]

use heapless::{String, Vec};

/// Supported Home Assistant device types for MQTT discovery.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeviceType {
    Switch,
    Sensor,
    BinarySensor,
    Light,
}

impl DeviceType {
    /// Return the HA component string used in discovery topics and payloads.
    pub fn as_str(&self) -> &'static str {
        match self {
            DeviceType::Switch => "switch",
            DeviceType::Sensor => "sensor",
            DeviceType::BinarySensor => "binary_sensor",
            DeviceType::Light => "light",
        }
    }
}

/// Configuration for an HA MQTT discovery message.
///
/// Builds the discovery topic and JSON payload that Home Assistant subscribes
/// to for automatic entity creation.
pub struct DiscoveryConfig<'a> {
    pub device_type: DeviceType,
    pub discovery_prefix: &'a str,
    pub device_id: &'a str,
    pub device_name: &'a str,
    pub state_topic: &'a str,
    pub command_topic: Option<&'a str>,
    pub availability_topic: Option<&'a str>,
    pub unit_of_measurement: Option<&'a str>,
    pub device_class: Option<&'a str>,
    pub model: &'a str,
    pub manufacturer: &'a str,
}

impl<'a> DiscoveryConfig<'a> {
    /// Build the discovery topic: `{prefix}/{type}/{device_id}/config`
    pub fn topic(&self) -> String<128> {
        let mut t = String::new();
        let _ = t.push_str(self.discovery_prefix);
        let _ = t.push('/');
        let _ = t.push_str(self.device_type.as_str());
        let _ = t.push('/');
        let _ = t.push_str(self.device_id);
        let _ = t.push_str("/config");
        t
    }

    /// Build the JSON discovery payload.
    ///
    /// Constructs JSON manually to avoid serde dependency. The output includes
    /// the device block with identifiers, name, model, and manufacturer, plus
    /// all configured topic and metadata fields.
    pub fn payload(&self) -> Vec<u8, 512> {
        let mut buf = Vec::new();

        let _ = buf.extend_from_slice(b"{");

        // name
        push_json_str(&mut buf, "name", self.device_name, true);

        // unique_id
        push_json_comma(&mut buf);
        push_json_str(&mut buf, "unique_id", self.device_id, true);

        // state_topic
        push_json_comma(&mut buf);
        push_json_str(&mut buf, "state_topic", self.state_topic, true);

        // command_topic (optional, used by Switch and Light)
        if let Some(cmd) = self.command_topic {
            push_json_comma(&mut buf);
            push_json_str(&mut buf, "command_topic", cmd, true);
        }

        // availability_topic (optional, for LWT)
        if let Some(avail) = self.availability_topic {
            push_json_comma(&mut buf);
            push_json_str(&mut buf, "availability_topic", avail, true);
        }

        // unit_of_measurement (optional, for Sensor)
        if let Some(unit) = self.unit_of_measurement {
            push_json_comma(&mut buf);
            push_json_str(&mut buf, "unit_of_measurement", unit, true);
        }

        // device_class (optional)
        if let Some(dc) = self.device_class {
            push_json_comma(&mut buf);
            push_json_str(&mut buf, "device_class", dc, true);
        }

        // device block
        push_json_comma(&mut buf);
        let _ = buf.extend_from_slice(b"\"device\":{");
        push_json_str(&mut buf, "identifiers", self.device_id, false);
        let _ = buf.extend_from_slice(b"[\"");
        let _ = buf.extend_from_slice(self.device_id.as_bytes());
        let _ = buf.extend_from_slice(b"\"]");

        push_json_comma(&mut buf);
        push_json_str(&mut buf, "name", self.device_name, true);

        push_json_comma(&mut buf);
        push_json_str(&mut buf, "model", self.model, true);

        push_json_comma(&mut buf);
        push_json_str(&mut buf, "manufacturer", self.manufacturer, true);

        let _ = buf.push(b'}');

        let _ = buf.push(b'}');

        buf
    }
}

/// Write `"key":"value"` into the buffer. If `complete` is true, writes the
/// full key-value pair. If false, writes only `"key":` (caller appends value).
fn push_json_str(buf: &mut Vec<u8, 512>, key: &str, value: &str, complete: bool) {
    let _ = buf.push(b'"');
    let _ = buf.extend_from_slice(key.as_bytes());
    let _ = buf.extend_from_slice(b"\":");
    if complete {
        let _ = buf.push(b'"');
        let _ = buf.extend_from_slice(value.as_bytes());
        let _ = buf.push(b'"');
    }
}

/// Append a comma separator to the buffer.
fn push_json_comma(buf: &mut Vec<u8, 512>) {
    let _ = buf.push(b',');
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn discovery_topic_format() {
        let config = test_switch_config();
        let topic = config.topic();
        assert_eq!(topic.as_str(), "homeassistant/switch/test-switch/config");
    }

    #[test]
    fn payload_contains_required_fields() {
        let config = test_switch_config();
        let payload = config.payload();
        let json = core::str::from_utf8(&payload).unwrap();

        assert!(json.contains("\"name\":\"Test Switch\""));
        assert!(json.contains("\"unique_id\":\"test-switch\""));
        assert!(json.contains("\"state_topic\":\"home/switch/test-switch/state\""));
        assert!(json.contains("\"command_topic\":\"home/switch/test-switch/set\""));
        assert!(json.contains("\"manufacturer\":\"DIY\""));
    }

    #[test]
    fn sensor_without_command_topic() {
        let config = DiscoveryConfig {
            device_type: DeviceType::Sensor,
            discovery_prefix: "homeassistant",
            device_id: "temp-sensor-1",
            device_name: "Temperature",
            state_topic: "home/sensor/temp-sensor-1/state",
            command_topic: None,
            availability_topic: None,
            unit_of_measurement: Some("C"),
            device_class: Some("temperature"),
            model: "ESP32-C3",
            manufacturer: "DIY",
        };

        let topic = config.topic();
        assert_eq!(
            topic.as_str(),
            "homeassistant/sensor/temp-sensor-1/config"
        );

        let payload = config.payload();
        let json = core::str::from_utf8(&payload).unwrap();
        assert!(!json.contains("command_topic"));
        assert!(json.contains("\"unit_of_measurement\":\"C\""));
        assert!(json.contains("\"device_class\":\"temperature\""));
    }

    #[test]
    fn device_type_strings() {
        assert_eq!(DeviceType::Switch.as_str(), "switch");
        assert_eq!(DeviceType::Sensor.as_str(), "sensor");
        assert_eq!(DeviceType::BinarySensor.as_str(), "binary_sensor");
        assert_eq!(DeviceType::Light.as_str(), "light");
    }

    fn test_switch_config() -> DiscoveryConfig<'static> {
        DiscoveryConfig {
            device_type: DeviceType::Switch,
            discovery_prefix: "homeassistant",
            device_id: "test-switch",
            device_name: "Test Switch",
            state_topic: "home/switch/test-switch/state",
            command_topic: Some("home/switch/test-switch/set"),
            availability_topic: Some("home/switch/test-switch/available"),
            unit_of_measurement: None,
            device_class: None,
            model: "ESP32-S3",
            manufacturer: "DIY",
        }
    }
}
