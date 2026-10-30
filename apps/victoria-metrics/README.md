# victoria-metrics

Long-term metrics: Prometheus keeps 7d hot and remote-writes everything (minus control-plane histogram buckets) here, kept for 2y. Single-node VictoriaMetrics, PromQL-compatible.

| What | Where |
|------|-------|
| Data | local-path PVC `victoria-metrics-data` on asus-laptop's root NVMe (pinned by the deployment's nodeSelector) |
| Writer | `prometheus.prometheusSpec.remoteWrite` in `apps/monitoring/helm-values.yaml` (plain Helm: needs the `helm upgrade` in that file's header) |
| Reader | Grafana datasource "VictoriaMetrics (long)", uid `victoriametrics` (same file) |
| Backup | `victoria-metrics-backup` CronJob, daily 03:45 ET, incremental into `/mnt/backup-8t/victoria-metrics/latest` |
| Alerts | `VictoriaMetricsDiskLow` (under 150 GB free), the chart's `PrometheusRemoteWriteBehind`, and `HomelabBackupStale` for the backup |

Ad-hoc query from a workstation:

```bash
kubectl -n monitoring port-forward svc/victoria-metrics 8428 &
curl -s 'localhost:8428/api/v1/query?query=count(up)'
```

## Restore

1. Suspend Flux for apps and scale the deployment to 0, so nothing writes into `/storage`.
2. Run `vmrestore` on asus-laptop with the PVC and the backup drive mounted (same volumes as the backup CronJob): `/vmrestore-prod -src=fs:///backup/latest -storageDataPath=/storage`, image `victoriametrics/vmrestore` at the same version as the server.
3. Scale back to 1 and resume Flux. Prometheus replays what it still holds (up to 2h of WAL) on its own.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Long-range panel empty before 2026-10-29 | Remote-write started then; older data was only ever in Prometheus (7d). |
| `VictoriaMetricsDiskLow` | Lower `-retentionPeriod` in `deployment.yaml`, or add metric names to the remoteWrite drop regex. |
