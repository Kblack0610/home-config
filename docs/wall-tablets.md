# Wall Tablets - Complete Reference (FreeKiosk, MQTT, Voice)

The canonical reference for the mounted Home Assistant wall-panel tablets: what they
are, the hard requirements, how each control plane works, how to provision and update
them, and how to run the Binks voice satellite on them.

- Hands-on per-tablet runbook (dashboard/kiosk/plug-settings steps): [`wall-panels.md`](./wall-panels.md)
- Device-fabric overview (MDM planes, fleet liveness, decisions): [`device-fabric.md`](./device-fabric.md)
- Voice pipeline (Binks stages, server-side): [`home-assistant-voice.md`](./home-assistant-voice.md)
- Operating skill (reach/reload/provision a tablet): `wall-tablet-ops`

---

## 1. Architecture - three control planes

Each tablet is a stock-Android Lenovo Tab running three cooperating layers:

| Plane | What it owns | Reached by |
|-------|--------------|-----------|
| **Headwind MDM** (`com.hmdm.launcher`) | Device Owner. Enrollment (Google-free QR), allow-listed apps, its own launcher, OTA fleet management, liveness heartbeats. | `mdm.kblab.me` (LAN/Tailscale) + its own MQTT |
| **FreeKiosk** (`com.freekiosk`, MIT) | The kiosk: full-screen lock-task WebView pointed at the HA dashboard, keep-awake, screensaver, and a REST + MQTT runtime-control channel. | ADB, FreeKiosk REST (`:8080`), FreeKiosk MQTT |
| **Home Assistant** (`hass.kblab.me`) | The dashboard content (`wall.yaml`), the voice-satellite integration + Binks Assist pipeline, per-plug settings. | The WebView loads `https://hass.kblab.me/wall-panels` |

Data/health flow: `apps/headwind-fleet-bridge` polls Headwind's `devices` table every 60s
and pushes `android_<device>` heartbeats to `apps/gatus-fleet` so tablets appear in the
fleet health surface.

> **Device Owner is Headwind, not FreeKiosk.** `dpm list-owners` returns
> `com.hmdm.launcher/.AdminReceiver`. FreeKiosk pins itself via lock-task while it is the
> foreground app; when FreeKiosk is not foreground, `mLockTaskModeState` reads `NONE` and
> Headwind's launcher (its allow-listed apps only) takes over.

---

## 2. Fleet inventory (verified 2026-07-17)

Wireless ADB (`:5555`) enabled on all. No static DHCP leases yet -> dynamic IPs can change
on lease renewal; re-discover from the OpenWRT lease table or `adb devices` if a connect
fails.

Four tablets: one M9 (office) + three M11 (living room / bedroom / kitchen). The three M11s
are interchangeable; which IP is which room is set from the dashboard (Admin -> Voice Room),
not hard-wired - so exact IP<->room is deliberately not pinned here.

| IP | adb model | Device | Room | FreeKiosk | Voice-capable |
|----|-----------|--------|------|-----------|---------------|
| `192.168.1.235:5555` | TB305FU | Tab M9 | Office | 1.2.20 | yes |
| `192.168.1.181:5555` | TB330FU | Tab M11 | set via admin | 1.2.20 | yes |
| `192.168.1.234:5555` | TB336FU | Tab M11 (TB336 variant) | set via admin | 1.2.20 | yes |
| `192.168.1.60:5555`  | TB330FU | Tab M11 | set via admin | 1.2.20 | yes |

> `192.168.1.249` sometimes appears in `adb devices` / the lease table but is a **stale/phantom
> DHCP entry**, not a real tablet - ignore it.

All are MediaTek-based -> some quirks (see Troubleshooting): `light.sensor.service` uses the
front camera as a flicker sensor, and the audio HAL uses the AIDL config (no legacy
`/proc/asound` entries).

---

## 3. Hard requirements

These are the non-negotiables. Miss one and the panel or voice silently fails.

