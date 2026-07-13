# Wall Panels (Home Assistant kiosk tablets)

Turns a wall-mounted Android tablet into a Nest-Hub-style home panel: a polished
HA dashboard that, when idle, fades into an Immich photo frame — with a clock/date +
photo metadata overlay, automatic side-by-side **portrait pairing**, and a translucent
**control panel** pinned to the right ~36% (weather + homelab/device status + an Expand hint).

- **Dashboard:** `https://hass.kblab.me/wall-panels` (LAN/Tailscale only)
- **Views:** Home (glance) · Control · Devices · Ops/Fleet · Photos · Media · Settings · Admin
- **Screensaver:** lovelace-wallpanel embeds immich-kiosk's full page via `iframe+` (idle/keep-awake
  from wallpanel; clock + metadata + `KIOSK_LAYOUT=splitview` portrait pairing from immich-kiosk),
  with wallpanel's info box repurposed as a right-hand control panel (`wallpanel.cards`).
- **Expand to full view:** `content_interaction: false` — a single tap ANYWHERE (photos or panel)
  reliably ends the screensaver and reveals the full Home dashboard. (Previously the screensaver
  ran `content_interaction: true` over a full-screen kiosk iframe, so taps were swallowed and it
  "took two clicks" to dismiss.) The panel is therefore glanceable; the real controls are one tap
  away on Home.
- **Photo source:** `apps/immich-kiosk` (`immich-kiosk.kblab.me`) → your Immich library
- **Dashboard source:** `apps/home-assistant/config/dashboards/wall.yaml` (code-first, Flux-deployed)

Hardware decisions, profiles model, and phase plan live in the project plan; this is the
hands-on per-tablet runbook.

---

## 0. Quick test (any device, no install)
Open `https://hass.kblab.me/wall-panels` in a browser and log in.
**Hard-refresh once (Ctrl/Cmd+Shift+R)** so the custom cards load. Wait ~20s without
touching it → the Immich photo screensaver starts, with the control panel on the right;
a single tap anywhere reveals the full Home dashboard. That's the full experience minus
the OS lockdown.

> Admins keep the HA header/sidebar; non-admin profiles get the clean kiosk view
> (kiosk-mode). To preview the locked look as admin, append `?kiosk`; `?disable_km` exits.

---

## 1. Keep the screen ON
Three independent layers — use all three for a permanent mount:

1. **Dashboard (already configured):** `wall.yaml` sets `keep_screen_on_time: 86400`
   in the `wallpanel:` block → uses the browser **Wake Lock API** to hold the screen on
   while the dashboard is open. Works in the browser today. (Wake Lock can drop if the
   tab is backgrounded — hence layers 2–3.)
2. **Android OS:** Settings → **Developer options** → **Stay awake** (screen never sleeps
   while charging). Enable Developer options: Settings → About tablet → tap **Build number**
   7×. A wall panel is always on the charger, so this is the reliable always-on switch.
3. **Kiosk app:** FreeKiosk (below) has its own "keep screen on" — belt-and-suspenders.

Also set Settings → Display → **Screen timeout = max**, and disable the lock screen
(Settings → Security → Screen lock = **None**) so it never asks for a PIN on wake.

---

## 2. Full kiosk lockdown (FreeKiosk — open-source)
For a mounted panel you want it to boot straight into the dashboard, full-screen, with
no way to wander off into Android. We use **FreeKiosk** (`com.freekiosk`, MIT, open-source)
on stock-Android Lenovo tablets (M9/M11). (FireOS is not supported — see plan.)

> **Scriptable (recommended): `scripts/provision-wall-tablet.sh`.** FreeKiosk takes its
> ENTIRE config via `am start` intent extras, so once the tablet is on ADB (Developer
> Options → USB debugging, plug in or `adb tcpip`) one command provisions it — grants the
> mic, keep-awake, start URL, PIN, auto-boot, and optional REST/MQTT — no tapping:
> ```bash
> scripts/provision-wall-tablet.sh --name wall-kitchen \
>   --url https://hass.kblab.me/wall-panels --pin 4321 \
>   --mqtt-broker mosquitto.kblab.me --mqtt-user hass --mqtt-pass '<pw>'   # MQTT optional
> # add --apk <path> to install first, --device-owner for true lock-task, --dry-run to preview
> ```
> It prints the few unavoidable on-device/HA steps at the end (HA login, satellite
> assignment, PIN change). The manual walkthrough below documents what it automates.

