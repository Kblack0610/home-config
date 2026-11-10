# drone sim (P0)

ArduPilot Copter SITL in a container that flies the hop mission (take off to 1 m, hop 1 m north, come back) and precision-lands on a pad 0.3 m N / 0.3 m E of home (in `sitl` SITL's target rounding moves it to about 0.49 N / -0.16 E; `hop.py` scores against where it really is). Nothing runs on the host except docker. Flux does not deploy this directory (`docs/excluded-from-flux.md`).

## Run

```bash
./sim.sh build                 # image drone-sim:p0 (ArduPilot Copter-4.7.2, Gazebo Harmonic, vision-landing-2)
./sim.sh run                   # all four stages; or name some: ./sim.sh run sitl gazebo
```

Output goes to `~/.cache/drone-sim/out/<stage>/` (override with `DRONE_SIM_OUT`): `result.json`, `hop.log`, `sitl.log`, dataflash logs under `logs/`, and for Gazebo stages `gz.log`, `relay.log`, `iris-pose.txt`. Each stage prints `STAGE <name>: OK` or `NOT OK` and `sim.sh` exits non-zero when any stage is not OK.

## Stages

| Stage | Target source | Expectation |
|-------|---------------|-------------|
| `sitl` | ArduPilot simulated target, `PLND_TYPE 4` | verdict PASS: target found, PL log shows `TAcq`, touchdown within 0.10 m of the target |
| `sitl-notag` | same, `SIM_PLD_ENABLE 0` | verdict FAIL, no target ever acquired, `PrecLand: Failsafe` taken (PLND_STRICT 1) |
| `gazebo` | Gazebo down camera -> vision-landing-2 `track_targets` -> `detector/relay.py` -> MAVLink LANDING_TARGET, `PLND_TYPE 1` | verdict PASS as for `sitl` |
| `gazebo-notag` | same, pad model removed from the running world | verdict FAIL with the failsafe, as for `sitl-notag` |

A negative control is OK only when the mission did not report success. A normal landing that happens to be near the pad is never a PASS: `mission/hop.py` requires the `PrecLand: Target Found` STATUSTEXT and PL records with `TAcq` set, and measures the error from simulator truth (SIMSTATE), not from the EKF.

## Files

- `params/sitl-hop.param`: precision landing, rangefinder, speeds; loaded after the frame defaults.
- `params/sitl-gazebo.param`: switches the Gazebo stages to `PLND_TYPE 1` and turns the simulated target off.
- `mission/hop.py`: the mission and the verdict (pymavlink). Last stdout line is the JSON result.
- `detector/relay.py`: feeds the camera's gz-transport frames to `track_targets` through a FIFO and sends LANDING_TARGET; `config.json` and the pinhole calibration sit next to it.
- `gazebo/`: world `hop_pad`, the pad (tagStandard41h12 id 0, 0.2 m black border), and `iris_downcam` (iris plus a nadir 640x480 camera, hfov 1.6 rad).
- `scripts/run-stage.sh`: starts the processes for one stage inside the container and checks the stage expectation.

## Choices

- Detector: vision-landing-2's C++ `track_targets` only. Its Python half is Python 2 with dronekit and an unpublished SmartLanding package, so a pymavlink relay replaces it. apriltag_plnd was skipped because it reads a V4L2 device (a host kernel module in a container setup) and sends its own UDP format instead of LANDING_TARGET.
- The pad is offset from home so precision landing has something to correct; SITL GPS is perfect, so a pad at home would pass with precland off.
- PLND_STRICT 1: on a lost or never-seen target, retry, then land vertically.
- After the return the vehicle hovers over home until `PrecLand: Init Complete` (cap `--acquire-timeout`, 15 s), then switches to LAND, so the descent starts with a locked estimator.
- The camera hfov is 1.6 rad, a wide lens as on most precland setups: at 1 m over home the pad sits 0.42 m off-axis and a 1.2 rad view kept it at the image edge.
- Gazebo stages run with `RNGFND1_TYPE 0` and `PLND_ALT_MIN`/`PLND_ALT_MAX` 0: the ArduPilotPlugin JSON backend sends no rangefinder, so precland uses height above home and cannot judge a sensor range.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Arming fails with `PreArm: Need Position Estimate` | the pre-arm health bit is set before the EKF has an absolute position | `hop.py` also waits for EKF_STATUS_REPORT `POS_HORIZ_ABS` and `PRED_POS_HORIZ_ABS` |
| `sitl` lands on the target but reports 0.2 m or more error | SITL builds the target as `int32(SIM_PLD_LAT * 1.0e7f)` in float32, which moves it up to about 0.4 m (lat) and 1.4 m (lon) from the param value | `hop.py` `sim_pad_deg()` repeats that rounding to get the true target |
| `track_targets` link errors for `matd_*` or `apriltag_*` | Ubuntu's libapriltag 3.3 does not export `matd`, and upstream puts `-lapriltag` before the objects | build apriltag v3.4.5 from source; pass the libs via `CMAKE_CXX_STANDARD_LIBRARIES` |
| ardupilot_gazebo cmake: `CPPZMQ::CPPZMQ not found` | missing zmq headers | `cppzmq-dev libzmq3-dev` in the image |
| aruco build fails linking `cv::imwrite` | its utils need opencv highgui | `-DBUILD_UTILS=OFF` |
| Gazebo arming fails with `PreArm: Rangefinder 1: No Data` | the JSON backend sends no rangefinder | `RNGFND1_TYPE 0` in `params/sitl-gazebo.param` |
| `gazebo` lands near the pad but `pl_acquired` is 0, STATUSTEXT repeats `Init Failed` | the tag was at the image edge and detections came with gaps over 500 ms, so the estimator never initialised | wide hfov and the hover-until-lock step in `hop.py` |
| `gazebo-notag` lands normally with no `PrecLand: Failsafe Measures` | with no rangefinder and PLND_ALT limits set, `check_if_sensor_in_range()` is false, so a never-seen target reads as out of range and the vehicle just descends | `PLND_ALT_MIN 0`, `PLND_ALT_MAX 0` in `params/sitl-gazebo.param` |
| relay exits with ConnectionRefused at start | SITL was not listening yet | `relay.py` `connect()` retries with backoff |
| a stage container never exits after the result | `run-stage.sh` is PID 1 and `wait` blocked on `track_targets`, which ignores SIGTERM | cleanup kills the children and does not wait |
