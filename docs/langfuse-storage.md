# Langfuse storage, retention, and backup

Langfuse is the LLM-observability surface for the house. Two producers write to it:

- **LiteLLM gateway** - `apps/litellm/configmap.yaml` sets `callbacks: ["prometheus", "langfuse_otel"]`, so every call through the gateway is traced.
- **Claude Code** - `~/.dotfiles/.config/shared-hooks/claude-telemetry.sh` exports OTLP to the Alloy DaemonSet, and `apps/alloy/configmap.yaml` forwards traces on to `langfuse-web:3000/api/public/otel`. That hook sets `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_DETAILS` and `OTEL_LOG_TOOL_CONTENT` to 1, so **full prompt and response text lands in this ClickHouse**. The only thing keeping that on the LAN is the `getent hosts hp-victus` guard in the hook.

Volume as of 2026-09-18: roughly 450-520 traces/day, 38.7k traces and 205k observations total since 2026-07-17.

## The 2026-09-17 disk incident

ClickHouse was using **172 GB** on asus-laptop's root nvme against a PVC that claims `20Gi`. `local-path` enforces no quota, so the claim was decorative.

Almost none of it was data:

| table | size | rows |
|---|---|---|
| `system.trace_log` | 151.00 GiB | 6,787,892,598 |
| `system.text_log` | 12.88 GiB | 63,967,937 |
| `system.metric_log` | 1.66 GiB | 5,345,457 |
| `system.asynchronous_metric_log` | 930 MiB | 1,214,334,376 |
| `default.observations` | 614 MiB | 204,844 |
| `default.traces` | 45 MiB | 38,776 |

166 GiB of ClickHouse observing itself, guarding 660 MiB of Langfuse data.

It was not free. ClickHouse is capped at 4Gi and was throwing `memory limit exceeded ... WaitForAsyncInsert` on inserts, and `langfuse-worker` had restarted **542 times** failing to flush real observations. The self-telemetry was starving the thing it was telemetry for.

**Root cause: the memory profiler, not the query profiler.** `memory_profiler_step` defaults to 4194304, which writes a stack trace to `trace_log` every 4 MiB allocated. Under Langfuse's async-insert load that produced 97% of the rows:

```
trace_type    rows (24h)
Memory        62,227,066
MemoryPeak    62,227,049
Real           1,851,433
CPU              248,757
```

Disabling only the two `query_profiler_*` settings - the obvious guess - would have left 97% of the writes in place.

## What is configured now

`apps/langfuse/helmrelease.yaml`, in the `clickhouse:` block:

- `extraOverrides` renders to `01_extra_overrides.xml` in `config.d` and removes `trace_log`, `text_log`, `metric_log`, `asynchronous_metric_log`, `latency_log`, `processors_profile_log`, `opentelemetry_span_log` and `part_log` via the `remove="1"` merge attribute, so the tables are never created. `query_log` is kept with a 7-day TTL: it is 142 MiB and it is the one that answers "why is this Langfuse query slow". Everything else those tables offered is already in Prometheus and Loki.
- `usersExtraOverrides` renders to `users.d` and zeroes `memory_profiler_step`, `memory_profiler_sample_probability`, `query_profiler_real_time_period_ns` and `query_profiler_cpu_time_period_ns`. Profile settings must go in `users.d`; putting them in `config.d` silently does nothing.

## Clearing the backlog after the config lands

Config only stops future writes. The existing parts stay until the tables are dropped, and dropping them before the rollout just means ClickHouse recreates them on the next flush. Order matters:

```bash
kubectl -n langfuse rollout status sts/langfuse-clickhouse-shard0
CH="kubectl -n langfuse exec langfuse-clickhouse-shard0-0 -- bash -c"
$CH 'clickhouse-client --user=default --password="$CLICKHOUSE_ADMIN_PASSWORD" -q "
  DROP TABLE IF EXISTS system.trace_log;
  DROP TABLE IF EXISTS system.text_log;
  DROP TABLE IF EXISTS system.metric_log;
  DROP TABLE IF EXISTS system.asynchronous_metric_log;
  DROP TABLE IF EXISTS system.latency_log;
  DROP TABLE IF EXISTS system.processors_profile_log;
  DROP TABLE IF EXISTS system.opentelemetry_span_log;
  DROP TABLE IF EXISTS system.part_log;"'
```

Verify the reclaim with `du -sh /bitnami/clickhouse/data/store` inside the pod.

## Retention and archive

The hot window is **90 days** in ClickHouse. Full history lives on the 8TB drive.

`apps/langfuse/backup-cronjob.yaml` runs daily at 03:15 on asus-laptop and writes to `/mnt/backup-8t/langfuse/`:

- `postgres/langfuse-<ts>.sql.gz` - projects, users, API keys, prompts, datasets. Without this the ClickHouse archive restores into nothing. 30 kept, with a minimum-size guard because gzip succeeds on empty input.
- `clickhouse/<table>/<YYYYMM>.native.gz` - `traces`, `observations` and `scores`, one file per month, matching the tables' own `toYYYYMM()` partition key. **Append-only**: a closed month is written once and never rewritten, which is what makes the TTL safe. The open month and the one just closed are re-exported each run.

The job asserts the 90-day TTL only after the archive is written, and re-asserts it every run so a Langfuse schema migration cannot silently drop it.

Restore a month:

```bash
gunzip -c /mnt/backup-8t/langfuse/clickhouse/traces/202607.native.gz \
  | kubectl -n langfuse exec -i langfuse-clickhouse-shard0-0 -- \
      clickhouse-client --user=default --password="$CH_PW" \
      -q "INSERT INTO default.traces FORMAT Native"
```

## Known gaps

- **Same box.** `/mnt/backup-8t` is `sda` inside asus-laptop, the same machine as the nvme holding the live PVC. Per `backup-runbook.md` there is still no offsite. This protects against a disk dying, not against losing the host.
- **S3 blobs are not covered.** Langfuse writes raw event payloads to minio (`S3_ACCESS_KEY_ID` in `langfuse-secrets`). The traces and observations are fully restorable without them; the original event bodies are not.
- **`nas-backup-verify` does not check this job.** It loops over `home-assistant litellm actual-budget` against the SMB share, and this job writes to the 8TB hostPath instead. Staleness is covered - `HomelabBackupStale` in `apps/monitoring/prometheus-rules-backup-drive.yaml` joins on any CronJob outside the `nas` namespace, so creating this CronJob brought Langfuse under it automatically - but content is not verified.
