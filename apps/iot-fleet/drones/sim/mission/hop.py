#!/usr/bin/env python3
"""Hop mission: take off to 1 m, hop a short distance, come back, precision-land.

Exits 0 only when the landing is a verified precision landing: the target was
acquired (STATUSTEXT and the dataflash PL log agree) and the true touchdown point
is within --tol of the pad. Anything else, including a normal landing that happens
to be close, prints FAIL and exits 1. The last stdout line is a JSON summary.

Truth comes from SITL's SIMSTATE (simulator position, not the EKF estimate). The
pad position is given as a north/east offset from home in metres; with --sim-pad the
script places ArduPilot's simulated target (PLND_TYPE=4) there itself.
"""
import argparse
import glob
import json
import math
import os
import struct
import sys
import time

from pymavlink import mavutil

COPTER_MODES = {"GUIDED": 4, "LAND": 9}
PREARM_BIT = mavutil.mavlink.MAV_SYS_STATUS_PREARM_CHECK
# The pre-arm health bit can be set before the EKF has an absolute position, so also
# wait for the EKF to report one before arming in GUIDED.
EKF_POS_OK = (mavutil.mavlink.EKF_POS_HORIZ_ABS | mavutil.mavlink.EKF_PRED_POS_HORIZ_ABS)
EARTH_M_PER_DEG = 111319.49
# Position-only mask for SET_POSITION_TARGET_LOCAL_NED
POS_ONLY = 0b110111111000
# AC_PrecLand reports its estimator state as STATUSTEXT; the newest of these is the lock state.
PLND_STATE_TEXTS = ("PrecLand: Init Complete", "PrecLand: Init Failed", "PrecLand: Target Lost")


