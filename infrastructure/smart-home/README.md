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