| Requirement | Value | Why / failure mode if wrong |
|-------------|-------|-----------------------------|
| **FreeKiosk version (voice)** | **>= 1.2.20-beta.4** | <= 1.2.19 lacks `MODIFY_AUDIO_SETTINGS` -> mic dead (`NotReadableError`). See Section 6. |
| **FreeKiosk config PIN** | **`4321`** | Set by `wall-panel-provision.sh` (orchestrator). A WRONG pin makes every `am start` config intent a silent no-op. NOT the raw script default `1234`. |
| **Dashboard URL scheme** | **HTTPS** (`https://hass.kblab.me/...`) | `getUserMedia` (voice) is silently blocked on http/mixed-content; needs a trusted secure context. We have a trusted LE cert. |
| **OS mic permission** | `RECORD_AUDIO` granted to `com.freekiosk` | Runtime permission; provisioner grants it. Without it: `NotAllowedError`. |
| **Manifest audio perm** | `MODIFY_AUDIO_SETTINGS` declared by FreeKiosk | Only present in >= 1.2.20. NOT `pm grant`-able (manifest perm). |
| **Screen stays on** | keep-awake + max timeout, screensaver never true-screen-off | A real screen-off tears down the mic stream; wake word goes deaf. |
| **Accounts/SIM (for Device-Owner enroll)** | none | Device-Owner provisioning fails if any account or SIM is present. |

---

## 4. Provisioning a tablet (fresh -> working panel)

Human prereqs ADB cannot bootstrap:

1. **Headwind enroll:** factory reset -> tap the first setup screen 6-7x -> scan the
   Headwind QR (admin -> Configurations -> QR) -> SKIP Google. Installs as Device Owner.
2. **Developer options + USB debugging** on; plug in USB; tap Allow (RSA).

Then one command does the rest (`~/.dotfiles/.local/src/android-suite/wall-panel-provision.sh`,
which wraps `home-config/scripts/provision-wall-tablet.sh`):

```bash
wall-panel-provision.sh --serial <usb-serial> --name wall-<room> --device m9|m11
# debloat -> FreeKiosk install(1.2.20-beta.4)+config -> OS kiosk tuning -> grants
# -> set-as-home -> disable lockscreen -> wireless ADB. PIN defaults to 4321.
```

It persists the FreeKiosk REST key to `~/.config/wall-tablets/<name>.rest-key` (the key is
set-once on FreeKiosk; losing it locks you out of the control channel).

Unavoidable manual tail (printed at the end):
- HA login once in the FreeKiosk WebView (session persists).
- Assign the voice satellite (Section 6).
- Reboot-proof Wireless Debugging pairing (Section 7).
- Change the PIN if desired.

---

## 5. FreeKiosk (the kiosk app)

### 5.1 Config via ADB intent (mind the PIN)

FreeKiosk takes its entire config as `am start` intent extras. **Every config intent must
carry the correct `--es pin 4321`; a wrong PIN is silently rejected and nothing applies.**
This looks exactly like "the kiosk is locked and won't reconfigure" - it is not; it is auth
rejection. Config applies on `onCreate` (cold start), so force-stop then start with extras:

```bash
S=192.168.1.235:5555
adb -s $S shell am force-stop com.freekiosk; sleep 2
adb -s $S shell am start -n com.freekiosk/.MainActivity \
  --es pin 4321 --es url "https://hass.kblab.me/wall-panels" \
  --ez kiosk_enabled true --es auto_relaunch true
```

Common extras: `pin`, `url`, `kiosk_enabled`, `auto_launch`, `auto_relaunch`, `auto_start`,
`screensaver_enabled`, `rest_api_enabled`/`rest_api_port`/`rest_api_key`, and the `mqtt_*`
set. Full list: FreeKiosk `docs/adb-configuration.md` upstream.

### 5.2 REST control channel (`:8080`) - reboot-proof

When enabled at provision time, FreeKiosk serves a REST API that **auto-starts on boot** and
survives reboot (unlike wireless ADB). Auth header: `X-Api-Key: <key>` (recover the key from
`~/.config/wall-tablets/<name>.rest-key`). This is the preferred routine-control channel.

```bash
KEY=$(grep rest_key ~/.config/wall-tablets/wall-office.rest-key | cut -d= -f2)
curl -H "X-Api-Key: $KEY" http://<ip>:8080/api/status
curl -H "X-Api-Key: $KEY" -X POST http://<ip>:8080/api/url  -d '{"url":"..."}'
curl -H "X-Api-Key: $KEY" -X POST http://<ip>:8080/api/js   -d '{"code":"location.reload()"}'
```

