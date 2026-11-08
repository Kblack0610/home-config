#!/usr/bin/env python3
"""Run vision-landing-2's track_targets on the Gazebo down camera and send its detections
to ArduPilot as MAVLink LANDING_TARGET (PLND_TYPE=1).

Frames come straight off the gz-transport image topic and are written raw into a FIFO
that track_targets reads through GStreamer rawvideoparse. That skips the H264 encode and
decode and the enable-streaming call that ardupilot_gazebo's GstCameraPlugin needs.

vision-landing-2 ships two halves: track_targets (C++, the AprilTag detector) and
vision_landing (Python 2 + dronekit + an unpublished SmartLanding package). Only the
detector is reused; this relay replaces the Python 2 half with pymavlink.

track_targets prints "target:<id>:<x>:<y>:<z>:..." with the landing point in the
camera frame (x right, y down, z out of the lens, metres). The camera is mounted
looking down with the top of the image toward the nose, so image right is body
right and image down is body back. ArduPilot's MAVLink backend reads angle_x as
the bearing to the right and angle_y as the bearing toward the image bottom.
"""
import argparse
import math
import os
import signal
import subprocess
import sys
import threading
import time

from pymavlink import mavutil

GST_INPUT = ("filesrc location={fifo} ! rawvideoparse width={w} height={h} format=rgb framerate=15/1 "
             "! videoconvert ! video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false")


def heartbeat(m, stop):
    while not stop.wait(1.0):
        m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                             mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)


def feed_frames(topic, fifo, w, h):
    """Copy every gz image into the FIFO. Blocks in open() until track_targets opens it."""
    from gz.msgs10.image_pb2 import Image
    from gz.transport13 import Node

    out = open(fifo, "wb", buffering=0)
    size = w * h * 3

    def on_image(msg):
        if len(msg.data) == size:
            out.write(msg.data)

    node = Node()
    if not node.subscribe(Image, topic, on_image):
        sys.exit(f"relay: cannot subscribe to {topic}")
    return node  # keep the subscription alive


def connect(url):
    # SITL opens its TCP ports a moment after the process starts; retry with backoff.
    delay = 0.5
    for _ in range(20):
        try:
            m = mavutil.mavlink_connection(url, source_system=1,
                                           source_component=mavutil.mavlink.MAV_COMP_ID_ONBOARD_COMPUTER)
            if m.wait_heartbeat(timeout=10):
                return m
        except OSError:
            pass
        time.sleep(delay)
        delay = min(delay * 2, 4)
    sys.exit(f"relay: no heartbeat on {url}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mavlink", default="tcp:127.0.0.1:5762", help="autopilot link (a spare SITL serial port)")
    ap.add_argument("--image-topic", default="/downcam/image", help="gz camera topic (RGB_INT8)")
    ap.add_argument("--fifo", default="/tmp/downcam.rgb")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--calibration", default=os.path.join(here, "gazebo-downcam-640x480.yml"))
    ap.add_argument("--markersize", default="0.2", help="tag size, m (per-tag sizes live in config.json)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    a = ap.parse_args()

    m = connect(a.mavlink)
    stop = threading.Event()
    threading.Thread(target=heartbeat, args=(m, stop), daemon=True).start()

    # config.json (tag ids, sizes, offsets) is read from the working directory.
    if not os.path.exists(a.fifo):
        os.mkfifo(a.fifo)
    cmd = ["track_targets", "--verbose", "--width", str(a.width), "--height", str(a.height),
           GST_INPUT.format(fifo=a.fifo, w=a.width, h=a.height), a.calibration, a.markersize]
    proc = subprocess.Popen(cmd, cwd=here, stdout=subprocess.PIPE, text=True, bufsize=1)
    node = feed_frames(a.image_topic, a.fifo, a.width, a.height)  # noqa: F841

    sent = 0
    boot = time.monotonic()
    for line in proc.stdout:
        line = line.strip()
        if "initcomp" in line:
            os.kill(proc.pid, signal.SIGUSR1)  # track_targets idles until told to start
            print("relay: detector running", flush=True)
            continue
        if not line.startswith("target:"):
            if line and not line.startswith("Markers:"):
                print(f"relay: {line}", flush=True)
            continue
        f = line.split(":")
        tag_id, x, y, z = int(f[1]), float(f[2]), float(f[3]), float(f[4])
        if z <= 0:
            continue
        dist = math.sqrt(x * x + y * y + z * z)
        m.mav.landing_target_send(
            int((time.monotonic() - boot) * 1e6), 0, mavutil.mavlink.MAV_FRAME_BODY_FRD,
            math.atan2(x, z), math.atan2(y, z), dist, 0, 0)
        sent += 1
        if sent % 50 == 1:
            print(f"relay: tag {tag_id} x={x:+.3f} y={y:+.3f} z={z:.3f} (sent {sent})", flush=True)
    stop.set()
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()
