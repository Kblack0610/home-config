#!/usr/bin/env python3
"""Convert a Flipper-IRDB `.ir` file into Broadlink b64 codes for Home Assistant.

Lets you control an IR device through the Broadlink RM4 **without the physical
remote** — grab the device's `.ir` from https://github.com/Lucaslhm/Flipper-IRDB
(or flippertools.net), convert here, and drop the b64 strings into a HA package
that calls `remote.send_command` (see config/packages/honeywell_fan.yaml).

Usage:
  flipper-to-broadlink.py <path-or-raw-github-url-to.ir>

Only handles `type: raw` signals (frequency + µs pulse list) — the common case
for fans/ACs/misc. For `type: parsed` (NEC/etc.) capture with broadlink-learn.sh
instead, or extend this to synthesize the waveform.

Test a code live (no remote needed):
  curl -sX POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
    -d '{"entity_id":"remote.broadlink_rm4_pro","command":"b64:...."}' \\
    https://hass.kblab.me/api/services/remote/send_command
"""
import base64, sys, urllib.request

BROADLINK_TICK = 269 / 8192  # µs → broadlink units (python-broadlink constant)


def load(src):
    if src.startswith(("http://", "https://")):
        return urllib.request.urlopen(src, timeout=15).read().decode()
    return open(src).read()


def parse(txt):
    """→ {command_name: [pulse_us, ...]} for raw signals."""
    cmds, name, is_raw = {}, None, False
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            name, is_raw = line.split(":", 1)[1].strip(), False
        elif line.startswith("type:"):
            is_raw = line.split(":", 1)[1].strip() == "raw"
        elif line.startswith("data:") and name and is_raw:
            cmds[name] = [int(x) for x in line.split(":", 1)[1].split()]
    return cmds


def first_frame(pulses, gap_us=5000):
    """One frame + its trailing inter-frame gap (Flipper captures many repeats)."""
    out = []
    for v in pulses:
        out.append(v)
        if v > gap_us:
            break
    return out


def to_broadlink_b64(pulses_us, repeat=1):
    out = bytearray([0x26, repeat & 0xFF])          # 0x26 = IR
    body = bytearray()
    for us in pulses_us:
        n = int(round(us * BROADLINK_TICK))
        if n < 256:
            body.append(n)
        else:                                        # >255 → 0x00 hi lo
            body += bytes([0, (n >> 8) & 0xFF, n & 0xFF])
    body += bytes([0x0D, 0x05])                      # terminator
    out += bytes([len(body) & 0xFF, (len(body) >> 8) & 0xFF]) + body
    return "b64:" + base64.b64encode(bytes(out)).decode()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    cmds = parse(load(sys.argv[1]))
    if not cmds:
        print("No raw signals found.")
        sys.exit(1)
    for name, pulses in cmds.items():
        print(f"{name}:")
        print("  " + to_broadlink_b64(first_frame(pulses)))
