# Smart-Home Outlet / Energy / AC Control — Buildout Runbook

> **Status: HARDWARE PURCHASED / not yet implemented in-cluster.** As of 2026-06-29 the
> first wave of devices is **bought** (see _Hardware inventory_ below) but **nothing has been
> wired into the cluster yet** — no MQTT config entry, no dashboard edit, no DHCP lease, no
> commit. Execute the "Repo integration" steps only once the devices physically arrive and
> are on the LAN. This doc is the buying + integration guide; later agents should treat the
> _Device compatibility verdicts_ table as the reusable "is this thing hackable/local?" lookup.

## Hardware inventory (purchased 2026-06-29)

| Qty | Device | Role | Firmware / integration |
|-----|--------|------|------------------------|
| a few | **Athom pre-flashed plug (US, ESP32-C3)** | Metered outlets / general appliances | Ships ESPHome **or** Tasmota (same HW). → MQTT → Mosquitto. Recommend keeping ESPHome + adding an `mqtt:` block so energy flows to `mqtt2prom`/Grafana. |
| 2 | **Shelly Plus Plug US** | AC + critical/high-draw loads (UL-listed, 15A) | First-party HA Shelly integration (auto-discovered) **or** flip to MQTT mode to feed Grafana. |
| 1 | **Broadlink RM4 Pro (IR + RF 433/315 MHz)** | AC control (IR) + 433 MHz devices (RF) | HA native Broadlink integration. Learn codes via HA's learn service, **not** the Broadlink app. The "Sensor Cable" bundle adds a temp/humidity probe — usable for AC automations. |

> Not bought (deliberately): Zigbee plugs (would need an Ethernet coordinator — growth path),
> Amazon-branded plugs / Govee appliances (cloud-locked dead-ends — see verdicts table).

## Goal

Local-first control + metering of household outlets/appliances, and control of the
**portable/floor AC units** (GARVEE + similar — they plug into a standard 120V outlet and
are driven by an **IR remote**), integrated with the existing Home Assistant stack. No
cloud dependencies.

## Why this fits the existing stack

The repo already has the ideal backbone, so this is additive, not new architecture:

- **`apps/mosquitto`** — MQTT broker (anonymous, `NodePort` 31883).
- **`apps/mqtt2prom`** — MQTT→Prometheus bridge (per-device power lands in Grafana for free).
- **`apps/home-assistant`** — HA with YAML-mode dashboards that **auto-populate** via
  `auto-entities`, and a config-entry-seeding pattern (Bambu/Tapo) for integrations.
- **`apps/esp32-firmware/smart-switch`** — homegrown ESP32+MQTT firmware pattern.

The cleanest path is therefore **WiFi-local devices → existing Mosquitto → HA**. We
deliberately avoid a USB Zigbee dongle to start: HA runs containerized in K3s with **no USB
passthrough**, so a USB coordinator is painful. (The clean Zigbee option — an *Ethernet*
coordinator — is in "Growth path" below, not needed for the first devices.)

---

## Do I actually need a Shelly plug AND a Broadlink?

**No specific brand is mandatory, but the two jobs are separate — buy per goal:**

| Goal | What's required | Notes / alternatives |
|------|-----------------|----------------------|
| **Control the AC** (on/off, mode, temp) | **An always-on IR blaster** — the ACs are IR-remote units; HA cannot talk to them without one. | Broadlink RM4 Pro = plug-and-play (+ does 433 MHz RF too). DIY **ESPHome IR blaster** (ESP32 + IR LED, ~$10) = fully local, on-brand with `apps/esp32-firmware`. Pick one. |
| **Meter energy / switch dumb appliances** (lamps, fans, anything with no remote) | **A smart plug** | Shelly Plus Plug US (~$20) = least friction. Athom pre-flashed ESPHome plug (~$13) or flashed Sonoff S31 = cheaper, equally local. |

Key consequences:

- **For the AC alone, the Shelly plug is NOT needed** — the IR blaster controls it. A plug
  on the AC only adds *energy measurement* + a *true hard cutoff*. It must **not** be the
  AC's primary on/off — rapid compressor power-cycling (short-cycling) damages the unit.
