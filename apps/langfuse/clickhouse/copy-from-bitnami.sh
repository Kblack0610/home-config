#!/usr/bin/env bash
# Copy Langfuse's ClickHouse data from the chart's Bitnami ClickHouse
# (langfuse-clickhouse) into langfuse-ch. One-off, for the v3 -> v4 upgrade;
# procedure in docs/langfuse-storage.md.
#
# Every run drops each table and view on the new server, re-creates it from the
# old server's own SHOW CREATE and refills it, so it is safe to re-run: once
# while v3 is live to rehearse, once more during the cutover freeze for the
# final copy. Re-creating, not just refilling, matters: the 1.5.41 bump ran
# ClickHouse migration 37 (a created_at index) between rehearsal and freeze, and
# a refill alone would have left the new server claiming 37 without it.
# Exits non-zero unless every table's row count matches.
#
# traces, observations and scores carry the 90-day TTL (docs/langfuse-storage.md,
# "Retention and archive"). The new server applies it on insert, while the old
# one still holds expired rows its TTL merges never removed, so for a table with
# a TTL only rows still more than an hour inside the window are compared. The
# expired ones are already in the monthly archive on the 8TB drive.
#
# Runs from a workstation with kubectl access; the SQL runs inside the new pod,
# which reads the old server with remote(). Both servers share the
# langfuse-secrets CLICKHOUSE_PASSWORD, and the new pod's shell substitutes it
# for @PW@ (ClickHouse has no getEnv), so no password crosses this shell.
set -euo pipefail

CTX="${KUBE_CONTEXT:-home-k3s}"
NS=langfuse
OLD_POD=langfuse-clickhouse-shard0-0
NEW_POD=langfuse-ch-0
OLD_HOST=langfuse-clickhouse.langfuse.svc.cluster.local:9000

old() { kubectl --context "$CTX" -n "$NS" exec -i "$OLD_POD" -- bash -c 'clickhouse-client --user default --password "$CLICKHOUSE_ADMIN_PASSWORD" --multiquery'; }
new() { kubectl --context "$CTX" -n "$NS" exec -i "$NEW_POD" -- bash -c 'sql=$(cat); clickhouse-client --user default --password "$CLICKHOUSE_PASSWORD" --multiquery --query "${sql//@PW@/"$CLICKHOUSE_PASSWORD"}"'; }

# Tables before views: the views select from the tables.
mapfile -t objects < <(echo "SELECT name, engine FROM system.tables WHERE database = 'default' ORDER BY engine LIKE '%View', name FORMAT TSV" | old)
[ "${#objects[@]}" -gt 0 ] || { echo "FAIL: no tables found on $OLD_POD" >&2; exit 1; }

# Drop in reverse (views first), create in order.
for ((i = ${#objects[@]} - 1; i >= 0; i--)); do
  echo "DROP TABLE IF EXISTS default.\`${objects[i]%%$'\t'*}\`" | new
done
for row in "${objects[@]}"; do
  name="${row%%$'\t'*}"
  echo "SHOW CREATE TABLE default.\`$name\` FORMAT TSVRaw" | old | new
done

rc=0
for row in "${objects[@]}"; do
  name="${row%%$'\t'*}"; engine="${row#*$'\t'}"
  case "$engine" in *View) continue ;; esac
  new <<SQL
INSERT INTO default.\`$name\` SELECT * FROM remote('$OLD_HOST', default, \`$name\`, 'default', '@PW@');
SQL
  ttl=$(echo "SELECT extract(create_table_query, ' TTL (.+?) SETTINGS ') FROM system.tables WHERE database = 'default' AND name = '$name'" | old)
  where=""; [ -n "$ttl" ] && where="WHERE $ttl > now() + INTERVAL 1 HOUR"
  # Replacing/Aggregating tables keep superseded versions until a merge, and
  # the freshly written copy merges sooner, so compare what queries see.
  final=""; case "$engine" in Replacing*|Aggregating*) final=FINAL ;; esac
  a=$(echo "SELECT count() FROM default.\`$name\` $final $where" | old)
  b=$(echo "SELECT count() FROM default.\`$name\` $final $where" | new)
  if [ "$a" = "$b" ]; then echo "ok: $name $b rows"; else echo "MISMATCH: $name old=$a new=$b"; rc=1; fi
done
exit $rc
