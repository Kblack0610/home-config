use std::sync::Arc;
use std::time::Instant;
use tokio::sync::RwLock;

use crate::config::Config;
use crate::system::SystemMetrics;

/// Serve Prometheus metrics on HTTP.
///
/// Runs a blocking tiny_http server. Call from the main thread or a dedicated thread.
pub fn serve(cfg: Config, sys_metrics: Arc<RwLock<SystemMetrics>>, start: Instant) {
    let addr = format!("0.0.0.0:{}", cfg.listen_port);
    let server = tiny_http::Server::http(&addr).expect("failed to bind HTTP server");
    eprintln!("Prometheus metrics at http://{addr}/metrics");

    for request in server.incoming_requests() {
        if request.url() != "/metrics" {
            let resp = tiny_http::Response::from_string("Not Found")
                .with_status_code(404);
            let _ = request.respond(resp);
            continue;
        }

        // Block briefly to get current metrics
        let sm = {
            let handle = tokio::runtime::Handle::current();
            handle.block_on(sys_metrics.read()).clone()
        };

        let uptime = start.elapsed().as_secs();
        let body = build_prometheus_response(&cfg, &sm, uptime);

        let resp = tiny_http::Response::from_string(body)
            .with_header(
                "Content-Type: text/plain; version=0.0.4; charset=utf-8"
                    .parse::<tiny_http::Header>()
                    .unwrap(),
            );
        let _ = request.respond(resp);
    }
}

fn build_prometheus_response(cfg: &Config, sm: &SystemMetrics, uptime: u64) -> String {
    let id = &cfg.device_id;
    let mut out = String::new();

    out.push_str(&format!(
        "# HELP pi_uptime_seconds Time since pi-monitor started\n\
         # TYPE pi_uptime_seconds gauge\n\
         pi_uptime_seconds{{device_id=\"{id}\"}} {uptime}\n"
    ));

    out.push_str(&format!(
        "# HELP pi_cpu_usage_percent CPU usage percentage\n\
         # TYPE pi_cpu_usage_percent gauge\n\
         pi_cpu_usage_percent{{device_id=\"{id}\"}} {:.1}\n",
        sm.cpu_usage
    ));

    out.push_str(&format!(
        "# HELP pi_memory_usage_percent Memory usage percentage\n\
         # TYPE pi_memory_usage_percent gauge\n\
         pi_memory_usage_percent{{device_id=\"{id}\"}} {:.1}\n",
        sm.mem_usage_percent()
    ));

    out.push_str(&format!(
        "# HELP pi_memory_total_bytes Total memory in bytes\n\
         # TYPE pi_memory_total_bytes gauge\n\
         pi_memory_total_bytes{{device_id=\"{id}\"}} {}\n",
        sm.mem_total_kb * 1024
    ));

    out.push_str(&format!(
        "# HELP pi_disk_usage_percent Root disk usage percentage\n\
         # TYPE pi_disk_usage_percent gauge\n\
         pi_disk_usage_percent{{device_id=\"{id}\"}} {:.1}\n",
        sm.disk_usage_percent()
    ));

    if let Some(temp) = sm.temperature {
        out.push_str(&format!(
            "# HELP pi_temperature_celsius CPU temperature\n\
             # TYPE pi_temperature_celsius gauge\n\
             pi_temperature_celsius{{device_id=\"{id}\"}} {:.1}\n",
            temp
        ));
    }

    out.push_str(&format!(
        "# HELP pi_load_1m 1-minute load average\n\
         # TYPE pi_load_1m gauge\n\
         pi_load_1m{{device_id=\"{id}\"}} {:.2}\n",
        sm.load_1m
    ));

    out.push_str(&format!(
        "# HELP pi_firmware_info Firmware version info\n\
         # TYPE pi_firmware_info gauge\n\
         pi_firmware_info{{device_id=\"{id}\",version=\"{}\"}} 1\n",
        cfg.firmware_version
    ));

    out
}