- **The IR blaster (in some form) IS necessary** for any automated/scheduled AC control.
  The **Flipper Zero cannot fill this role** (handheld, no scheduling, no HA integration) —
  it is a recon tool only (see below).
- **Cheapest viable start:** DIY ESPHome IR blaster (~$10) + one Athom plug (~$13).
  **Least-effort start:** Broadlink RM4 Pro + one Shelly Plus Plug US.

---

## Recommended start-small bill of materials

| Need | Device | Approx. | Why |
|------|--------|---------|-----|
| AC control (IR) — **required for AC** | **BestCon/Broadlink RM4C Mini** (IR-only) | ~$18 | Confirmed supported by HA's Broadlink integration (auto-discovered). IR-only is fine — the ACs are IR. Budget pick. |
| ↳ if you also want 433 MHz RF replay | Broadlink RM4C Pro / RM4 Pro | $30–50 | Only needed to replay RF *outlet* remotes — but plan prefers *replacing* those with smart plugs, so usually skip. |
| ↳ fully-local DIY alternative | ESPHome IR blaster (ESP32 + IR LED) | ~$10 | On-brand with `apps/esp32-firmware`; OTA-updatable. |
| AC `climate` entity | **SmartIR** custom component (or HA 2026.4 native IR) | free | Proper mode/temp/fan card instead of raw button presses. |
| Metered outlet (optional to start) | **Athom pre-flashed ESPHome plug (US)** | ~$13 | **Recommended plug.** Ships with ESPHome → native MQTT + energy, zero flashing. |
| ↳ plug alternatives | Shelly Plus Plug US (~$20, no flash) · Emporia plug → Tasmota (~$8–10, flash) · Sonoff S31 → ESPHome/Tasmota (~$10–15, serial flash) | — | All local + energy monitoring. Shelly = least effort; Emporia = cheapest. |
| Brain | Home Assistant (already running) | — | — |

> **Minimum start-small kit ≈ $31:** RM4C Mini (~$18) + one Athom plug (~$13). If you only
> want AC control and no metering, the plug is optional → ~$18.
>
> **IR blaster gotcha:** learn AC codes via **HA's Broadlink learn service**, *not* the
> BestCon/Broadlink phone app — app-learned codes are cloud-encrypted and unusable by HA.

**AC nameplate check:** most portable ACs draw < 12A, fine on a 15A plug. If a unit is
close to 15A, put it on an inline **Shelly Plus 1PM** at the outlet instead of a plug.

---

## Device compatibility verdicts (buying guide — reusable lookup)

Before buying or trying to "hack" any device, classify it. The control method follows
directly from **how the device talks** — not from the brand.

### Decision rule (apply to any new device)

1. **WiFi + ESP8266/ESP32 + Tuya/Smart-Life** → flashable to **ESPHome/Tasmota** (cloudcutter
   OTA if pre-2019 firmware, else serial), or **LocalTuya** without flashing. ✅ Fully local.
2. **WiFi + proprietary cloud** (Govee, Kasa/TP-Link, Dreo, Amazon) → **not flashable**. Local
   only if the vendor exposes a local API/integration (varies — see table). Otherwise cloud.
3. **IR remote** → control with an **IR blaster** (RM4 Pro / ESPHome IR). Stateless.
4. **RF 433 MHz remote** → **RF blaster** (RM4 Pro RF) or capture+replay via Flipper. Usually
   fixed-code = replayable. Stateless/one-way.
5. **2.4 GHz proprietary RF remote** → generally **not** replayable by Broadlink/Flipper → use
   a smart **plug** for on/off instead.
6. **Zigbee/Z-Wave** → needs a **coordinator** (use an **Ethernet** one — SLZB-06 — never USB
   on this K3s HA) + Zigbee2MQTT/ZHA. Growth path.
7. **Cloud-locked + non-ESP** (Amazon Smart Plug, Govee kettle) → **dead end.** Don't fight it;
   put it on a smart plug for crude on/off, or keep it on its own app.

