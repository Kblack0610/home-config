#!/usr/bin/env python3
"""Bulk-provision ESPHome/Athom plugs over Improv-BLE. No phone, no browser.

Usage:  PROVISION_PSK=<wifi-pass> improv_provision.py "<SSID>"
Scans for every device advertising the Improv service, then pushes WiFi creds
to each. PSK comes from the env var (kept out of argv/ps). Idempotent-ish:
already-joined plugs stop advertising, so re-running only hits the ones left.
"""
import asyncio, os, sys
from bleak import BleakScanner, BleakClient

SVC = "00467768-6228-2272-4663-277478268000"
ST  = "00467768-6228-2272-4663-277478268001"  # current state (notify)
ERR = "00467768-6228-2272-4663-277478268002"  # error state (notify)
CMD = "00467768-6228-2272-4663-277478268003"  # rpc command (write)
CAP = "00467768-6228-2272-4663-277478268005"  # capabilities (read)

SSID = sys.argv[1]
PSK  = os.environ.get("PROVISION_PSK", "")


def wifi_packet(ssid, pwd):
    data = bytes([len(ssid)]) + ssid.encode() + bytes([len(pwd)]) + pwd.encode()
    pkt = bytes([0x01, len(data)]) + data          # cmd 0x01 = SEND_WIFI_SETTINGS
    return pkt + bytes([sum(pkt) & 0xFF])           # trailing checksum


async def provision(dev):
    print(f"  → {dev.address}  {dev.name}")
    try:
        async with BleakClient(dev, timeout=25) as c:
            st = {"cur": None, "err": 0, "done": asyncio.Event()}

            def on_state(_, d):
                st["cur"] = d[0]
                if d[0] == 0x04:
                    st["done"].set()

            def on_err(_, d):
                st["err"] = d[0]
                if d[0] != 0:
                    st["done"].set()

            await c.start_notify(ST, on_state)
            await c.start_notify(ERR, on_err)
            cur = await c.read_gatt_char(ST)
            cur = cur[0] if cur else None
            print(f"      state={cur} (0x02=ready, 0x01=needs button press)")
            if cur == 0x01:
                print("      !! requires authorization — press the plug's button, then re-run")
                return False
            await c.write_gatt_char(CMD, wifi_packet(SSID, PSK), response=True)
            try:
                await asyncio.wait_for(st["done"].wait(), timeout=30)
            except asyncio.TimeoutError:
                print("      timeout waiting for result")
                return False
            if st["err"]:
                m = {3: "couldn't connect to WiFi (wrong pass/SSID/2.4GHz?)"}.get(st["err"], "")
                print(f"      ✗ error {st['err']} {m}")
                return False
            if st["cur"] == 0x04:
                print("      ✅ PROVISIONED — joining WiFi")
                return True
            print(f"      ended in state {st['cur']}")
            return False
    except Exception as e:
        print(f"      connect/provision failed: {type(e).__name__}: {e}")
        return False


async def main():
    print(f"scanning 10s for Improv plugs → SSID '{SSID}'...")
    found = {}

    def cb(dev, adv):
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if SVC in uuids or "athom" in ((adv.local_name or dev.name or "").lower()):
            found[dev.address] = dev

    s = BleakScanner(detection_callback=cb)
    await s.start(); await asyncio.sleep(10); await s.stop()
    print(f"found {len(found)} improv device(s)")
    ok = 0
    for dev in found.values():
        if await provision(dev):
            ok += 1
    print(f"\ndone: {ok}/{len(found)} provisioned")


asyncio.run(main())
