#!/usr/bin/env bash
# Host entry point: builds the drone-sim image and runs P0 stages in it. Nothing runs on the host.
#   ./sim.sh build
#   ./sim.sh run [stage...]   default: sitl sitl-notag gazebo gazebo-notag
# Results land in $DRONE_SIM_OUT (default ~/.cache/drone-sim/out/<stage>).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
image=${DRONE_SIM_IMAGE:-drone-sim:p0}
out=${DRONE_SIM_OUT:-$HOME/.cache/drone-sim/out}

case ${1:-run} in
  build) docker build -t "$image" "$here" ;;
  run)
    shift || true
    stages=("${@:-sitl sitl-notag gazebo gazebo-notag}")
    read -r -a stages <<< "${stages[*]}"
    mkdir -p "$out"
    rc=0
    for s in "${stages[@]}"; do
      docker run --rm --name "drone-sim-$s" --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$here:/sim:ro" -v "$out:/out" "$image" \
        bash /sim/scripts/run-stage.sh "$s" || rc=1
    done
    exit $rc ;;
  *) echo "usage: $0 build | run [stage...]" >&2; exit 2 ;;
esac
