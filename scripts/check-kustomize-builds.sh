#!/bin/sh
# Builds what Flux builds: the `apps` aggregate and each cluster under clusters/. A manifest that does
# not build is one Flux refuses after merge. Called by the kustomize-builds job of
# .forgejo/workflows/checks.yaml and by .githooks/pre-push, so the target list has one home.
#
#   scripts/check-kustomize-builds.sh [rendered-file]
#
# Appends each build, followed by "---", to rendered-file (relative to the repo root; truncated first) for the CronJob timeZone
# step; with no argument the output is dropped. Needs `kustomize`, the build CI runs: another
# version can disagree with it. Exits 1 when a target fails to build or there is no target.
set -eu
cd "$(dirname "$0")/.."
out="${1:-/dev/null}"
command -v kustomize >/dev/null 2>&1 || { echo "FAIL: kustomize is not installed"; exit 1; }
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
rc=0
: > "$out"
targets="apps $(ls -d clusters/*/ 2>/dev/null || true)"
# An empty target list must FAIL, not pass vacuously.
[ -n "$(echo "$targets" | tr -d ' ')" ] || { echo "FAIL: no build targets found"; exit 1; }
for d in $targets; do
  if kustomize build "$d" >"$tmp/build.out" 2>"$tmp/build.err"; then
    { cat "$tmp/build.out"; echo ---; } >> "$out"
    echo "ok: $d"
  else
    echo "FAIL: kustomize build $d"
    sed 's/^/    /' "$tmp/build.err"
    rc=1
  fi
done
exit $rc
