# mqtt2prom

Prometheus exporter for the in-cluster `mosquitto` broker. Subscribes to all
topics (`#`), counts messages per topic, and decodes any JSON payloads into
per-key gauges.

Image: `ghcr.io/kpetremann/mqtt-exporter` — public, configless. The directory
keeps the legacy name `mqtt2prom` to match the existing manifests + ServiceMonitor
selector; the underlying tool is `mqtt-exporter`. If you ever need richer
per-device modeling (e.g., temperature gauges by sensor name with custom labels),
swap to `ghcr.io/hikhvar/mqtt2prometheus` and add a config.yaml ConfigMap.

## What you get

- `mqtt_message_received_total{topic="..."}` — message counters per topic
- `mqtt_<key>{topic="<topic with / as _>"}`: a gauge for each number or boolean in a JSON payload; text values are dropped (Tasmota / Home Assistant patterns)
- The agent fleet's watch payloads (dotfiles `agent-mqtt-publish`, topic `agents_<host>_watch_<name>`) therefore carry numbers and booleans: `tripped`, `stale`, `paused`, `since_epoch`, and `severity_rank` (the manifest severity, 1 low to 4 urgent), which the command-center `other` catch-all reads to warn on a low watch, and `quarantined` (true while a quarantine with an owner and an expiry is in force on a red watch), which demotes that watch from down to warn.

## Verification

```bash
kubectl --context home-k3s -n mqtt2prom get pods
kubectl --context home-k3s -n mqtt2prom port-forward svc/mqtt2prom 9000:9000 &
curl -s localhost:9000/metrics | grep -E '^mqtt_' | head -20
```

To force traffic and watch the counter increment:

```bash
# any device with mosquitto-clients on it
mosquitto_pub -h mosquitto.kblab.me -p 31883 -t test/hello -m '{"value":42}'
# then re-curl /metrics — mqtt_value{topic="test_hello"} 42 should appear
```

The notes-sync fan-out fires `notes/sync/needed` automatically on every git
push, so you'll see `mqtt_notes_sync_needed_total` (or similar topic-shaped
metric) climb without doing anything.

## ServiceMonitor

Picked up by the `kube-prometheus-stack` Prometheus instance via the cluster
operator's all-namespaces selector — same path as `litellm`, `binks-api`,
etc. No extra wiring needed. Visible in Grafana under the `mqtt_*` series.
