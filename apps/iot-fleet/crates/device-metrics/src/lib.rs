//! Standard device metrics format for IoT fleet telemetry.
//!
//! Provides a common `DeviceMetrics` struct that all devices can use to report
//! health telemetry over MQTT. Supports JSON serialization for embedded targets
//! and Prometheus exposition format when the `std` feature is enabled.

#![cfg_attr(not(feature = "std"), no_std)]

use heapless::Vec;

/// Common device metrics reported by all fleet members.
///
/// Fields are optional where they only apply to certain device classes:
/// - `rssi`, `heap_free`: MCU/WiFi devices (ESP32, etc.)
/// - `cpu_usage`, `mem_usage`: Linux devices (Raspberry Pi, etc.)
/// - `temperature`: any device with a thermal sensor
pub struct DeviceMetrics<'a> {
    pub device_id: &'a str,
    pub firmware_version: &'a str,
    pub uptime_secs: u64,
    pub rssi: Option<i8>,
    pub heap_free: Option<u32>,
    pub cpu_usage: Option<f32>,
    pub mem_usage: Option<f32>,
    pub temperature: Option<f32>,
}

impl<'a> DeviceMetrics<'a> {
    /// Serialize to JSON for MQTT publishing.
    ///
    /// Manually constructs JSON to avoid serde dependency. Only includes
    /// fields that are `Some`.
    pub fn to_json(&self) -> Vec<u8, 512> {
        let mut buf = Vec::new();
        let _ = buf.extend_from_slice(b"{");

        // device_id
        let _ = buf.extend_from_slice(b"\"device_id\":\"");
        let _ = buf.extend_from_slice(self.device_id.as_bytes());
        let _ = buf.push(b'"');

        // firmware_version
        let _ = buf.extend_from_slice(b",\"firmware_version\":\"");
        let _ = buf.extend_from_slice(self.firmware_version.as_bytes());
        let _ = buf.push(b'"');

        // uptime_secs
        let _ = buf.extend_from_slice(b",\"uptime_secs\":");
        push_u64(&mut buf, self.uptime_secs);

        // rssi (optional)
        if let Some(rssi) = self.rssi {
            let _ = buf.extend_from_slice(b",\"rssi\":");
            push_i8(&mut buf, rssi);
        }

        // heap_free (optional)
        if let Some(heap) = self.heap_free {
            let _ = buf.extend_from_slice(b",\"heap_free\":");
            push_u32(&mut buf, heap);
        }

        // cpu_usage (optional)
        if let Some(cpu) = self.cpu_usage {
            let _ = buf.extend_from_slice(b",\"cpu_usage\":");
            push_f32(&mut buf, cpu);
        }

        // mem_usage (optional)
        if let Some(mem) = self.mem_usage {
            let _ = buf.extend_from_slice(b",\"mem_usage\":");
            push_f32(&mut buf, mem);
        }

        // temperature (optional)
        if let Some(temp) = self.temperature {
            let _ = buf.extend_from_slice(b",\"temperature\":");
            push_f32(&mut buf, temp);
        }

        let _ = buf.push(b'}');
        buf
    }

    /// Serialize to Prometheus exposition format.
    ///
    /// Outputs metric lines suitable for a Prometheus text endpoint.
    /// Each metric is labeled with `device_id`.
    #[cfg(feature = "std")]
    pub fn to_prometheus(&self) -> std::string::String {
        use std::format;
        let mut out = std::string::String::new();

        out.push_str(&format!(
            "device_uptime_seconds{{device_id=\"{}\"}} {}\n",
            self.device_id, self.uptime_secs
        ));

        out.push_str(&format!(
            "device_firmware_info{{device_id=\"{}\",version=\"{}\"}} 1\n",
            self.device_id, self.firmware_version
        ));

        if let Some(rssi) = self.rssi {
            out.push_str(&format!(
                "device_rssi_dbm{{device_id=\"{}\"}} {}\n",
                self.device_id, rssi
            ));
        }

        if let Some(heap) = self.heap_free {
            out.push_str(&format!(
                "device_heap_free_bytes{{device_id=\"{}\"}} {}\n",
                self.device_id, heap
            ));
        }

        if let Some(cpu) = self.cpu_usage {
            out.push_str(&format!(
                "device_cpu_usage_percent{{device_id=\"{}\"}} {:.1}\n",
                self.device_id, cpu
            ));
        }

        if let Some(mem) = self.mem_usage {
            out.push_str(&format!(
                "device_mem_usage_percent{{device_id=\"{}\"}} {:.1}\n",
                self.device_id, mem
            ));
        }

        if let Some(temp) = self.temperature {
            out.push_str(&format!(
                "device_temperature_celsius{{device_id=\"{}\"}} {:.1}\n",
                self.device_id, temp
            ));
        }

        out
    }
}

