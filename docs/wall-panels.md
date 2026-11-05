# Wall Panels (Home Assistant kiosk tablets)

Turns a wall-mounted Android tablet into a Nest-Hub-style home panel: a polished HA dashboard that dims when idle: the view stays up by day, and at night it shows what the Sleep behavior setting picks.

- **Dashboard:** `https://hass.kblab.me/wall-panels` (LAN/Tailscale only)
- **Views:** Home (glance, with collapsible Cameras and Music sections), Control, Devices, Local, Ops/Fleet, Agents, Photos, Music, Media, Settings, Admin
- **Screensaver:** lovelace-wallpanel loads a web page via `iframe+` and repurposes its info box as the control panel (`wallpanel.cards`). It is one piece the idle owner switches per time of day, below.
- **Dashboard source:** `apps/home-assistant/config/dashboards/wall.yaml` (code-first, Flux-deployed)

### Idle behaviour

| When | Idle | Touch or motion |
|------|------|-----------------|
| Day | Home view stays up at the tablet's lowest usable brightness | Full brightness |
| Night (22:00-07:00) | The Sleep behavior choice, at brightness 0 | Full brightness and the view |

After the idle timeout the panel goes back to that time of day's idle mode. To change the night mode, use the **Sleep behavior** select on the Settings view: Black screensaver or Dim view. It takes effect at once, with no edit or deploy.

One owner: lovelace-wallpanel's idle timer on each tablet. It starts the screensaver, a touch ends it, and it mirrors that into `input_boolean.wall_idle_<panel>`. `config/pyscript/wall_idle.py` watches those booleans and sets brightness through FreeKiosk's HA number entity, and publishes `sensor.wall_screensaver_<panel>`, which wallpanel reads as its `profile_entity`. Nothing else dims a panel: FreeKiosk's own screensaver is off, and Android never sleeps.

Per-tablet data is in `config/pyscript/wall_panels.yaml`, `defaults` plus a block per panel that can override any key. It loads when HA starts, so a change here deploys through Flux:

| Key | Meaning |
|-----|---------|
| `idle_seconds` | Idle timeout. Read at page load; the restart reloads the panels. |
| `active_brightness` | Brightness while in use, 0-100 |
| `day.idle_brightness`, `night.idle_brightness` | Brightness while idle |
| `day.screensaver` | `off`, `black`, `scenery` or `family` |
| `sleep_behavior` | Per panel only: `Dim view` or `Black screensaver`, overrides the select for that panel |
| `night_window` | `start` and `end`, 24h local time |
| `motion_wake` | Turn on FreeKiosk's always-on camera motion and wake on it |
| `motion_min_lux` | Ignore motion below this light level; touch still wakes the panel. Needs `light`, the panel's FreeKiosk light sensor. A tablet with no sensor (office reports -1) is never gated |

All four tablets support the same options, so the select has no per-tablet list and the override is data only.

The screensavers are the wallpanel profiles in `wall.yaml`:

| Profile | Content | Controls |
|---------|---------|----------|
| `off` | Clear overlay, so the Home view shows through | None; any tap ends it |
| `black` | Solid black after 1 s | None; any tap ends it |
| `scenery` (base config) | `apps/wall-ambient` (`wall-ambient.kblab.me`): Wikimedia Commons scenery from the "Wall Scenery" album, with an animated shader scene (plasma, flow, kaleidoscope, Julia set, tunnel, aurora) after every few slides | Full screen below a clear band for the clock and weather: Talk and Dashboard as two big buttons with a health pill under them on the left, square room tiles on the right; one column on a portrait tablet |
| `family` | `apps/immich-kiosk` family album ("Wall Best"), portrait pairing, metadata | Narrow translucent column on the right |

`scenery` and `family` are not on any schedule now; set `day.screensaver` to bring one back. On those two, taps toggle tiles in place and the Dashboard button is the way out.

**Display off is not offered.** Checked 2026-11-04 on all four tablets (`wall-bedroom`, `wall_office`, `wall_kitchen`, `wall_living_room`): none can power the panel off.

- FreeKiosk `set/screen OFF` left `screen.on` true over three state reports on each, because it has neither Device Admin nor the Accessibility service, and it falls back to dimming. Headwind MDM is device owner, so FreeKiosk cannot be.
- adb (5555) is closed on all four, so there is no `input keyevent` or screen-off timeout.
- A real lock would also stop the WebView mic, so the wake word would die on all four voice panels, and touch cannot wake a locked panel. Wake would have to come from HA presence or MQTT.