Endpoints (40+): `/api/status /api/info /api/health /api/url /api/js /api/reload
/api/screenshot /api/screen(/on|/off) /api/screensaver(/on|/off) /api/brightness /api/volume
/api/tts /api/audio(/beep|/play|/stop) /api/wake /api/reboot /api/battery /api/wifi
/api/remote/*`. **The REST key is SET-ONCE** - it cannot be changed via ADB intent or any
endpoint after initial setup; a lost key permanently locks the channel (hence we persist it).

### 5.3 MQTT control channel

With `--mqtt-broker mosquitto.kblab.me` at provision time, FreeKiosk publishes ~42 entities
via HA MQTT discovery (screen, brightness, reload, TTS, current URL, battery, ...) under
`homeassistant/.../<device-name>`. Lets HA automate the panels (night dim, force-reload after
a dashboard deploy, announce via TTS) without ADB. Broker: `apps/mosquitto` (`mosquitto.kblab.me`).

### 5.4 Updating FreeKiosk across the fleet

FreeKiosk releases are signed with a stable key, so `adb install -r` updates **in place** -
keeps config, session, and the satellite assignment, no uninstall. Roll a new version:

```bash
APK=~/.dotfiles/.local/src/android-suite/apks/freekiosk.apk   # keep this at the pinned version
for S in 192.168.1.181:5555 192.168.1.234:5555 192.168.1.235:5555 192.168.1.60:5555; do
  adb -s $S install -r -g "$APK"
  adb -s $S shell dumpsys package com.freekiosk | grep -m1 versionName
  adb -s $S shell am start -n com.freekiosk/.MainActivity --es pin 4321 --es url https://hass.kblab.me/wall-panels
done
```

Get the latest APK from `https://github.com/RushB-fr/freekiosk/releases` (GitHub API for the
asset URL: `/repos/RushB-fr/freekiosk/releases/tags/<tag>`).

---

## 6. Voice satellite (Binks) - the complete picture

The panel doubles as an always-listening voice satellite: in-browser microWakeWord ->
the Binks Assist pipeline (litellm whisper STT -> gemini -> Google TTS). Server side is all
code-first (`apps/home-assistant`, the `voice_satellite` integration + `openwakeword`); see
`home-assistant-voice.md` for the pipeline. This section is the tablet side.

### 6.1 Requirements (all of these, or the mic is silent)

1. **FreeKiosk >= 1.2.20-beta.4** (declares `MODIFY_AUDIO_SETTINGS`). This is the load-bearing one.
2. `RECORD_AUDIO` granted to `com.freekiosk` (provisioner does it).
3. HTTPS dashboard URL (secure context).
4. Screen kept on; no true screen-off / navigating screensaver.
5. The browser assigned to a satellite entity in HA (Section 6.4).

### 6.2 Why 1.2.20 (root cause, so nobody re-debugs it)

Chromium's WebView requires the **host app** to declare BOTH `RECORD_AUDIO` and
`MODIFY_AUDIO_SETTINGS` before it will select a microphone input device
(`AudioManager.setMode(MODE_IN_COMMUNICATION)` needs the latter). FreeKiosk **<= 1.2.19
declares only `RECORD_AUDIO`**. Result: the permission prompt is granted, but device
selection fails and `getUserMedia` rejects with:

```
NotReadableError: Could not start audio source
# logcat, inside the FreeKiosk WebView:
W cr_media: Requires MODIFY_AUDIO_SETTINGS and RECORD_AUDIO. No audio device will be available
E chromium: [ERROR:audio_manager_android.cc] Unable to select audio device!
```

`MODIFY_AUDIO_SETTINGS` is a **manifest** permission, so no `pm grant`, `appops`, reboot,
audio-constraint tweak, or camera change can fix it (all were tried and ruled out). Chrome
works on the same hardware because Chrome's manifest declares it. FreeKiosk **1.2.18-beta.1**
added it and **1.2.20-beta.4** ships it plus an intercom 2-way-audio mode (switches to
communication audio mode only while capturing). The fix is purely: install >= 1.2.20-beta.4.
Verified 2026-07-17 on M9 (TB305) and M11 (TB336): 1.2.19 fails, 1.2.20 works.

### 6.3 How the browser side works

