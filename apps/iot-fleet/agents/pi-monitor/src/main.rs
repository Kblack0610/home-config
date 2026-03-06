use std::sync::Arc;
use std::time::Instant;
use tokio::sync::RwLock;

mod config;
mod metrics;
mod mqtt;
mod system;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let cfg = config::load();
    let start = Instant::now();
    let sys_metrics = Arc::new(RwLock::new(system::SystemMetrics::default()));

    // Spawn system metrics collector (updates every 5s)
    let sm = sys_metrics.clone();
    tokio::spawn(async move {
        system::collector_loop(sm).await;
    });

    // Spawn MQTT publisher (publishes every 30s)
    let sm = sys_metrics.clone();
    let cfg2 = cfg.clone();
    tokio::spawn(async move {
        mqtt::publisher_loop(cfg2, sm, start).await;
    });

    // Run HTTP server for Prometheus scraping (blocking on main thread)
    metrics::serve(cfg, sys_metrics, start);
}