- **Scenery album:** owned by a dedicated Immich user `wall@kblab.me`, so none of the family libraries mix in. The `immich-scenery` CronJob (`apps/immich/scenery.py`, 05:00) adds 40 new free-licensed featured pictures a night from the Commons categories in `CATEGORIES`, and prunes the oldest past 400. Each asset's description is its credit (title, author, licence), which the slideshow shows.
- **Retune the rotation** in the `image_url` query string in `wall.yaml`: `scenery` (slides between shader scenes), `trippy_secs` (length of a shader scene), `shaders` (comma list to restrict which ones play), `render_scale` (lower it if a tablet stutters). Set `trippy_secs=0` to turn shader scenes off.
- **Health pill:** green "All up" or red "N down" from `sensor.services_down_count` (`packages/fleet_alerts.yaml`), the same count the phone, the Ops view and the ntfy alert read; tap opens Ops. To widen what counts as down, change that sensor, not the card.
- **Screen sizes:** the two columns need 420px each, so a portrait tablet stacks them; room tiles are capped at `--wall-tile-max` (`min(15vh, 120px)`) so all rooms fit a 600px tall Tab M9. Check a change at 1005x600 (Tab M9), 1280x800 (Tab M11) and 1463x914 (Idea Tab), landscape and portrait, before merging.

Hardware decisions, profiles model, and phase plan live in the project plan; this is the
hands-on per-tablet runbook. For the full reference - requirements table, FreeKiosk
REST/MQTT control channels, the voice-satellite deep-dive and root cause, fleet inventory,
and the fleet-update procedure - see [`wall-tablets.md`](./wall-tablets.md).

---

## 0. Quick test (any device, no install)
Open `https://hass.kblab.me/wall-panels` in a browser and log in.
**Hard-refresh once (Ctrl/Cmd+Shift+R)** so the custom cards load. Wait ~20s without
touching it -> the screensaver starts (scenery on weekdays, family photos on Sunday) with
the controls on top; the Dashboard button reveals the full Home dashboard. That's the full experience minus
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
>   --mqtt-broker mosquitto.kblab.me   # MQTT optional; anonymous, port 31883 by default
> # add --apk <path> to install first, --device-owner for true lock-task, --dry-run to preview
> ```
> It prints the few unavoidable on-device/HA steps at the end (HA login, satellite
> assignment, PIN change). The manual walkthrough below documents what it automates.

1. **Install FreeKiosk** — sideload the APK (**v1.2.20-beta.4+ REQUIRED for voice**; earlier
   versions lack `MODIFY_AUDIO_SETTINGS` and the mic is dead — see the voice section and
   `wall-tablets.md` Section 6) from <https://github.com/RushB-fr/freekiosk/releases>. The
   pinned APK is kept at `~/.dotfiles/.local/src/android-suite/apks/freekiosk.apk`.
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
6. **FreeKiosk PIN.** FreeKiosk ships a default of `1234`, but **our provisioner sets `4321`** —
   that's the code that unlocks settings / exits the kiosk AND authenticates every `am start`
   config intent (a wrong PIN makes config intents silently no-op). Change it per tablet if you
   want, but keep it in sync with what you pass to `--pin`.

> Built-in screensaver alternative: FreeKiosk also has a **web-page screensaver mode** you
> can point at `https://immich-kiosk.kblab.me` (NOT `?disable_ui=true` — that hides the clock/
> metadata overlays). We instead drive the screensaver *inside* the dashboard (wallpanel), so
> you don't need it — but it's there if you ever want the photos even on a non-HA page.

