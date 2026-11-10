#!/usr/bin/env bash
# Runs one P0 stage inside the drone-sim container and exits 0 when the stage's
# expectation holds. Output (dataflash logs, process logs, result.json) goes to /out/<stage>.
#   sitl          ArduPilot simulated target (PLND_TYPE=4); expects a precision landing
#   sitl-notag    same, simulated target off; expects FAIL with PLND_STRICT failsafe
#   gazebo        Gazebo camera + vision-landing-2 detector (PLND_TYPE=1); expects a precision landing
#   gazebo-notag  same, pad removed from the world; expects FAIL with PLND_STRICT failsafe
set -euo pipefail

stage=${1:?usage: run-stage.sh sitl|sitl-notag|gazebo|gazebo-notag}
out=/out/$stage
rm -rf "$out" && mkdir -p "$out" && cd "$out"
home=-35.363262,149.165237,584,0
pad=0.3,0.3
pids=()
# No wait here: this script is the container's PID 1, so its exit kills every process
# left (track_targets ignores SIGTERM, and waiting on it hung a run for 23 hours).
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

case $stage in
  sitl|sitl-notag)
    defaults=/opt/copter.parm,/sim/params/sitl-hop.param
    if [[ $stage == sitl-notag ]]; then
      echo "SIM_PLD_ENABLE 0" > notag.param
      defaults+=,$out/notag.param
    fi
    arducopter -S --model quad --speedup 1 -I0 --home "$home" --defaults "$defaults" > sitl.log 2>&1 &
    pids+=($!)
    hop_args=(--sim-pad --pad "$pad")
    ;;
  gazebo|gazebo-notag)
    gz sim -s -r --headless-rendering /sim/gazebo/worlds/hop_pad.sdf > gz.log 2>&1 &
    pids+=($!)
    arducopter -S --model JSON --speedup 1 -I0 --home "$home" \
      --defaults /opt/gazebo-iris.parm,/sim/params/sitl-hop.param,/sim/params/sitl-gazebo.param > sitl.log 2>&1 &
    pids+=($!)
    python3 /sim/detector/relay.py > relay.log 2>&1 &
    pids+=($!)
    if [[ $stage == gazebo-notag ]]; then
      # gz service waits for the world to come up (its own timeout is the guard).
      gz service -s /world/hop_pad/remove --reqtype gz.msgs.Entity --reptype gz.msgs.Boolean \
        --timeout 60000 --req 'name: "apriltag_pad" type: MODEL' | tee remove.log
      grep -q 'data: true' remove.log
    fi
    hop_args=(--pad "$pad")
    ;;
  *) echo "unknown stage $stage" >&2; exit 2 ;;
esac

set +e
python3 /sim/mission/hop.py --log-dir "$out/logs" "${hop_args[@]}" | tee hop.log
set -e
tail -n 1 hop.log > result.json
if [[ $stage == gazebo* ]]; then
  gz model -m iris -p > iris-pose.txt 2>&1 || true
fi

# A negative control passes only when the flight did NOT report success, the target was
# never acquired, and ArduPilot took its PLND_STRICT failsafe path.
python3 - "$stage" result.json <<'PY'
import json, sys
stage, r = sys.argv[1], json.load(open(sys.argv[2]))
if stage.endswith("notag"):
    ok = r["verdict"] == "FAIL" and not r["target_found"] and r["pl_acquired"] == 0 and r["failsafe"]
else:
    ok = r["verdict"] == "PASS"
print(f"STAGE {stage}: {'OK' if ok else 'NOT OK'} (verdict={r['verdict']} error_m={r['error_m']} "
      f"target_found={r['target_found']} pl_acquired={r['pl_acquired']} failsafe={r['failsafe']})")
sys.exit(0 if ok else 1)
PY
