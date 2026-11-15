#!/bin/sh
# Runs apps/monitoring/tests/*.test.yaml with promtool. A PrometheusRule is the rule file
# wrapped in a Kubernetes object, so `.spec` is extracted first. Needs promtool and yq (yq-go).
set -eu

apps="$(cd "$(dirname "$0")/../apps" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Every alert declares its agent route: `fleet: fix` sends it to an agent (the sentinel watch
# alertmanager-fleet reads every active fleet=fix alert and queues a red item for fix-red);
# `fleet: none  # <why>` keeps it with the human, and the line comment says why. An alert
# with neither reached no agent: TraderRunStale paged ntfy at warning for two nights
# (2026-11-12, 11-13) until a human noticed.
bad=0
check_routes() { # <file> <yq path to the rule groups>
  missing="$(yq "$2 | .groups[].rules[] | select(has(\"alert\")) | select(.labels.fleet != \"fix\" and (.labels.fleet != \"none\" or (.labels.fleet | line_comment) == \"\")) | .alert" "$1")"
  for a in $missing; do
    echo "${1#"$apps"/}: $a needs fleet: fix, or fleet: none with a line comment saying why" >&2
    bad=1
  done
}
# Each extracts to <name>.rules.yaml, the name the tests' rule_files use: prometheus-rules-<name>.yaml,
# or the app directory for a plain prometheus-rules.yaml.
for f in "$apps"/*/prometheus-rules*.yaml; do
  case "${f##*/}" in
    prometheus-rules-*) name="${f##*/prometheus-rules-}"; name="${name%.yaml}" ;;
    *) name="${f%/*}"; name="${name##*/}" ;;
  esac
  [ ! -e "$tmp/$name.rules.yaml" ] || { echo "two rule files extract to $name.rules.yaml" >&2; exit 1; }
  yq '.spec' "$f" > "$tmp/$name.rules.yaml"
  check_routes "$f" '.spec'
done
# The Loki ruler's alerts (LogQL, so not for promtool) sit in a ConfigMap and page through
# the same Alertmanager.
for f in "$apps"/loki/rules-*.yaml; do
  check_routes "$f" '.data[] | from_yaml'
done
[ "$bad" = 0 ]
cp "$apps"/monitoring/tests/*.test.yaml "$tmp/"

cd "$tmp"
promtool check rules --lint-fatal ./*.rules.yaml
promtool test rules ./*.test.yaml