> **Picking up dashboard changes on the tablet:** a tablet keeps the page it loaded until the
> page reloads, so a deploy alone never reaches it. A `wall.yaml` change restarts HA, and once
> `wall_idle.py` has started, the automation `wall_panels_reload_after_start` (`packages/wall_idle.yaml`)
> presses the FreeKiosk MQTT `reload` button of every panel that has one, found by device model
> "(FreeKiosk)". A panel without FreeKiosk MQTT keeps the old page until someone pulls to refresh.
> Side effect: any HA restart reloads the panels (a few seconds, mic included). A change to
> `apps/wall-ambient` alone does not restart HA, so it is not pushed.
>
> **Intended control channel = FreeKiosk MQTT, not Headwind MDM.** By design, all *runtime*
> panel control (reload the dashboard, screen on/off, brightness, TTS) rides FreeKiosk's own
> MQTT/REST channel to HA — Headwind MDM only handles enrollment, app deploy, lockdown, and
> liveness. See the "Runtime control is FreeKiosk MQTT" decision in `docs/device-fabric.md`.
> Enable it per tablet with `provision-wall-tablet.sh --mqtt-broker mosquitto.kblab.me ...`,
> then reload via the FreeKiosk `reload` MQTT entity instead of adb. Where MQTT is not enabled,
> the fallback is `adb force-stop com.freekiosk` + relaunch (see the `wall-tablet-ops` skill).

#### Remote update and view: status (verified 2026-10-15)

What survives a reboot and what does not, per channel:

| Channel | Gives you | Survives reboot? | Live on |
|---------|-----------|------------------|---------|
| FreeKiosk MQTT -> HA | reload, navigate URL, screen, brightness, volume, current-URL sensor, D-pad/keyboard | Yes, config is stored in the app (bedroom has reported continuously since 2026-07-17) | wall-bedroom (`freekiosk_dc43ede573076a31`, 192.168.1.60), wall-office and wall-kitchen (enabled in-app 2026-11-03, entities `button.wall_office_reload`, `button.wall_kitchen_reload`) |
| FreeKiosk REST `:8080` | `/api/url`, `/api/js` | Yes on the tablet, but the set-once key must be in `~/.config/wall-tablets/<name>.rest-key` | none: that directory does not exist on the workstation, so no key is usable |
| `adb tcpip 5555` | screencap, input, provisioning | No, resets on reboot (no root, `persist.adb.tcp.port` denied) | none |
| Android Wireless Debugging (paired workstation, random port, `~/.local/bin/adb-wall-bedroom` scans for it) | screencap, scrcpy | **No in practice.** Set up on .60 on 2026-07-18; on 2026-10-15 the scan found no port. Android turns it off on its own (Wi-Fi reconnect / timeout), not only on reboot | none |
| DHCP static lease | stable IP | Yes (router-side) | wall-bedroom only; kitchen (.229), living room (.181) and office (.227) are dynamic, read 2026-11-04 from each panel's FreeKiosk state |

So today there is **no remote screenshot path that holds**, though `freekiosk/<name>/set/execute_js` can read the DOM (see `wall-tablet-ops`), and remote reload works on every panel with FreeKiosk MQTT. FreeKiosk MQTT has no screenshot. Verify a reload without a screenshot via HA: `sensor.freekiosk_*_current_url`, `binary_sensor.freekiosk_*_screen_on`, and `assist_satellite.<room>` going `idle`.

Open follow-up, in order:
1. Add static leases for kitchen / living room / office in `infrastructure/dhcp/devices.yaml`.
2. For a remote view that holds: check what Headwind MDM (already device owner on all four) can enforce, such as keeping USB/wireless debugging on, or a remote-view app it keeps running. Do not rely on Wireless Debugging alone.

---

### Voice satellite (Binks) on the panel

The wall panel doubles as an always-listening **Binks** voice satellite (in-browser
microWakeWord → the Binks Assist pipeline; see `docs/home-assistant-voice.md` Stage 4). Two
FreeKiosk-specific things matter:

- **Mic ⚠️ REQUIRES FreeKiosk >= 1.2.20-beta.4.** Chromium's WebView needs the host app to
  declare BOTH `RECORD_AUDIO` **and** `MODIFY_AUDIO_SETTINGS` to select a mic input device.
  FreeKiosk <= **1.2.19 declares only `RECORD_AUDIO`**, so `getUserMedia` fails with
  `NotReadableError: could not start audio source` (logcat: `cr_media: Requires
  MODIFY_AUDIO_SETTINGS ... No audio device` → `chromium: audio_manager_android.cc Unable to
  select audio device!`). `MODIFY_AUDIO_SETTINGS` is a **manifest** permission — NOT grantable
  via `pm grant` — so the ONLY fix is the newer APK (1.2.20-beta.4 adds it + intercom 2-way
  audio). Verified 2026-07-17: 1.2.19 mic fails, 1.2.20 mic works (M9 TB305 + M11 TB336).
  It also only works over HTTPS — our `https://hass.kblab.me` (trusted LE cert) qualifies.
  (Diagnose fast: point a panel at a tiny `getUserMedia` test page and read `err.name`;
  `NotReadableError` = version/permission, `NotAllowedError` = permission-request denied.)
