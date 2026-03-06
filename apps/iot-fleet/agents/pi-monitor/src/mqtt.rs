use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

use device_config::metrics_topic;
use device_metrics::DeviceMetrics;
use rumqttc::{AsyncClient, MqttOptions, QoS};

use crate::config::Config;
use crate::system::SystemMetrics;

pub async fn publisher_loop(cfg: Config, sys_metrics: Arc<RwLock<SystemMetrics>>, start: Instant) {
    let topic = metrics_topic(&cfg.device_id);

    loop {
        match connect(&cfg).await {
            Ok((client, mut eventloop)) => {
                eprintln!("MQTT connected to {}:{}", cfg.mqtt_host, cfg.mqtt_port);

                // Spawn eventloop driver
                let driver = tokio::spawn(async move {
                    loop {
                        if eventloop.poll().await.is_err() {
                            break;
                        }
                    }
                });

                loop {
                    tokio::time::sleep(Duration::from_secs(cfg.publish_interval_secs)).await;

                    let sm = sys_metrics.read().await.clone();
                    let uptime = start.elapsed().as_secs();

                    let metrics = DeviceMetrics {
                        device_id: &cfg.device_id,
                        firmware_version: &cfg.firmware_version,
                        uptime_secs: uptime,
                        rssi: None,
                        heap_free: None,
                        cpu_usage: Some(sm.cpu_usage),
                        mem_usage: Some(sm.mem_usage_percent()),
                        temperature: sm.temperature,
                    };

                    let json = metrics.to_json();
                    if client
                        .publish(topic.as_str(), QoS::AtMostOnce, false, json.as_slice())
                        .await
                        .is_err()
                    {
                        eprintln!("MQTT publish failed, reconnecting...");
                        break;
                    }
                }

                driver.abort();
            }
            Err(e) => {
                eprintln!("MQTT connection failed: {e}, retrying in 10s...");
            }
        }

        tokio::time::sleep(Duration::from_secs(10)).await;
    }
}

async fn connect(cfg: &Config) -> Result<(AsyncClient, rumqttc::EventLoop), rumqttc::ClientError> {
    let mut opts = MqttOptions::new(&cfg.device_id, &cfg.mqtt_host, cfg.mqtt_port);
    opts.set_keep_alive(Duration::from_secs(30));

    let (client, eventloop) = AsyncClient::new(opts, 10);
    Ok((client, eventloop))
}