> **Stateless control caveat (IR & RF):** a blaster *fires* codes but gets **no feedback**, so
> HA doesn't know the device's true state, and many remotes use *toggle/cycle* buttons (one
> "power" code for both on and off; one "speed" button that cycles). Pair with a **smart plug**
> when you need real on/off **state** (power draw) or a guaranteed hard cutoff.

### Verdicts for devices evaluated in this thread

| Device | Talks via | Local control? | Verdict / how |
|--------|-----------|----------------|---------------|
| **GARVEE / portable floor ACs** | IR remote | ✅ via blaster | Learn into **RM4 Pro (IR)** + SmartIR `climate` entity. Plug = metering/cutoff only (no compressor short-cycling). |
| **Amazon Smart Plug** (Amazon-branded) | Alexa cloud, non-ESP chip | ❌ | **Dead end** — no local API, not Tuya/ESP so not flashable. Recycle or leave on Alexa. |
| **Govee LED lights** (newer WiFi) | Govee LAN API | ✅ | HA **Govee Light Local** (no cloud/key). Limited (on/off/bright/color; some effects cloud-only). |
| **Govee BLE sensors** (thermo/hygro) | Bluetooth advert | ✅ read-only | HA native `govee_ble` — passive local read, no hack. |
| **Govee kettle / appliances** | Govee cloud (AWS IoT) | ❌ | **Cloud-only** — no LAN control. Only `govee2mqtt` via cloud API key. Treat as dumb; meter via a plug if anything. Safety: don't auto-boil unattended. |
| **Kasa HS103 (P4)** | TP-Link local (KLAP) | ⚠️ partial | Works locally via HA **TP-Link** integration, but **NO energy monitoring** (HW lacks it), and newer firmware needs TP-Link cloud creds for KLAP. Fine for plain on/off. For metering use **KP125/KP115/HS110**. |
| **Third Reality Zigbee Plug** (4-pack, metered) | Zigbee | ✅ (with coordinator) | Great plugs, but **Zigbee** → needs Ethernet **SLZB-06** + Zigbee2MQTT. = the growth path, not a WiFi drop-in. |
| **Athom plug — Tasmota vs ESPHome** | WiFi (ESP32-C3) | ✅ | **Same hardware**, only the pre-flashed firmware differs; convertible either way. **Tasmota** = web-UI config, MQTT-native, no toolchain. **ESPHome** = YAML config-as-code (commit to repo), tight HA API; add `mqtt:` to feed Grafana. Recommend **ESPHome** for this GitOps repo. |
| **WOWLUMEN UFO 71" floor lamp** | **433 MHz RF** remote; has state **memory** | ✅ | Not IR — RM4C *Mini* can't; **RM4 Pro (RF)** can (on/off+dim+timer). OR simplest: **smart plug** for on/off — memory means it returns to your preset. Flipper can pre-verify it's fixed-code. |
| **Dreo tower fan** | **IR** remote (or WiFi on "Smart"/"S" models) | ✅ | Non-smart model → **RM4 Pro (IR)**. "Smart"/WiFi model → **`hass-dreo`** HACS integration (cloud). IR is toggle/stateless → pair a plug for true on/off state. |
| **Fumoi auto litter box** ("Cat Litter Box M4") | **WiFi — Tuya** (Smart Life app, 2.4 GHz) | ✅ | Generic rebranded **Tuya** device → **LocalTuya** (`xZetsubou` fork), fully on-LAN after key extraction. Discovered 2026-06-30: proto v3.5, 7 writable DPs. See _Tuya local-control recipe_ + _LocalTuya integration_ below. **Do not flash** an appliance — LocalTuya talks to the stock firmware. |

---

## Tuya local-control recipe (reusable — for any future Tuya device)

Any device that pairs with the **Smart Life / Tuya** app can be controlled **fully locally**
via **LocalTuya** (`xZetsubou/hass-localtuya` fork) — no flashing, no cloud in the control
path. The cloud is touched once to extract the per-device `local_key`. Validated 2026-06-30
on the Fumoi litter box. Helper scripts live in the session scratchpad (`pull.py`, `poll.py`)
and are **regenerable** — keys are never printed, only written to mode-600 files.