1. **Install FreeKiosk** — from Google Play (search "FreeKiosk"), or sideload the APK from
   <https://github.com/RushB-fr/freekiosk> / <https://freekiosk.app>.
2. **Start URL:**
   `https://hass.kblab.me/wall-panels/home?kiosk`
   (the `?kiosk` query forces kiosk-mode chrome-hiding regardless of which user logs in.)
3. **Settings to enable:**
   - Launch on boot / set as home app (so a reboot returns to the panel).
   - Fullscreen / immersive (hide status + nav bars).
   - Keep screen on.
   - Motion/tap wake (optional).
4. **Lockdown (device-owner, strongest):** FreeKiosk can provision as Android *device owner*
   via ADB for true lock-task mode (blocks home/recents/status bar). On a factory-fresh or
   reset tablet:
   ```bash
   # tablet in dev mode + USB debugging on, connected via adb (see adb-ops skill)
   # Prereq: ALL accounts removed (Settings → Accounts = none) + SIM out, else this fails.
   adb shell dpm set-device-owner com.freekiosk/.DeviceAdminReceiver
   ```
   Skip this for a quick setup; the home-app + immersive settings already give a solid kiosk.
5. **Log in** to HA as the panel's profile (see §4). The webview keeps the session.
6. **Change the FreeKiosk PIN.** FreeKiosk ships a **default settings/exit PIN of `1234`** —
   that's the code that unlocks settings / exits the kiosk. **Change it** (FreeKiosk →
   Settings → Security/PIN) to your own, or anyone can walk up and exit the kiosk into Android.

> Built-in screensaver alternative: FreeKiosk also has a **web-page screensaver mode** you
> can point at `https://immich-kiosk.kblab.me` (NOT `?disable_ui=true` — that hides the clock/
> metadata overlays). We instead drive the screensaver *inside* the dashboard (wallpanel), so
> you don't need it — but it's there if you ever want the photos even on a non-HA page.

> **Picking up dashboard changes on the tablet:** the webview caches the dashboard. After a
> dashboard/screensaver change is deployed, the tablet keeps the old one until you **hard-refresh**
> (pull-to-refresh, or FreeKiosk → reload, or clear the FreeKiosk webview cache / restart the app).
> Symptom of a stale tablet: HA logs `frontend.wallpanel ... Failed to play media .../image?t=...`
> (the old single-image screensaver URL). A refresh switches it to the current immich-kiosk frame.

---

### Voice satellite (Binks) on the panel

The wall panel doubles as an always-listening **Binks** voice satellite (in-browser
microWakeWord → the Binks Assist pipeline; see `docs/home-assistant-voice.md` Stage 4). Two
FreeKiosk-specific things matter:

- **Mic:** FreeKiosk's patched WebView **auto-grants** `getUserMedia` once the OS
  `RECORD_AUDIO` permission exists (the provision script grants it; no in-app mic toggle).
  It only works over HTTPS — our `https://hass.kblab.me` (trusted LE cert) qualifies.
- **Screensaver ⚠️:** a real **screen-off** or a screensaver that **navigates away** from the
  dashboard tears down the mic stream and the wake word goes deaf. Keep **Keep-Screen-On +
  screensaver OFF (or Dim-Only)** on a voice panel. (This is why the in-dashboard wallpanel
  photo screensaver is fine but an OS/screen-off screensaver is not.)

One-time, in HA (per browser, like browser_mod): open the **Voice Satellite** sidebar panel
→ assign this browser to the **"Wall Kitchen"** satellite. Then say "ok nabu" → Binks.

## 3. Per-tablet identity (browser_mod)
Each tablet registers with browser_mod under a stable Browser ID by adding it to the URL once:
`…/wall-panels/home?kiosk&BrowserID=wall-kitchen` (e.g. `wall-kitchen`, `wall-bedroom`).
This lets later automations target a specific panel (navigate it home on idle, night dim, etc.).

---

## 4. Profiles (HA users)
"Profiles" = HA users. Today everything runs as the admin (`kblack0610`). Phase 5 adds:
- **Family** (non-admin) — Home/Control/Media/Photos/Ops; admin actions server-rejected.
- **Guest/Wall** (non-admin, limited) — Home/Photos.

Create users in HA UI → Settings → People/Users. Log each tablet's webview into the intended
profile. **Real security = the non-admin user** (the Admin view's restart/update actions are
rejected for non-admins regardless of UI). The PIN popup added later is cosmetic friction only.

---

## 4.5 Plug Settings view (any user configures the home)

