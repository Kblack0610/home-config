#!/bin/sh
# Runs apps/monitoring/tests/*.test.yaml with promtool. A PrometheusRule is the rule file
# wrapped in a Kubernetes object, so `.spec` is extracted first. Needs promtool and yq.
set -eu

dir="$(cd "$(dirname "$0")/../apps/monitoring" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

for name in capacity pager node-health; do
  yq '.spec' "$dir/prometheus-rules-$name.yaml" > "$tmp/$name.rules.yaml"
done
cp "$dir"/tests/*.test.yaml "$tmp/"

cd "$tmp"
promtool check rules ./*.rules.yaml
promtool test rules ./*.test.yaml