1. **Confirm it's Tuya** — pairs with **Smart Life / Tuya** (not a brand-specific app). If it's
   a proprietary-cloud brand (Govee/Kasa/Dreo/Amazon), this recipe does **not** apply — see the
   verdicts table.
2. **Create a Tuya IoT cloud project** at `iot.tuya.com`:
   - Cloud → Development → **Create Cloud Project** → Industry/Method = **Smart Home**.
   - **Data Center = the region your app account is in** (US → `us`). Wrong region = 0 devices.
   - Note the **Client ID (Access ID)** + **Client Secret (Access Secret)**.
   - **Devices → Link Tuya App Account → Add App Account** → QR appears → in the **Smart Life
     app: Me → scan (top-right) → scan QR → confirm**. *Mandatory* — without it the cloud sees
     no devices. The linked account's **UID** then shows in that subtab.
3. **Store creds in rbw** (never paste secrets in chat). One entry holds them all:
   ```bash
   rbw add tuya-iot <CLIENT_ID>      # username = Client ID; editor first line = Client Secret
   # notes lines:  user_id: <UID>   /   region: us
   ```
4. **Pull from cloud** (`pull.py`) — env from rbw via command-substitution (no echo), run in a
   `python -m venv` (Arch is PEP-668 externally-managed):
   - `tinytuya.Cloud(apiRegion, apiKey, apiSecret).cloudrequest("/v1.0/users/{uid}/devices")`
     → `device_id` + `local_key` + per-device DP **function schema**.
   - `tinytuya.deviceScan()` → the device's **LAN IP**.
5. **Poll the device locally** (`poll.py`): `tinytuya.Device(id, ip, key)`, try protocol
   versions 3.3 / 3.4 / 3.5 → live **DP id → value** map + confirmed version.
6. **Map DPs → HA entities** and seed the LocalTuya config (next section).

**Gotchas:** re-pairing in the Smart Life app **rotates the local_key** (keep the cloud
project linked so LocalTuya auto-refreshes it). The Tuya project's free trial can expire after
~1 month (one-click free extension) — only affects *re-fetching* keys; control stays local.

## LocalTuya integration — Fumoi litter box (mirror the Tapo RV30 vacuum)

**Discovered facts** (2026-06-30): name "Cat Litter Box M4", Tuya category `msp`,
**LAN IP `192.168.1.215`** (⚠️ outside the IoT range `.80–.119` → pin a lease), **protocol
3.5**. `device_id` + `local_key` captured (durable home = a SOPS secret; transient copy in the
scratchpad `devices_full.json`, mode 600). Tuya creds in rbw entry **`tuya-iot`**.

**DP → entity map** — **corrected 2026-07-03** against the authoritative Tuya cloud device
spec (`GET /v1.0/devices/{id}/specifications` for functions/status + shadow properties for the
custom 100-range codes). The original guesses (Counter A/B, Litter Level, Bin Full) were wrong:

| DP | Tuya code (authoritative) | → entity | note |
|----|---------------------------|----------|------|
| 1 | `switch` | `switch` "Power" | reads `false` while the unit runs → likely auto-run enable, not mains — confirm live |
| 2 | `work_mode` (`auto_clean`/`manual_clean`) | `select` "Mode" | |
| 3 | `start` | `button` "Start Clean" | |
| 4 | `auto_clean` | `switch` "Auto Clean" | |
| 5 | `delay_clean_time` (1–60 min) | `number` "Clean Delay" | |
| 6 | `cat_weight` (scale 1 → raw ÷10) | `sensor` "Cat Weight" | **was mislabelled "Counter A"** |
| 7 | `excretion_times_day` (unit `times`) | `sensor` "Excretions Per Day" | **was "Counter B"** |
| 8 | `excretion_time_day` (0–600) | `sensor` "Excretion Time Daily" | **was "Litter Level" — NOT a litter level; no litter-level DP exists** |
| 9 | `manual_clean` (writable bool) | `button` "Manual Clean" | **was "Bin Full" binary_sensor — no bin-full DP exists** |
| 103 | `use_time` | `sensor` "Use Time" | newly exposed |
| 108 | `work_state` (enum) | `sensor` "Work State" | the real runtime state; enum codes unlabeled — correlate live |
| 110 | `data_identification` (enum, `Cat_into`) | `sensor` "Status" | cat-presence status |
| 23 | `factory_reset` | — | omitted (destructive) |
| 102/105/106/107/109 | `doorbell_song`/`relay_status`/`flow_set`/`bright_value`/`battery_state` | — | unmapped (whitelabel/generic codes, unclear relevance) |

