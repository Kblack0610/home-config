#!/usr/bin/env bash
# Run every test for the zomboid app. Stdlib Python only, no cluster required.
#
#   apps/zomboid/tests/run.sh
#
# These cover the two scripts that can do real damage: the control API (publicly
# reachable, so auth must hold) and the idle sleeper (must never scale down a server
# that has players on it).
set -euo pipefail

cd "$(dirname "$0")/.."

failed=0
for suite in tests/test_control.py tests/test_sleeper.py tests/test_backup.sh; do
  echo "=== ${suite} ==="
  runner=python3; [[ "${suite}" == *.sh ]] && runner=bash
  if ! "${runner}" "${suite}"; then
    failed=1
  fi
  echo
done

if [[ ${failed} -ne 0 ]]; then
  echo "FAILED"
  exit 1
fi
echo "All suites passed."