/// Write a u64 as decimal ASCII into the buffer.
fn push_u64(buf: &mut Vec<u8, 512>, val: u64) {
    if val == 0 {
        let _ = buf.push(b'0');
        return;
    }
    let mut tmp = [0u8; 20];
    let mut i = 0;
    let mut v = val;
    while v > 0 {
        tmp[i] = b'0' + (v % 10) as u8;
        v /= 10;
        i += 1;
    }
    // Reverse into buffer
    for j in (0..i).rev() {
        let _ = buf.push(tmp[j]);
    }
}

/// Write a u32 as decimal ASCII into the buffer.
fn push_u32(buf: &mut Vec<u8, 512>, val: u32) {
    push_u64(buf, val as u64);
}

/// Write an i8 as decimal ASCII into the buffer.
fn push_i8(buf: &mut Vec<u8, 512>, val: i8) {
    if val < 0 {
        let _ = buf.push(b'-');
        push_u64(buf, (-(val as i16)) as u64);
    } else {
        push_u64(buf, val as u64);
    }
}

/// Write an f32 as a fixed-point decimal with 1 decimal place.
///
/// This is a simple implementation suitable for metrics values.
/// Handles negative values and rounds to 1 decimal place.
fn push_f32(buf: &mut Vec<u8, 512>, val: f32) {
    if val < 0.0 {
        let _ = buf.push(b'-');
        push_f32_positive(buf, -val);
    } else {
        push_f32_positive(buf, val);
    }
}

fn push_f32_positive(buf: &mut Vec<u8, 512>, val: f32) {
    let integer = val as u32;
    let frac = ((val - integer as f32) * 10.0 + 0.5) as u32;
    if frac >= 10 {
        push_u32(buf, integer + 1);
        let _ = buf.extend_from_slice(b".0");
    } else {
        push_u32(buf, integer);
        let _ = buf.push(b'.');
        let _ = buf.push(b'0' + frac as u8);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_mcu_metrics() -> DeviceMetrics<'static> {
        DeviceMetrics {
            device_id: "esp32-switch-01",
            firmware_version: "0.1.0",
            uptime_secs: 86400,
            rssi: Some(-45),
            heap_free: Some(32000),
            cpu_usage: None,
            mem_usage: None,
            temperature: Some(42.5),
        }
    }

    fn sample_linux_metrics() -> DeviceMetrics<'static> {
        DeviceMetrics {
            device_id: "pi3-adguard",
            firmware_version: "1.2.0",
            uptime_secs: 604800,
            rssi: None,
            heap_free: None,
            cpu_usage: Some(12.3),
            mem_usage: Some(45.6),
            temperature: Some(55.0),
        }
    }

    #[test]
    fn json_mcu_metrics() {
        let m = sample_mcu_metrics();
        let json = m.to_json();
        let s = core::str::from_utf8(&json).unwrap();

        assert!(s.contains("\"device_id\":\"esp32-switch-01\""));
        assert!(s.contains("\"uptime_secs\":86400"));
        assert!(s.contains("\"rssi\":-45"));
        assert!(s.contains("\"heap_free\":32000"));
        assert!(!s.contains("cpu_usage"));
        assert!(!s.contains("mem_usage"));
        assert!(s.contains("\"temperature\":42.5"));
    }

    #[test]
    fn json_linux_metrics() {
        let m = sample_linux_metrics();
        let json = m.to_json();
        let s = core::str::from_utf8(&json).unwrap();

        assert!(s.contains("\"device_id\":\"pi3-adguard\""));
        assert!(s.contains("\"cpu_usage\":12.3"));
        assert!(s.contains("\"mem_usage\":45.6"));
        assert!(!s.contains("rssi"));
        assert!(!s.contains("heap_free"));
    }

    #[test]
    #[cfg(feature = "std")]
    fn prometheus_format() {
        let m = sample_mcu_metrics();
        let prom = m.to_prometheus();

        assert!(prom.contains("device_uptime_seconds{device_id=\"esp32-switch-01\"} 86400"));
        assert!(prom.contains("device_rssi_dbm{device_id=\"esp32-switch-01\"} -45"));
        assert!(prom.contains("device_heap_free_bytes{device_id=\"esp32-switch-01\"} 32000"));
        assert!(prom.contains("device_temperature_celsius{device_id=\"esp32-switch-01\"} 42.5"));
    }
}
