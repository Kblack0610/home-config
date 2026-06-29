# Wall Panels (Home Assistant kiosk tablets)

Turns a wall-mounted Android tablet into a Nest-Hub-style home panel: a polished
HA dashboard that, when idle, fades into a full-screen slideshow of Immich photos.

- **Dashboard:** `https://hass.kblab.me/wall-panels` (LAN/Tailscale only)
- **Views:** Home (glance) · Control · Ops/Fleet · Photos · Media · Admin
- **Photo source:** `apps/immich-kiosk` (`immich-kiosk.kblab.me`) → your Immich library
- **Dashboard source:** `apps/home-assistant/config/dashboards/wall.yaml` (code-first, Flux-deployed)

Hardware decisions, profiles model, and phase plan live in the project plan; this is the
hands-on per-tablet runbook.

---

## 0. Quick test (any device, no install)
Open `https://hass.kblab.me/wall-panels` in a browser and log in.
**Hard-refresh once (Ctrl/Cmd+Shift+R)** so the custom cards load. Wait ~20s without
touching it → the Immich photo screensaver starts; tap to dismiss. That's the full
experience minus the OS lockdown.

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
no way to wander off into Android. We use **FreeKiosk** (MIT, open-source) on stock-Android
Lenovo tablets (M9/M11). (FireOS is not supported — see plan.)

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
   adb shell dpm set-device-owner uk.freekiosk/.AdminReceiver   # confirm exact component in app docs
   ```
   Skip this for a quick setup; the home-app + immersive settings already give a solid kiosk.
5. **Log in** to HA as the panel's profile (see §4). The webview keeps the session.

> Built-in screensaver alternative: FreeKiosk also has a **web-page screensaver mode** you
> can point at `https://immich-kiosk.kblab.me?disable_ui=true`. We instead drive the
> screensaver *inside* the dashboard (wallpanel), so you don't need it — but it's there if
> you ever want the photos even on a non-HA page.

---

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
