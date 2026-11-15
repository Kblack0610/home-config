#!/bin/sh
# Runs apps/monitoring/tests/*.test.yaml with promtool. A PrometheusRule is the rule file
# wrapped in a Kubernetes object, so `.spec` is extracted first. Needs promtool and yq (yq-go).
set -eu

dir="$(cd "$(dirname "$0")/../apps/monitoring" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Every alert declares its agent route: `fleet: fix` sends it to an agent (the sentinel watch
# alertmanager-fleet reads every active fleet=fix alert and queues a red item for fix-red);
# `fleet: none  # <why>` keeps it with the human, and the line comment says why. An alert
# with neither reached no agent: TraderRunStale paged ntfy at warning for two nights
# (2026-11-12, 11-13) until a human noticed.
bad=0
for f in "$dir"/prometheus-rules-*.yaml; do
  name="${f##*/prometheus-rules-}"
  name="${name%.yaml}"
  yq '.spec' "$f" > "$tmp/$name.rules.yaml"
  missing="$(yq '.spec.groups[].rules[] | select(has("alert")) | select(.labels.fleet != "fix" and (.labels.fleet != "none" or (.labels.fleet | line_comment) == "")) | .alert' "$f")"
  for a in $missing; do
    echo "${f##*/}: $a needs fleet: fix, or fleet: none with a line comment saying why" >&2
    bad=1
  done
done
[ "$bad" = 0 ]
cp "$dir"/tests/*.test.yaml "$tmp/"

cd "$tmp"
promtool check rules --lint-fatal ./*.rules.yaml
promtool test rules ./*.test.yaml