- **Screensaver ⚠️:** a real **screen-off** or a screensaver that **navigates away** from the
  dashboard tears down the mic stream and the wake word goes deaf. Keep **Keep-Screen-On +
  screensaver OFF (or Dim-Only)** on a voice panel. (This is why the in-dashboard wallpanel
  photo screensaver is fine but an OS/screen-off screensaver is not.)

**Talk button and on-screen answers.** The Home view and the screensaver column carry a **Talk to Binks** button (`dashboards/_talk_card.yaml`). It calls `voice_satellite.wake` on the satellite this browser is bound to, read from the card's own localStorage key `vs-satellite-entity`, so one dashboard file serves every room; a browser with no room assigned shows no button. Replies stay on screen for 15 s after speech ends (`finish_delay`, pinned in `voice_satellite_binks.yaml`); double tap or "stop" dismisses early.

One-time, in HA (per browser, like browser_mod): open the **Voice Satellite** sidebar panel
→ assign this browser to the **"Wall Kitchen"** satellite. Then say "ok nabu" → Binks.

### Snapcast room on a wall panel

A wall panel with a speaker on its aux jack can be a synced Snapcast room. The kitchen panel is one: it shows up in snapweb and in HA's Snapcast integration as client `kitchen`, and plays whatever stream its group is on, in sync with the other rooms.

How: `apps/home-assistant/config/www/snapcast-tablet.js` is a Snapcast browser client loaded on every HA page through `frontend: extra_module_url`. It starts only on a browser whose Voice Satellite assignment (localStorage `vs-satellite-entity`, set in the Voice Satellite sidebar panel) is listed in its `ROOMS` table, so phones and desktops load it and do nothing. It connects to `wss://snapcast.kblab.me/stream` with hostID and host name set to the room name. FLAC is decoded with libflacjs 5.4.0, vendored by the `install-lovelace-cards` init container into `/local/community/libflacjs/`.

Why this shape:

- A module, not a card: HA is a single-page app, so a card is unmounted on every view change, and the wallpanel screensaver is an overlay. The module is evaluated once per page load and keeps running until the page reloads.
- Not an iframe of snapweb: snapweb's player registers as "Snapweb client" under a random id kept in snapcast.kblab.me's localStorage, which the HA page cannot set, so the room could not be named. It also starts only from a click on its play button.
- The code is a port of snapweb v0.9.2 `src/snapstream.ts` (the version the snapserver image ships), not a rewrite. Changes from upstream: native AudioContext instead of the standardized-audio-context ponyfill; FLAC and PCM only (the server codec is flac); the clock-offset median sorts numerically (upstream sorts strings); buffers are stopped on disconnect (upstream stacks another set of buffer chains on every reconnect); a link that goes silent for 5 s without a TCP close is closed and reconnected, with backoff from 1 s to 30 s and an immediate retry on the browser `online` event.
- Autoplay: FreeKiosk's WebView sets `mediaPlaybackRequiresUserAction={false}` (`src/components/WebViewComponent.tsx`), the same reason the satellite's TTS plays untouched, so the AudioContext starts without a tap. If a browser blocks it anyway, the first touch on the page starts it.
- Cross-origin: browsers do not apply CORS to WebSockets and snapserver does not check `Origin`, so `hass.kblab.me` talking to `snapcast.kblab.me` needs no ingress change. Both are https/wss, so there is no mixed content. HA sends no Content-Security-Policy.

Add a room: add `"assist_satellite.<panel>": "<room>"` to `ROOMS`, bump the `?v=` on the `extra_module_url` line in `configuration.yaml`, deploy, and reload the panel. Pick the room's stream and volume in snapweb or with the HA `media_player` for that client.

