# rpi-throttle-metric

Exports the Raspberry Pi firmware's throttle flags to node-exporter as `rpi_throttled{flag,when}`, so `PiUndervoltageSinceBoot` (`apps/monitoring/prometheus-rules-node-health.yaml`) pages when a Pi's power supply sags. Bound to the `rpi_hosts` inventory group: every Pi, k3s or not.

## Why

`vcgencmd get_throttled` bits 16-19 latch from boot until the next reboot, so a dip lasting milliseconds still sets them, and a one-minute timer loses nothing. This used to live in `roles/crash-evidence`, which only runs on `netconsole_senders`, so pi5-master and the k3s worker Pis had no metric. On 2026-11-17 pi5-master logged 1360 undervoltage events in one boot, its USB link to the UPS dropped 30 times, and a bad reading after one of those drops shut down every host on the UPS (`docs/nut-ups.md`). No alert could fire for it.

## Where the metric lands

`rpi_throttle_textfile_dir`: the k3s node-exporter DaemonSet reads `/var/lib/node-exporter-textfile` (the default); the Pis outside k3s run Debian's node-exporter package, which reads `/var/lib/prometheus/node-exporter` (set on `rpi_standalone` in `ansible/inventory.yml`).

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit rpi_hosts --tags rpi-throttle --check --diff
ansible-playbook playbooks/site.yml --limit rpi_hosts --tags rpi-throttle
```

The play reads the metric back and fails a host whose file does not hold all 8 series.

```bash
# Every Pi's latch, from Prometheus
curl -s 'https://prometheus.kblab.me/api/v1/query' --data-urlencode 'query=rpi_throttled{flag="undervoltage",when="since_boot"}' | jq -r '.data.result[] | "\(.metric.instance) \(.value[1])"'
```
