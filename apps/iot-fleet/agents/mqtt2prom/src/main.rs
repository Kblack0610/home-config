use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use rumqttc::{AsyncClient, Event, MqttOptions, Packet, QoS};
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
struct DeviceReport {
    device_id: String,
    #[serde(default)]
    firmware_version: String,
    #[serde(default)]
    uptime_secs: u64,
    rssi: Option<i8>,
    heap_free: Option<u32>,
    cpu_usage: Option<f32>,
    mem_usage: Option<f32>,
    temperature: Option<f32>,
}

type DeviceMap = Arc<RwLock<HashMap<String, DeviceReport>>>;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let mqtt_host = std::env::var("MQTT_HOST").unwrap_or_else(|_| "192.168.1.20".into());
    let mqtt_port: u16 = std::env::var("MQTT_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(31883);
    let http_port: u16 = std::env::var("HTTP_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(9201);

    let devices: DeviceMap = Arc::new(RwLock::new(HashMap::new()));

    // Spawn MQTT subscriber
    let devices_mqtt = devices.clone();
    tokio::spawn(async move {
        mqtt_loop(mqtt_host, mqtt_port, devices_mqtt).await;
    });

    // Run HTTP server (blocking)
    http_serve(http_port, devices);
}

async fn mqtt_loop(host: String, port: u16, devices: DeviceMap) {
    loop {
        let mut opts = MqttOptions::new("mqtt2prom", &host, port);
        opts.set_keep_alive(std::time::Duration::from_secs(30));

        let (client, mut eventloop) = AsyncClient::new(opts, 64);

        if let Err(e) = client.subscribe("home/metrics/#", QoS::AtMostOnce).await {
            eprintln!("MQTT subscribe failed: {e}, retrying in 10s...");
            tokio::time::sleep(std::time::Duration::from_secs(10)).await;
            continue;
        }

        eprintln!("MQTT subscribed to home/metrics/#");

        loop {
            match eventloop.poll().await {
                Ok(Event::Incoming(Packet::Publish(msg))) => {
                    if let Ok(report) = serde_json::from_slice::<DeviceReport>(&msg.payload) {
                        let id = report.device_id.clone();
                        devices.write().await.insert(id, report);
                    }
                }
                Ok(_) => {}
                Err(e) => {
                    eprintln!("MQTT error: {e}, reconnecting in 5s...");
                    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                    break;
                }
            }
        }
    }
}

fn http_serve(port: u16, devices: DeviceMap) {
    let addr = format!("0.0.0.0:{port}");
    let server = tiny_http::Server::http(&addr).expect("failed to bind HTTP server");
    eprintln!("Prometheus metrics at http://{addr}/metrics");

    for request in server.incoming_requests() {
        if request.url() != "/metrics" {
            let resp = tiny_http::Response::from_string("Not Found").with_status_code(404);
            let _ = request.respond(resp);
            continue;
        }

        let body = {
            let handle = tokio::runtime::Handle::current();
            let map = handle.block_on(devices.read());
            build_prometheus(&map)
        };

        let resp = tiny_http::Response::from_string(body).with_header(
            "Content-Type: text/plain; version=0.0.4; charset=utf-8"
                .parse::<tiny_http::Header>()
                .unwrap(),
        );
        let _ = request.respond(resp);
    }
}

fn build_prometheus(devices: &HashMap<String, DeviceReport>) -> String {
    let mut out = String::new();

    // Count of known devices
    out.push_str("# HELP iot_devices_total Number of IoT devices reporting\n");
    out.push_str("# TYPE iot_devices_total gauge\n");
    out.push_str(&format!("iot_devices_total {}\n", devices.len()));

    out.push_str("# HELP iot_uptime_seconds Device uptime in seconds\n");
    out.push_str("# TYPE iot_uptime_seconds gauge\n");
    for d in devices.values() {
        out.push_str(&format!(
            "iot_uptime_seconds{{device_id=\"{}\"}} {}\n",
            d.device_id, d.uptime_secs
        ));
    }

    out.push_str("# HELP iot_firmware_info Firmware version info\n");
    out.push_str("# TYPE iot_firmware_info gauge\n");
    for d in devices.values() {
        out.push_str(&format!(
            "iot_firmware_info{{device_id=\"{}\",version=\"{}\"}} 1\n",
            d.device_id, d.firmware_version
        ));
    }

    out.push_str("# HELP iot_rssi_dbm WiFi signal strength in dBm\n");
    out.push_str("# TYPE iot_rssi_dbm gauge\n");
    for d in devices.values() {
        if let Some(rssi) = d.rssi {
            out.push_str(&format!(
                "iot_rssi_dbm{{device_id=\"{}\"}} {}\n",
                d.device_id, rssi
            ));
        }
    }

    out.push_str("# HELP iot_heap_free_bytes Free heap memory in bytes\n");
    out.push_str("# TYPE iot_heap_free_bytes gauge\n");
    for d in devices.values() {
        if let Some(heap) = d.heap_free {
            out.push_str(&format!(
                "iot_heap_free_bytes{{device_id=\"{}\"}} {}\n",
                d.device_id, heap
            ));
        }
    }

    out.push_str("# HELP iot_cpu_usage_percent CPU usage percentage\n");
    out.push_str("# TYPE iot_cpu_usage_percent gauge\n");
    for d in devices.values() {
        if let Some(cpu) = d.cpu_usage {
            out.push_str(&format!(
                "iot_cpu_usage_percent{{device_id=\"{}\"}} {:.1}\n",
                d.device_id, cpu
            ));
        }
    }

    out.push_str("# HELP iot_mem_usage_percent Memory usage percentage\n");
    out.push_str("# TYPE iot_mem_usage_percent gauge\n");
    for d in devices.values() {
        if let Some(mem) = d.mem_usage {
            out.push_str(&format!(
                "iot_mem_usage_percent{{device_id=\"{}\"}} {:.1}\n",
                d.device_id, mem
            ));
        }
    }

    out.push_str("# HELP iot_temperature_celsius Device temperature in Celsius\n");
    out.push_str("# TYPE iot_temperature_celsius gauge\n");
    for d in devices.values() {
        if let Some(temp) = d.temperature {
            out.push_str(&format!(
                "iot_temperature_celsius{{device_id=\"{}\"}} {:.1}\n",
                d.device_id, temp
            ));
        }
    }

    out
}