Debug: in the panel's WebView console, `window.__snapcastTablet.status()` shows the socket, AudioContext state, sample format, volume, clock offset and last error. Log lines are prefixed `[snapcast-tablet]`. From any LAN machine: `curl -s https://snapcast.kblab.me/jsonrpc -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' | jq '.result.server.groups[] | {stream_id, clients: [.clients[] | {id, connected}]}'`.

### Home layout

`/wall-panels/home` has a fixed layout so every tablet shows the same thing in the same spot:

| Spot | What is there |
|---|---|
| Top bar | One row of equal nav buttons: Controls, Devices, Music, Media, Local, Agents (red or amber when an agent needs you), Photos, Settings, Admin. Destinations only; the Screensaver button is on Admin. |
| Left: Now | The household alert (only when set), clock and 3-day forecast, Talk to Binks, Cluster and 3D printer status, Announce (folded). |
| Middle: Rooms | `dashboards/_rooms_cards.yaml`, the room tiles by HA area, shared with Overview and the screensaver. One square tile per device, lights first, lit amber when on; a word under the name only when the glow cannot say it (Offline, litter box status, red when the litter box needs a person). A tile shows its entity's icon (`icon` in `pyscript/room_layout.yaml`, `mdi:*` or `wall:*`), else a lightbulb. |
| Right: Media | Music, then Cameras. |

The view has exactly three sections, so a 3-column tablet fills one row. A narrower one (2 columns, the 8.7" office tablet at some densities) puts Media under Now. Adding a fourth section starts a new row under the tallest column, so add cards to an existing column instead. Every sub-view starts with `dashboards/_home_button.yaml`, one shared Home bar that navigates from anywhere on the bar, not only its icon.

### Cameras and Music on Home

The wall Home view and the phone Home view both include `dashboards/_cameras_card.yaml` and `dashboards/_music_card.yaml`, one file each, so the two dashboards cannot drift. Each is an expander-card that starts open and remembers open or closed per browser (`storage-id`).

- **Cameras:** advanced-camera-card on Frigate's `camera.amcrest_ptz_1`, live over go2rtc WebRTC proxied by HA. Tap the video or the expand button for a full-size dialog; the overlay menu has PTZ, the talk button (two-way audio) and Frigate reviews. A new camera is one more entry under `cameras:`.
- **Music:** player controls for `sensor.music_leader` (`packages/music_panel.yaml`), the Music Assistant player at the head of what is playing, and one tile per room. A tile is lit when the room is in the music. Tap adds or drops it (`script.music_toggle_room`): with nothing playing it resumes that room's queue, and dropping the last room pauses it. Hold opens the room's player. Turntable and per-room sources stay on the Music view.

### Music view

`/wall-panels/music` (Music button on Home) has one section per Snapcast room: the Music Assistant player as a `media-control` card (now playing, play/pause, skip, the browse button for MA's library, radio and Spotify, the join button for grouping rooms), a volume slider, and a Source picker on the room's Snapcast client entity (`media_player.<room>_snapcast_client`). Turntable is picked there, because MA only offers its own streams. Source is per Snapcast group, so joined rooms switch together.

Every tablet shows the same view: all tablets log in as the same HA user, and only wallpanel reads the Browser ID, so a card cannot tell which tablet it is on. A room's cards hide while its players do not exist (a state condition reads a missing entity as `unavailable`).

---

## 3. Per-tablet identity (browser_mod)
The idle settings reach a tablet through its Browser ID, which must match its key in `wall_panels.yaml`. Set it once by loading the URL with it:
`https://hass.kblab.me/wall-panels/home?kiosk&BrowserID=wall_kitchen` (underscores: `wall_bedroom`, `wall_office`, `wall_kitchen`, `wall_living_room`).
It stays in the tablet's localStorage, so the plain URL works afterwards. To set it remotely, publish that URL to `freekiosk/<name>/set/url`. New tablet: pass it to `provision-wall-tablet.sh --url`, and add the panel to `wall_panels.yaml` and `packages/wall_idle.yaml`.

---

## 4. Profiles (HA users)
"Profiles" = HA users. Today everything runs as the admin (`kblack0610`). Phase 5 adds:
- **Family** (non-admin) — Home/Control/Media/Photos/Ops; admin actions server-rejected.
- **Guest/Wall** (non-admin, limited) — Home/Photos.

Create users in HA UI → Settings → People/Users. Log each tablet's webview into the intended
profile. **Real security = the non-admin user** (the Admin view's restart/update actions are
rejected for non-admins regardless of UI). The PIN popup added later is cosmetic friction only.