**Implementation steps** (same shape as `ha-bambulab` / `tapo-rv30-ha`):

1. `apps/home-assistant/deployment.yaml` — **init container** pinning `xZetsubou/hass-localtuya`
   into `/config/custom_components` (per the no-HACS policy in `apps/home-assistant/README.md`).
2. `apps/home-assistant/localtuya-secret.sops.yaml` — **SOPS-encrypted** Secret with
   `device_id` + `local_key` + IP; wire into `kustomization.yaml`. *This is the durable home for
   the key.*
3. Seed the LocalTuya **config entry** (device + the DP→entity map above) via the Bambu/Tapo
   `.storage/core.config_entries` seeding pattern.
4. `infrastructure/dhcp/devices.yaml` — static lease (move into `.80–.119` or lock `.215`),
   following the `rv30-vacuum` entry.
5. Wall dashboard auto-populates (`switch`/`select`/`sensor`/`button` already filtered).

**✅ DONE + live-verified 2026-07-01** (PRs #72 install, #74 seed). All entities report real
device values over LAN via the HA API — each matching a direct `tinytuya` DP poll, confirming
on-LAN read (no cloud). Seed authored against `config_flow.py` @ 2025.11.0 + validated locally.

**🔧 DP labels corrected 2026-07-03.** The 2026-07-01 seed shipped with *provisional* read-only
labels that turned out wrong — HA faithfully mirrored the device, but "Litter Level 42%",
"Counter A/B" and "Bin Full" were guesses. Cross-checking the **Tuya cloud device spec** proved
DP8 = `excretion_time_day` (no litter-level DP exists), DP6 = `cat_weight`, DP9 = `manual_clean`
command (no bin-full DP), and surfaced the real `work_state`/`use_time` DPs. Re-seeded via the
version-gated `SEED_VERSION` mechanism (drops the stale entry + purges its registry rows). See the
corrected DP table above.

**Gotchas captured for future LocalTuya seeds:**
- `__init__.py` reads `region`/`client_id`/`client_secret`/`user_id` **unconditionally** → include
  them (empty) in the hub entry even for `no_cloud`, or setup `KeyError`s.
- Entry `version` must equal `ENTRIES_VERSION` (4 @ 2025.11.0); per-platform required keys:
  switch/select need `restore_on_reconnect`+`is_passive_entity`; number needs `max_value`+`step_size`;
  binary_sensor needs `state_on`; each entity needs `platform`/`id`/`friendly_name`/`entity_category`.
- **Don't trust live values as label proof** — HA mirroring the device does NOT mean the *labels*
  are right. The 2026-07-01 seed's provisional labels were wrong even though every entity read
  correctly. Verify DP semantics against the **Tuya cloud spec** (`/v1.0/devices/{id}/specifications`
  + shadow properties), not by eyeballing values.
- **Re-seeding an existing entry:** the seed skips if a `localtuya` entry already exists, so a
  corrected map needs the version gate — bump `SEED_VERSION`; the init container drops the old
  entry and purges its `core.entity_registry` rows (both `entities` + `deleted_entities`) so stale
  slugs regenerate cleanly.
- **Still TODO (needs the physical box):** DP1 `switch` reads `false` while the unit runs — confirm
  whether it's mains power or an auto-run enable. Map the DP108 `work_state` / DP110
  `data_identification` **enum codes** by toggling in Smart Life and re-polling.

---

## Repo integration (execute ONLY after hardware arrives)

All changes ship through Flux: edit → commit → push to **forgejo** → reconcile. No
`kubectl apply` on Flux-managed resources.

1. **Enable HA's MQTT integration** (not configured yet). Two distinct broker addresses —
   don't confuse them:
   - **HA → broker** (in-cluster): `mosquitto.mosquitto.svc.cluster.local:1883`.
   - **LAN WiFi devices → broker**: broker is a **NodePort** (`apps/mosquitto/service.yaml`,
     `nodePort: 31883`) → point Shelly/Broadlink/ESPHome at **`<pi-node-ip>:31883`**
     (e.g. `192.168.1.20:31883`), *not* 1883. Consider promoting Mosquitto to a MetalLB
     `LoadBalancer` on :1883 later for a stable device-facing IP.
   - Seed the MQTT config entry like Bambu/Tapo (config-flow entry from a SOPS secret), per
     `apps/home-assistant/README.md`. Broker is `allow_anonymous true` → no creds initially.
2. **Devices auto-appear** — Shelly + Tasmota/ESPHome speak HA MQTT discovery, so each plug
   shows up as `switch` + power/energy `sensor`, and the AC as `climate`, with no manual
   entity wiring.
3. **Surface on the wall dashboard** — `apps/home-assistant/config/dashboards/wall.yaml`
   Control view already auto-populates `light`/`fan`/`switch` via `auto-entities`. Add a
   `climate` domain filter (ACs) + a small "Energy" section filtering `device_class: power`.
   This is the only dashboard edit needed.
4. **Energy graphs for free** — `apps/mqtt2prom` already scrapes MQTT JSON → Prometheus, so
   per-plug watts land in Grafana automatically. Optionally enable HA's native **Energy
   dashboard** off the plug kWh sensors.
5. **Static DHCP leases** — add each device to `infrastructure/dhcp/devices.yaml` in the IoT
   range (`.80–.119`), following the existing `rv30-vacuum` entry. Broadlink needs a reserved
   IP for its HA integration.
6. **(Optional) on-brand IR blaster** — build an ESPHome IR blaster matching
   `apps/esp32-firmware` instead of Broadlink; fully local + OTA. Broadlink recommended to
   start (plug-and-play + 433 MHz); graduate to ESPHome per-room later.

### Files that will be touched (when implementing)

- `apps/home-assistant/` — seed MQTT config entry (SOPS, Bambu/Tapo pattern); add SmartIR
  custom component via init container (per README policy).
- `apps/home-assistant/config/dashboards/wall.yaml` — add `climate` + power-sensor filters.
- `apps/home-assistant/config/packages/` — optional energy package / HA Energy dashboard.
- `infrastructure/dhcp/devices.yaml` — static leases for plugs + Broadlink.
- *(growth)* `apps/zigbee2mqtt/` — new Flux app if/when an Ethernet Zigbee coordinator is added.

---

## Flipper Zero recon workflow (capture → bake in)

The Flipper decodes your existing remotes so the always-on blaster can replay them — then it
goes back in the drawer. It is **not** the control plane.

1. **Capture the AC IR remote**: Flipper → *Infrared → Learn new remote* → press each button
   (power/mode/temp±/fan). Save. Note the protocol if identified (NEC/Samsung/etc.).
2. **Capture any 433 MHz outlet remotes**: Flipper → *Sub-GHz → Read / Read RAW* (for any
   "outlet that plugs in and uses an RF remote"). Identify fixed-code protocols.
3. **Bake codes into the blaster**:
   - First try matching the AC model in the **SmartIR** database — if present, skip learning
     (just set the device code).
   - If not in the DB, *learn* the captured codes into the **Broadlink** (HA's Broadlink
     integration has a learn-command service) or an ESPHome IR blaster.
4. **Retire RF remotes**: for cheap RF-remote outlets, prefer **replacing** them with a
   Shelly/ESPHome plug (real state feedback + metering) over blindly replaying 433 MHz
   (fixed-code, no confirmation the outlet actually switched).

**"Software hack" note (only if relevant):** for any *cloud-locked Tuya/Smart Life* device you
already own, use **LocalTuya** (extract local key, control on-LAN) or flash it to
**ESPHome/Tasmota** via tuya-cloudcutter — legitimate local-control liberation of your own
hardware. Not needed for new purchases (buy Shelly/Athom and skip the hack).

---

## Growth path (later, not now)

- **Cheap bulk plugs/sensors** → add `apps/zigbee2mqtt/` (new Flux app) + an **Ethernet**
  SMLight SLZB-06 coordinator. Zigbee2MQTT publishes to the *existing* Mosquitto → HA +
  Grafana pick up devices with zero new plumbing, no USB passthrough.
- **Whole-house energy** → **IotaWatt** (open-source, fully local, native HA integration) or
  **Shelly Pro 3EM** at the panel (electrician for panel CT install). Feeds the HA Energy
  dashboard. Avoid **Sense** (cloud-dependent — fails local-first).
- **240V loads** (central AC compressor, water heater, EV) → Shelly Pro 4PM / smart breaker in
  the panel — **licensed electrician required.**

---

## Verification (run after implementing)

1. **Broker reachable from LAN**: `mosquitto_sub -h 192.168.1.20 -p 31883 -t '#' -v` from the
   workstation; power a Shelly plug and watch discovery + state topics appear.
2. **Device in HA**: Settings → Devices shows the plug/AC; the wall Control view
   auto-populates the new `switch`/`climate` cards.
3. **Switching works**: toggle the plug from HA, confirm relay + `sensor.*_power` reads draw.
4. **AC control works**: trigger the `climate` entity (cool / temp / off); confirm the unit
   responds (IR line-of-sight to the blaster).
5. **Energy → observability**: watts appear in Prometheus/Grafana via `mqtt2prom`; optionally
   in HA's Energy dashboard.
6. **No-short-cycle guard**: any AC automation using the plug for power must enforce a minimum
   off-time (≥3 min) before re-energizing.

---

## Decisions captured

- **2026-06-29** — WiFi-local + MQTT chosen over Zigbee/Z-Wave for the initial buildout.
  WHY: HA is containerized in K3s with no USB passthrough, and Mosquitto + mqtt2prom + the
  ESP32-firmware pattern already exist, so WiFi-local devices integrate with zero new
  infra. Ethernet Zigbee (SLZB-06) is the deferred growth option, not a day-one need.
- **AC control = IR blaster, not power-switching.** Portable AC compressors short-cycle if
  hard power-cycled; the plug is metering/cutoff only.
- **Flipper Zero = recon tool, not control plane.** Capture/decode remotes → bake into an
  always-on Broadlink/ESPHome blaster.
- **2026-06-29 — bought RM4 Pro (IR + RF), not the IR-only RM4C Mini.** WHY: a 433 MHz device
  surfaced (the UFO floor lamp), and more RF gadgets are likely, so one device covering IR
  (ACs/fan) + RF (lamp + future 433) beats the Mini + per-device plugs. Tradeoff accepted:
  Broadlink RF is one-way/stateless (no state feedback) — pair a plug where true state matters.
- **2026-06-29 — bought a few Athom + 2 Shelly plugs.** Athom (ESPHome/MQTT) = cheap metered
  outlets that feed `mqtt2prom`→Grafana for free; Shelly (UL-listed) reserved for the AC and
  high-draw loads. Control = blaster; metering/state = plug (complementary, not either/or).
- **Classify by transport, not brand.** See _Device compatibility verdicts_ — the control
  method falls out of how a device talks (WiFi-ESP/Tuya vs WiFi-proprietary vs IR vs 433 RF vs
  2.4 GHz RF vs Zigbee vs cloud-locked-non-ESP). Cloud-locked non-ESP (Amazon plug, Govee
  kettle) = dead end; meter via a plug at most.
- **2026-06-30 — Fumoi litter box = first integration; LocalTuya over the cloud Tuya
  integration.** WHY: it's a generic Tuya device, and LocalTuya keeps control **on-LAN** (no
  cloud dependency, the project's core goal). Mirrors the existing Tapo RV30 vacuum pattern
  (custom component + SOPS-seeded config), so no new architecture. Discovery (keys/IP/DPs) done
  via `tinytuya` — captured as the reusable _Tuya local-control recipe_ for future devices.
- **Secrets via rbw, never chat.** Tuya Client ID/Secret/UID live in rbw entry `tuya-iot`;
  `device_id`/`local_key` get sealed into a SOPS secret at implementation time. Discovery
  scripts write keys only to mode-600 files.
</content>
