# Neptune

The Elegoo Neptune 4 Pro 3D printer, and the reverse-proxy that exposes its Fluidd and Moonraker services in the cluster.

The printer is on the LAN at `192.168.1.54`. Traefik exposes the Fluidd UI at `https://neptune.kblab.me` (local-network source ranges only), and the cluster reaches Moonraker through `neptune.neptune.svc.cluster.local:7125`. Home Assistant reads it from there (`apps/home-assistant/config/packages/printing_neptune.yaml`).

## Files

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, service/endpoints, and ingress |
| `namespace.yaml` | Creates the `neptune` namespace |
| `service.yaml` | ClusterIP service plus manual endpoints for the printer's LAN IP |
| `ingress.yaml` | TLS ingress for `neptune.kblab.me`, restricted to local-network source ranges |

## The printer

| What | Value |
|------|-------|
| Board | Elegoo ZNP-K1 (Rockchip, 4 cores, 1 GB RAM, 6.9 GB eMMC), hostname `znp-k1` |
| OS | OpenNept4une (Armbian trixie, `edge-rockchip64` kernel) |
| Stack | Klipper, Moonraker, Fluidd, Mainsail, Mobileraker, OpenNept4une display connector, KAMP |
| Network | Ralink RT5370 USB Wi-Fi dongle (`rt2800usb`), MAC `00:22:F4:D9:12:3E`, onboard Ethernet unused |
| IP | `192.168.1.54`, DHCP reservation `neptune-3d-wan` in `infrastructure/dhcp/devices.yaml` |
| Wi-Fi | NetworkManager profile `BrownDooDoo` (WPA2, priority 10, see Joining Wi-Fi), fallback `Home` on BrownThunder (WPA1, priority 0). Both SSIDs are on the router's 2.4 GHz radio0. Power save is off via `/etc/NetworkManager/conf.d/zz-20-override-wifi-powersave-disable.conf` |
| Login | `ssh mks@192.168.1.54` with the workstation key. The `mks` password (needed for sudo) is in rbw as `neptune printer mks` |

The printer is often powered off, and "No route to host" from Moonraker or Home Assistant usually means just that.

## Joining Wi-Fi

Run from the workstation. The sudo password and the PSK come from rbw over stdin, so neither lands in shell history or on the printer outside NetworkManager:

```bash
{ rbw get 'neptune printer mks'; rbw get 'BrownDooDoo wifi'; } | ssh mks@192.168.1.54 'read -r pw; read -r psk; echo "$pw" | sudo -S -p "" nmcli connection add type wifi ifname wlan0 con-name BrownDooDoo ssid BrownDooDoo wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$psk" connection.autoconnect-priority 10'
```

Then reboot the printer (`curl -X POST http://192.168.1.54:7125/machine/reboot`). On the router, `iw dev phy0-ap1 station get 00:22:f4:d9:12:3e` should list the printer on BrownDooDoo; the `.54` reservation follows the MAC, so the IP does not change.

## Updating

Everything goes through Moonraker's update manager, so its view of each repo stays the source of truth. Only update while `print_stats.state` is not `printing`; Moonraker refuses otherwise.

```bash
M=http://192.168.1.54:7125
curl -s -X POST $M/machine/update/refresh >/dev/null
curl -s $M/machine/update/status | jq -r '.result.version_info | to_entries[] | "\(.key): \(.value.version // .value.package_count) -> \(.value.remote_version // "") valid=\(.value.is_valid // "n/a")"'
curl -s -X POST $M/machine/update/moonraker      # first: it restarts itself
curl -s -X POST $M/machine/update/klipper
curl -s -X POST "$M/machine/update/client?name=fluidd"   # also mainsail, mobileraker, display, Klipper-Adaptive-Meshing-Purging
curl -s -X POST $M/machine/update/system         # apt
```

The Fluidd and Mainsail "Software Updates" panels do the same thing. After a Klipper update, `printer/info` must report `ready`; a protocol mismatch error means the MCU needs reflashing with the MCU option in `~/OpenNept4une/OpenNept4une.sh`, with someone at the printer.

## Monitoring

Two Sentinel watches run on the workstation (`~/.agent/watches/`):

- `neptune-health` (30 min, high): trips when the printer answers and disk free is under 1 GB or `klippy.log` is over 200 MB. Passes while the printer is off.
- `neptune-updates` (daily, low): trips when Moonraker shows a newer Klipper or Moonraker, so updates arrive as a ping.

Up/down is covered by Gatus and Home Assistant.

## Verify

```bash
kubectl --context home-k3s -n neptune get svc,endpoints,ingress
curl -kI https://neptune.kblab.me/
curl -s http://192.168.1.54:7125/printer/info | jq .result.state
curl -s 'http://192.168.1.54:7125/server/files/directory?path=gcodes' | jq '.result.disk_usage.free / 1e9'
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| After a new Wi-Fi dongle the printer is on some other IP and `neptune.kblab.me` returns 502 | The dongle has a new MAC, so the `.54` reservation no longer matches | Read the MAC from the router (`logread \| grep DHCPACK`), update `neptune-3d-wan` in `infrastructure/dhcp/devices.yaml`, run `dhcp sync`, reboot the printer (`POST /machine/reboot`) |
| Uploads fail, the printer "stalls", Wi-Fi drops and does not rejoin | Disk full. On 2026-11-02 Klipper's log rollover looped (Python 3.13 `TimedRotatingFileHandler` returns early when the dated file exists, and Klipper re-wrote its config header on every line), and `klippy.log.2026-11-02` reached 2.27 GB | Check disk free first (Verify above). Delete the runaway log over SSH (Moonraker refuses deletes in `logs/`). Klipper from `7bc4d094 queuelogger: workaround infinite rollover printing` on fixes the loop |
| Moonraker update fails with `Git Command 'fetch origin --prune --progress' failed` after a disk-full period | Refs and loose objects written as empty files while the disk was full | On the printer, per repo: `find .git/refs .git/logs .git/objects -type f -empty -delete`, then `git fsck --connectivity-only`; for any broken link run `git fetch --refetch origin` |
| Cancel on the screen does nothing during heat-up | The cancel is queued behind the `M190`/`M109` wait | `curl -X POST http://192.168.1.54:7125/printer/print/cancel`. A "below minimum temp" error from the cancel macro's retract is harmless |
| Small pings pass but the UI hangs | Weak link: the dongle sits about -85 dBm at the router | Test with `ping -M do -s 1400`, read `iw dev phy0-ap1 station get 00:22:f4:d9:12:3e` on 192.168.1.1. A USB extension cable that moves the dongle off the frame toward the router is the cheapest fix |