---

## 4.5 Settings view

The **Settings** view (Settings button on Home -> `/wall-panels/settings`, also on the phone dashboard) holds only what someone changes from the wall: **Sleep behavior**.

Names, rooms, icons and labels are owned by `config/pyscript/room_layout.yaml`, applied by `pyscript.room_layout_seed` on startup into HA's registries. Add `labels: [Lamp]` to a row to put a plug in the lamp group. Whether a plug counts as a light is the `seed-switch-as-x-lights` list in `deployment.yaml`. The baseline rooms are seeded by the `seed-areas` init container.

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
| A panel still shows the old dashboard or screensaver after a deploy | It has no FreeKiosk MQTT, so the reload-after-start automation cannot reach it: enable MQTT on it (Remote update follow-up 2), or pull to refresh. Check: `button.<panel>_reload` exists in HA. |
| Screensaver photos don't rotate | The `image_url` must keep `?t=${timestamp}` (cache-buster) — without it newer wallpanel caches one image. |
| Screensaver image blocked / blank | Use the **HTTPS** immich-kiosk host (HA is HTTPS → http is mixed-content blocked). |
| Weather hero shows an error | Add the **Met.no** integration (§5) → `weather.forecast_home`. |
| Screen still sleeps | Layer 2 (Android Stay-awake) is the reliable one; browser Wake Lock alone can drop. |
| Want full chrome back as admin | Append `?disable_km` to the URL. |
| A name changed in `room_layout.yaml` does not show on a tile | The seed skips an entity that already has a name. Call `pyscript.room_layout_seed` with `force: true` once after the deploy. |
| A tile needs an icon MDI does not have | Add its path to `config/www/wall-icons.js`, use `wall:<name>` as the `icon`, and bump `?v=` in `configuration.yaml`. |
| A live edit to a dashboard partial (`_rooms_cards.yaml`) does not show | YAML-mode dashboards reload on the main file's mtime only: `touch /config/dashboards/wall.yaml` in the pod, then refresh. |
| A panel never goes idle, or a tap on one wakes the wrong one | Its Browser ID is wrong or missing, so `${browser_id}` matches no `sensor.wall_idle_seconds_<panel>` and an empty idle time means never idle. Load the URL in Section 3 once, then check the sensor exists; if none do, read the HA log for `wall_idle`. |
| The view is unreadable at day idle, or too bright | Set `day.idle_brightness` for that panel in `wall_panels.yaml`. It is 1, not 0, because 0 is Android's lowest backlight and can leave a view too dark. The level each panel really reaches was not measured remotely (no screenshots, and the office tablet has no light sensor), so judge it by eye. |
| A panel is still too bright at night | 0 is the floor FreeKiosk can set; below that is the hardware. Black on an LCD still leaks some backlight. Display off is not available on any tablet, see Idle behaviour. |
| Worried about burn-in with the view up all day | These tablets have LCD panels, which do not burn in like OLED but can hold a faint image of a static layout after long hours. Low backlight slows it. If a ghost image shows, set `day.screensaver: scenery` for that panel, which moves. |
| A panel wakes by itself at night | Camera motion on `motion_wake`, often a shadow or headlights. Set `motion_wake: false` for that panel; touch still wakes it. Also the setting to turn off if `wall-bedroom` runs short of memory, which it is closest to. |
| A panel dims, then wakes itself about 12 s later, every cycle | The camera in a very dark room reports noise as motion; `wall_living_room` did this at 0-1 lux, 2026-11-04. `motion_min_lux` stops it; raise it if a panel still cycles. Check with the history of `input_boolean.wall_idle_<panel>` against the panel's motion sensor |
| `wall_bedroom` feels slow, or an animation never finishes | Its WebView drew 1 to 4 frames per 3 s at any brightness on 2026-11-04, against about 80 on kitchen and living room, and a reload did not help. Cause unknown; its JS heap was about 6 times living room's. `fade_in_time: 0` keeps idle working anyway. Measure with `requestAnimationFrame` through `set/execute_js` |
| Room tiles run off the bottom of a small tablet | Lower `--wall-tile-max` in `wall.yaml`'s info-box style; a tile cannot shrink below its icon and two-line name (about 90px). |