class Vehicle:
    def __init__(self, conn, deadline):
        self.m = conn
        self.deadline = deadline
        self.last = {}
        self.texts = []

    def pump(self, timeout=1.0):
        msg = self.m.recv_match(blocking=True, timeout=timeout)
        if msg is None:
            return
        t = msg.get_type()
        if t == "BAD_DATA":
            return
        if t == "HEARTBEAT" and msg.get_srcComponent() != 1:
            return  # only the autopilot's heartbeat, not a companion's
        self.last[t] = msg
        if t == "STATUSTEXT":
            self.texts.append(msg.text)
            print(f"  [fc] {msg.text}", flush=True)

    def wait(self, what, pred, timeout):
        end = min(time.monotonic() + timeout, self.deadline)
        while time.monotonic() < end:
            self.pump()
            if pred(self.last):
                return
        raise TimeoutError(what)

    def precland_locked(self):
        states = [t for t in self.texts if t.startswith(PLND_STATE_TEXTS)]
        return bool(states) and states[-1].startswith("PrecLand: Init Complete")

    def armed(self):
        hb = self.last.get("HEARTBEAT")
        return bool(hb and hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

    def mode(self):
        hb = self.last.get("HEARTBEAT")
        return hb.custom_mode if hb else None

    def set_mode(self, name):
        self.m.mav.set_mode_send(self.m.target_system,
                                 mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                                 COPTER_MODES[name])
        self.wait(f"mode {name}", lambda _: self.mode() == COPTER_MODES[name], 10)

    def command(self, cmd, *params):
        p = list(params) + [0] * (7 - len(params))
        self.m.mav.command_long_send(self.m.target_system, self.m.target_component, cmd, 0, *p)

    def set_param(self, name, value):
        self.m.mav.param_set_send(self.m.target_system, self.m.target_component,
                                  name.encode(), value, mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
        self.wait(f"param {name}",
                  lambda last: "PARAM_VALUE" in last and last["PARAM_VALUE"].param_id == name,
                  10)
        return self.last["PARAM_VALUE"].param_value

    def local(self):
        p = self.last["LOCAL_POSITION_NED"]
        return p.x, p.y, p.z, math.sqrt(p.vx ** 2 + p.vy ** 2 + p.vz ** 2)

    def goto(self, n, e, d, tol=0.1):
        self.m.mav.set_position_target_local_ned_send(
            0, self.m.target_system, self.m.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_NED, POS_ONLY, n, e, d, 0, 0, 0, 0, 0, 0, 0, 0)

        def there(last):
            if "LOCAL_POSITION_NED" not in last:
                return False
            x, y, z, v = self.local()
            return math.dist((x, y, z), (n, e, d)) < tol and v < 0.1

        self.wait(f"reach {n:.2f},{e:.2f},{d:.2f}", there, 30)


def ned_from(lat0, lon0, lat, lon):
    return ((lat - lat0) * EARTH_M_PER_DEG,
            (lon - lon0) * EARTH_M_PER_DEG * math.cos(math.radians(lat0)))


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def sim_pad_deg(param):
    """Where SITL actually puts the PLND_TYPE=4 target for a SIM_PLD_LAT/LON value.

    SIM_Precland::update() builds the target as int32(param * 1.0e7f) in float32
    math, so the 1e-7 degree position is rounded to float32 (up to about 0.4 m in
    lat and 1.4 m in lon here) and truncated toward zero.
    """
    return int(f32(f32(param) * f32(1.0e7))) * 1e-7


def truth(v):
    s = v.last["SIMSTATE"]
    return s.lat * 1e-7, s.lng * 1e-7


def connect(url, deadline):
    # SITL opens its TCP port a moment after the process starts; retry with backoff.
    delay = 0.5
    while True:
        try:
            m = mavutil.mavlink_connection(url, source_system=255)
            if m.wait_heartbeat(timeout=10):
                return m
        except OSError:
            pass
        if time.monotonic() + delay > deadline:
            raise TimeoutError(f"no heartbeat on {url}")
        time.sleep(delay)
        delay = min(delay * 2, 4)


def log_acquisition(log_dir, started):
    """Count PL records with TAcq=1 in the newest dataflash log written during this run."""
    logs = [p for p in glob.glob(os.path.join(log_dir, "*.BIN")) if os.path.getmtime(p) >= started]
    if not logs:
        return None, 0, 0
    path = max(logs, key=os.path.getmtime)
    from pymavlink import DFReader
    log = DFReader.DFReader_binary(path)
    total = acq = 0
    while True:
        msg = log.recv_match(type="PL")
        if msg is None:
            break
        total += 1
        acq += 1 if msg.TAcq else 0
    return path, total, acq


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--connect", default="tcp:127.0.0.1:5760")
    ap.add_argument("--alt", type=float, default=1.0, help="takeoff height, m")
    ap.add_argument("--hop", default="1.0,0.0", help="hop offset north,east from home, m")
    ap.add_argument("--pad", default="0.3,0.3", help="true pad position north,east from home, m")
    ap.add_argument("--sim-pad", action="store_true", help="place the PLND_TYPE=4 simulated target at --pad")
    ap.add_argument("--acquire-timeout", type=float, default=15,
                    help="max hover over home waiting for the precland lock before landing anyway, s")
    ap.add_argument("--tol", type=float, default=0.10, help="max touchdown error from the pad, m")
    ap.add_argument("--log-dir", default="logs")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()

    started = time.time()
    deadline = time.monotonic() + a.timeout
    hop_n, hop_e = (float(x) for x in a.hop.split(","))
    pad_n, pad_e = (float(x) for x in a.pad.split(","))
    result = {"took_off": False, "hopped": False, "returned": False, "landed": False,
              "target_found": False, "failsafe": False, "error_m": None}
    v = Vehicle(connect(a.connect, deadline), deadline)
    try:
        for msg_id in (mavutil.mavlink.MAVLINK_MSG_ID_SIMSTATE,
                       mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED,
                       mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS,
                       mavutil.mavlink.MAVLINK_MSG_ID_EKF_STATUS_REPORT):
            v.command(mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, msg_id, 100000)
        print("waiting for pre-arm checks and an EKF position", flush=True)
        v.wait("pre-arm checks and EKF position", lambda last: "SYS_STATUS" in last and "SIMSTATE" in last
               and last["SYS_STATUS"].onboard_control_sensors_health & PREARM_BIT
               and "EKF_STATUS_REPORT" in last
               and last["EKF_STATUS_REPORT"].flags & EKF_POS_OK == EKF_POS_OK, 120)
        lat0, lon0 = truth(v)

        if a.sim_pad:
            want_lat = lat0 + pad_n / EARTH_M_PER_DEG
            want_lon = lon0 + pad_e / (EARTH_M_PER_DEG * math.cos(math.radians(lat0)))
            # The stored float32 value is quantized again by SITL; use where it really is.
            got_lat = sim_pad_deg(v.set_param("SIM_PLD_LAT", want_lat))
            got_lon = sim_pad_deg(v.set_param("SIM_PLD_LON", want_lon))
            pad_n, pad_e = ned_from(lat0, lon0, got_lat, got_lon)
        result["pad_ned"] = [round(pad_n, 3), round(pad_e, 3)]
        print(f"pad at N{pad_n:+.3f} E{pad_e:+.3f} m from home", flush=True)

        v.set_mode("GUIDED")
        v.command(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 1)
        v.wait("arm", lambda _: v.armed(), 15)
        v.command(mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, a.alt)
        v.wait("takeoff", lambda last: "LOCAL_POSITION_NED" in last
               and -last["LOCAL_POSITION_NED"].z >= a.alt * 0.95, 30)
        result["took_off"] = True
        print("took off", flush=True)

        v.goto(hop_n, hop_e, -a.alt)
        result["hopped"] = True
        print("hopped", flush=True)
        v.goto(0.0, 0.0, -a.alt)
        result["returned"] = True
        print("returned over home, waiting for the precland lock", flush=True)
        # A real dock approach hovers until the target is locked. Without a lock (the
        # negative control) the vehicle still lands, and PLND_STRICT decides how.
        hover_start = time.monotonic()
        try:
            v.wait("precland lock", lambda _: v.precland_locked(), a.acquire_timeout)
            result["locked_s"] = round(time.monotonic() - hover_start, 1)
        except TimeoutError:
            result["locked_s"] = None
        print(f"lock: {result['locked_s']}, landing", flush=True)

        v.set_mode("LAND")
        v.wait("touchdown and disarm", lambda _: not v.armed(), 120)
        result["landed"] = True
        lat, lon = truth(v)
        n, e = ned_from(lat0, lon0, lat, lon)
        result["touchdown_ned"] = [round(n, 3), round(e, 3)]
        result["error_m"] = round(math.dist((n, e), (pad_n, pad_e)), 3)
    except TimeoutError as exc:
        result["timeout"] = str(exc)

    result["target_found"] = any("PrecLand: Target Found" in t for t in v.texts)
    result["failsafe"] = any("PrecLand: Failsafe" in t for t in v.texts)
    result["retries"] = sum("PrecLand: Retrying" in t for t in v.texts)
    path, pl_total, pl_acq = log_acquisition(a.log_dir, started)
    result.update(log=path, pl_records=pl_total, pl_acquired=pl_acq)

    ok = (result["landed"] and result["target_found"] and pl_acq > 0
          and result["error_m"] is not None and result["error_m"] <= a.tol)
    result["verdict"] = "PASS" if ok else "FAIL"
    print(json.dumps(result), flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