The **Settings** view (nav chip → `/wall-panels/settings`) lets **any user, including a
non-admin on the kiosk**, configure each smart plug: its **room**, **name**, **icon**,
**category**, and whether it counts as a **light**. Tap a plug in the list → it loads into the
always-visible **Editor** at the top → change fields → **Save**. Changes are instant and apply
everywhere (the plug dashboards regroup by room; voice picks up the new name/area). Plugs start
**Unassigned** — nothing is placed for you.

Where the metadata lives — all in HA's **native registries** (the persistent `.storage` "db"),
so it's the same data HA's own UI would write and it powers native area/voice features:

| Field | Native store |
|-------|--------------|
| Room | `core.area_registry` + entity `area_id` |
| Name | `core.entity_registry` name override |
| Icon | `core.entity_registry` icon override |
| Category | `core.label_registry` + entity `labels` |
| Is-a-light | `switch_as_x` config entry (add/remove) |

How it works (why it needs a helper): HA has **no built-in service to set an entity's area or
name**, and the native registry editors are **admin-only and hidden in kiosk mode**. So the
editor calls a small **pyscript** app (`config/pyscript/plug_settings.py`) whose services
(`pyscript.plug_load` / `pyscript.plug_apply`) run in HA's own context and perform the registry
writes — callable by a non-admin. The UI is an **inline editor**: a fixed set of staging helpers
in `config/packages/plug_settings.yaml` (no browser_mod dependency). Tapping a plug calls
`pyscript.plug_load` (fills the helpers); Save calls `pyscript.plug_apply` (writes them back).
The per-plug rows are `custom:button-card` because HA core does not template `tap_action`
service data (button-card's `[[[ ]]]` JS does, so it passes the tapped plug's `entity_id`).

> ⚠️ **Security note:** the `pyscript.plug_*` services are a deliberate, **LAN/Tailscale-only**
> privileged surface — a non-admin can write the registry through them. That's the price of
> kiosk-editable metadata; acceptable because the dashboard is not internet-exposed. Do not
> expose HA publicly without gating these.

Adding rooms: the editor's Room dropdown lists live HA areas; type a new room in **New room
(optional)** to create it on Save. The baseline palette (Living Room, Kitchen, Bedroom, Office,
Garage, Bathroom) is seeded idempotently by the `seed-areas` init container.

---

## 5. One-time HA integrations (UI, keyless/optional)
- **Met.no** (weather hero) — Settings → Devices → Add Integration → *Meteorologisk institutt (Met.no)*.
  Keyless; uses HA's home location → `weather.forecast_home`. **(done)**
- **Jellyfin** (optional, for in-HA media control) — Add Integration → *Jellyfin* →
  `https://jellyfin.kblab.me` + an API key. Creates `media_player` per active session.

---

## 6. Network (when mounting permanently)
Reserve a static lease so the panel keeps a stable IP (browser_mod, future per-panel targeting):
add it to `infrastructure/dhcp/devices.yaml` (mobile range `.50–.79`, e.g. `wall-kitchen`),
then apply via the host-layer dhcp/Ansible path (not Flux). MAC is on the tablet:
Settings → About → Status → Wi-Fi MAC (use the *non-randomized* MAC for this SSID).

---

## 7. Battery longevity (24/7 on charger)
- **Lenovo Tab M11:** Settings → Battery → enable **Battery Maintenance / Protection** (caps
  charge ~40–60%). Do this — it's the whole reason we picked the M11.
- **Tab M9 / others:** if no charge-cap toggle exists, gate the charger 20–80% with a smart
  plug + 2 HA automations off the tablet's battery sensor (the IoT pattern) to avoid swelling.

---

## 8. Troubleshooting
| Symptom | Fix |
|---|---|
| Cards show "Custom element doesn't exist: …" | Hard-refresh (Ctrl+Shift+R). Resources are vendored by the HA init container; confirm `https://hass.kblab.me/local/community/<card>/<file>.js` returns 200. |
| Screensaver photos don't rotate | The `image_url` must keep `?t=${timestamp}` (cache-buster) — without it newer wallpanel caches one image. |
| Screensaver image blocked / blank | Use the **HTTPS** immich-kiosk host (HA is HTTPS → http is mixed-content blocked). |
| Weather hero shows an error | Add the **Met.no** integration (§5) → `weather.forecast_home`. |
| Screen still sleeps | Layer 2 (Android Stay-awake) is the reliable one; browser Wake Lock alone can drop. |
| Want full chrome back as admin | Append `?disable_km` to the URL. |