The `voice_satellite` integration (`jxlarrea/voice-satellite-card-integration`, MIT) vendors
a frontend JS that **auto-loads globally** on every HA page via `add_extra_js_url` (browser_mod
pattern - no `resources:` entry, no card needed). Once a browser is assigned to a satellite,
that JS holds the mic open, runs microWakeWord in-browser, and on wake drives the Binks
pipeline. Note: the old `voice-satellite-card` Lovelace card is **deprecated** in current
versions ("Settings moved to the Voice Satellite sidebar panel") - it renders but does NOT
bind a browser. Assignment is only via the sidebar panel.

### 6.4 Assign a browser to a satellite (the per-tablet manual step)

Per-browser identity, not git-seedable. In the tablet's WebView:

1. Navigate to the **Voice Satellite** panel: `https://hass.kblab.me/voice-satellite`
   (in kiosk mode the sidebar is hidden; reach it by a dashboard button with
   `tap_action: {action: navigate, navigation_path: /voice-satellite}`, or temporarily point
   FreeKiosk's URL there with `--es pin 4321 --es url .../voice-satellite`).
2. In **Settings -> Assign the Voice Satellite device**, pick the satellite entity for this
   room from the dropdown, then **Start** (or rely on Auto start).
3. Verify server-side: `assist_satellite.<name>` leaves `unavailable` and becomes `idle`.

Only one satellite entity (`assist_satellite.wall_kitchen`) is seeded today. For voice on more
than one room, seed a `seed-voice-satellite-<room>` init container per tablet in
`apps/home-assistant` (each creates its own `assist_satellite.<room>`), then assign each
browser to its own - two browsers on one satellite will fight.

### 6.5 Wake word

Currently the built-in **"ok nabu"**. Custom **"hey binks"** is a follow-up: train a
microWakeWord `.tflite`, ship it into `/config/voice_satellite/models/`, flip
`select.<satellite>_wake_word_1`. See `home-assistant-voice.md` Stage 4B.

### 6.6 Screensaver caveat

The in-dashboard lovelace-wallpanel photo screensaver overlays in-page (does not navigate),
so it is compatible with a held mic. A real OS screen-off, or a screensaver configured to
navigate away, tears the mic stream down. `wall.yaml` `idle_time` is 8s - if a panel is
primarily a voice panel, verify the satellite stays `idle` across a screensaver cycle; raise
`idle_time` if it drops.

### 6.7 Diagnose the mic in one shot

Point a panel at a tiny `getUserMedia` test page and read `err.name`:

- `NotReadableError` -> version/host-permission (FreeKiosk < 1.2.20, or `MODIFY_AUDIO_SETTINGS` missing).
- `NotAllowedError` -> the WebView permission request was denied (rare; RECORD_AUDIO missing or user-denied).
- `SecurityError` / no secure context -> not HTTPS.

A minimal test page lives at `https://hass.kblab.me/local/mictest.html` (HA `/config/www`);
it enumerates devices and tries several constraint sets. Reproduce via:
`adb -s <ip> shell am start -n com.freekiosk/.MainActivity --es pin 4321 --es url "https://hass.kblab.me/local/mictest.html?v=N"` (bump `?v=` to bust the WebView cache), then screenshot.

---

## 7. Wireless ADB and reboot persistence

Classic `adb tcpip 5555` is **not reboot-proof** on these tablets: `persist.adb.tcp.port` is
empty (settable only by `init` at build time on a user build) and `service.adb.tcp.port`
resets on reboot. A reboot therefore drops wireless ADB back to USB-only. Re-arm over USB:

```bash
adb -s <usb-serial> tcpip 5555 && adb connect <ip>:5555
```

The only reboot-proof, root-free path is **Android 11+ Wireless Debugging**
(`adb_wifi_enabled`), which requires a one-time on-device tap: Settings -> Developer options
-> Wireless debugging -> "Pair device with pairing code" -> `adb pair <ip>:<port> <code>`, and
"always allow on this network" to trust the Wi-Fi BSSID. Nothing headless bypasses the
BSSID-trust gate (verified at AOSP source level; a Device-Owner `setGlobalSetting` hits the
same gate). After trusting once it survives reboot, but an AP roam / SSID change re-arms it.
Of the fleet, only the Idea Tab (`.234`) was paired this way; the others revert to USB on
reboot until paired. Full analysis: `claudedocs/code-research_android-control-persistence_2026-07-17.md`.

**Practical rule:** use the FreeKiosk REST/MQTT channel (Section 5) for routine control - it
is genuinely reboot-proof. Keep wireless ADB for occasional real shell access.

---

## 8. Headwind MDM

Self-hosted Headwind MDM Community (Google-free), the light Android MDM layer (NOT FleetDM,
which is decommissioned). Manifests in `apps/headwind-mdm/` (Flux, LAN/Tailscale-only). Admin
at `https://mdm.kblab.me` (image `headwindmdm/hmdm:0.1.8`, `HMDM_VARIANT=os`), shared-cluster
Postgres db `hmdm`. It is the Device Owner and owns the allow-listed app launcher; it does NOT
provide remote shell (no self-hostable MDM does - confirmed). Liveness bridge:
`apps/headwind-fleet-bridge` -> gatus key `android_<device>`. Runbook: `device-fabric.md`.

---

## 9. Dashboard / HA integration

- Dashboard is `apps/home-assistant/config/dashboards/wall.yaml`, `mode: yaml`, registered in
  `configuration.yaml` panel `wall-panels`. Content-hashed `configMapGenerator` -> editing it
  rolls the HA pod; a fresh pod re-reads all YAML dashboards on start.
- The WebView **caches** the dashboard; a Flux deploy does NOT auto-refresh a panel. Force it:
  `adb ... am force-stop com.freekiosk` + relaunch (or REST `/api/reload`, or FreeKiosk
  pull-to-refresh). Order of operations + verification: `wall-panels.md` Section on reloading.
- Per-tablet identity uses browser_mod Browser IDs (`?BrowserID=wall-<room>`) for future
  per-panel targeting (note: browser_mod is vendored but must be configured to register).

---

## 10. Troubleshooting playbook

| Symptom | Likely cause / fix |
|---|---|
| Voice mic dead, `NotReadableError` | FreeKiosk < 1.2.20 (missing `MODIFY_AUDIO_SETTINGS`). Update the APK (Section 5.4). This is the #1 cause. |
| Voice mic dead, `NotAllowedError` | `RECORD_AUDIO` not granted, or the WebView permission request denied. Re-grant; re-provision. |
| `am start` config intent does nothing | Wrong PIN. Use `--es pin 4321`. Not a lock. |
| Can't reach `/voice-satellite` panel on the kiosk | Sidebar hidden in kiosk mode; navigate via a dashboard button `tap_action navigate`, or temporarily set FreeKiosk URL to it (pin 4321). |
| Wireless ADB gone after reboot | Expected; re-arm over USB (`adb tcpip 5555`) or pair Wireless Debugging (Section 7). |
| Lost the FreeKiosk REST key | Set-once, unrecoverable from the device. Check `~/.config/wall-tablets/<name>.rest-key`; else re-provision. |
| Tablet only shows 3 apps | It is on the Headwind launcher (FreeKiosk kiosk disabled). Re-enable: `am start ... --es pin 4321 --ez kiosk_enabled true`. |
| Cards "Custom element doesn't exist" | Hard-refresh; confirm `hass.kblab.me/local/community/<card>/<file>.js` returns 200. |
| Screensaver photos blocked/blank | Must use the HTTPS immich-kiosk host (mixed-content otherwise). |
| Panel shows stale dashboard after deploy | WebView cache; force-stop+relaunch FreeKiosk or `/api/reload`. |
| Orientation wrong after an update | Lock rotation in FreeKiosk/OS to the mount orientation. |

---

## 11. Known gotchas and decisions

- **FreeKiosk config PIN is `4321`** (orchestrator), not the `1234` raw-script default. Wrong PIN = silent no-op.
- **Device Owner is Headwind**, not FreeKiosk. Disabling FreeKiosk kiosk drops to Headwind's launcher.
- **FreeKiosk REST key is set-once** and generated at provision time - it is persisted to `~/.config/wall-tablets/`.
- **Wireless ADB is not reboot-proof** without a one-time Wireless-Debugging pairing tap.
- **Chrome on the enrolled tablets is a de-Googled stub** (won't launch); do not rely on it - use the mic test page in FreeKiosk instead.
- **Decision: FreeKiosk (MIT/OSS) over Fully Kiosk (proprietary).** The trade-off cost us the pre-1.2.20 mic bug; kept because 1.2.20 resolves it and OSS is preferred. Re-evaluate only if upstream stalls on a future WebRTC regression.
- **All tablets are MediaTek** - `light.sensor.service` cycling the front camera and AIDL audio HAL are normal, not faults.
