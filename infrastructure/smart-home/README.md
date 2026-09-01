# Smart-home provisioning tools

Host-side helpers for bringing WiFi smart-home devices onto the LAN. See
[`docs/smart-home-control.md`](../../docs/smart-home-control.md) for the full buildout runbook.

## `improv-provision.py` — bulk Improv-BLE WiFi provisioning

Provisions ESPHome/Athom plugs onto WiFi over **Improv-BLE** — no phone, no browser, no
per-device captive portal. Scans for every device advertising the Improv service and pushes
the same WiFi credentials to each. Ideal for onboarding a batch of Athom Smart Plug V3s (whose
setup AP is **Improv-only** — it serves no web page, so `192.168.4.1` provisioning does NOT work).

### Why BLE (not the WiFi AP)
Athom V3 (ESP32-C3) exposes only Improv-BLE in setup mode. The catch: while the plug hosts its
setup state its BLE shares the radio with WiFi, so **the connection is unstable at range** —
provision with the plug **within ~1 m of the host's Bluetooth adapter**, then redeploy it to its
final outlet (WiFi creds persist in flash). Provision-at-desk, deploy-anywhere.

### Usage
```bash
python3 -m venv /tmp/improv-venv
/tmp/improv-venv/bin/pip install bleak
PROVISION_PSK="$(rbw get 'BrownDooDoo wifi')" \
  /tmp/improv-venv/bin/python infrastructure/smart-home/improv-provision.py "BrownDooDoo"
```
- SSID is the argument; the PSK comes from the `PROVISION_PSK` env var (kept out of `argv`/`ps`).
  Pull it from rbw as shown — never hard-code it.
- Prints `✅ PROVISIONED` per plug. Already-joined plugs stop advertising, so re-running only
  hits the ones still in setup mode.
- If a plug reports it needs a button press (Improv state `0x01`), press its button and re-run.

### After provisioning
Each plug joins WiFi and appears via ESPHome mDNS. Then: add it to HA (config-flow or the
`seed-esphome` init container in `apps/home-assistant/deployment.yaml`), and pin its DHCP lease
in `infrastructure/dhcp/devices.yaml`. It auto-populates the plug dashboards (`switch.*plug*`).

## `broadlink-learn.sh` — guided IR/RF remote learning

Teaches the Broadlink RM4 the buttons on a physical remote, one at a time (you press, it
captures). Use when you HAVE the remote. Reusable for any IR (or 433 RF) device.
```bash
./broadlink-learn.sh dreo_tower_fan ir power speed_up speed_down oscillate
./broadlink-learn.sh ufo_lamp       rf power
```
Codes land in HA's `.storage`; then wrap them in a package that calls `remote.send_command`.

## `flipper-to-broadlink.py` — control an IR device WITHOUT its remote

When the remote is **lost**, grab the device's `.ir` from [Flipper-IRDB](https://github.com/Lucaslhm/Flipper-IRDB)
(or [flippertools.net](https://search.flippertools.net/)) and convert it to Broadlink b64 codes:
```bash
python3 infrastructure/smart-home/flipper-to-broadlink.py \
  https://raw.githubusercontent.com/Lucaslhm/Flipper-IRDB/main/Fans/Honeywell/Honeywell_Fan_HYF290B.ir
```
Drop the b64 strings into a HA package that calls `remote.send_command` (worked example:
`apps/home-assistant/config/packages/honeywell_fan.yaml` — a Honeywell QuietSet whose remote
was lost, controlled entirely from database codes). Codes are often shared across a product
series (the HYF290B codes drive an HYF260). A Flipper, if you have one, can pre-validate a code
on the device before you bake it in — but isn't required.
