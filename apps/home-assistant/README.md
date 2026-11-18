# Home Assistant on K3s

Home Assistant deployment for the home K3s cluster with Git-managed dashboards and homelab monitoring.

## Access

| Method | URL |
|--------|-----|
| Canonical | `https://hass.kblab.me` |
| Port Forward | `kubectl port-forward -n home-assistant svc/home-assistant 8123:8123` then `http://localhost:8123` |

### DNS Setup

Add DNS rewrite in AdGuard Home (Pi 3) at **Filters → DNS Rewrites**:
- Domain: `hass.kblab.me`
- Answer: `192.168.1.124`

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Local Access                                                     │
│   Browser → AdGuard DNS → Traefik (192.168.1.124) → HA Pod       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Cluster Monitoring                                               │
│   HA Pod → Prometheus API (monitoring.svc:9090) → REST Sensors   │
└─────────────────────────────────────────────────────────────────┘
```

## Layout

Repo-managed Home Assistant files live under `apps/home-assistant/config/`:

- `configuration.yaml`: base HA config, proxies, Lovelace mode
- `packages/`: Prometheus-backed REST sensors and derived template sensors
- `ui-lovelace.yaml`: default dashboard definition
- `dashboards/`: views in tab order: `Overview`, `Devices`, `Local`, `Ops`, `CI`, `Admin`
- `automations.yaml`, `scripts.yaml`, `scenes.yaml`: checked-in defaults so a fresh PVC can boot cleanly

These files are bundled into a generated ConfigMap and synced into the HA PVC on each rollout. Runtime state such as `.storage` stays on the PVC and is not repo-managed.

Custom components (e.g. `ha-bambulab`) are installed via init containers with pinned versions. Integration config entries are seeded from K8s Secrets into `.storage/core.config_entries` on first boot.

## Features

### Homelab Monitoring

| Use Case | Description |
|----------|-------------|
| Cluster status | CPU, RAM, and disk sensors for nodes 1-5 |
| Service health | Home Assistant, Traefik, and LiteLLM container metrics |
| Mac monitoring | Mac Mini node exporter metrics |

### Site status (generated)

`config/packages/site_status.yaml` holds one `binary_sensor` per site, read from the blackbox probes; `fleet_alerts.yaml` and `voice_health.yaml` use them. [`scripts/gen-ha-launcher.py`](../../scripts/gen-ha-launcher.py) writes it from the ingresses, together with the kblab.me front door and the probe list ([`apps/front-door/README.md`](../front-door/README.md)), so do not edit it by hand.

### Phone overview widget

`sensor.agent_widget` is the main mobile view: the fleet and the house on an Android home screen. `hub phone --json` in dotfiles makes the fleet rows from the join the hub's Home tab reads, `agent-mqtt-publish` sends them as `sensor.agent_overview`, and `config/custom_templates/phone_widget.jinja` lays them out. That file holds all the HTML and every colour, one macro per section. `config/packages/agents.yaml` only calls it. The widget does not scroll and clips at the bottom, so sections run most important first and a green section is one line:

1. System: domains down, warn and up, and the time it was rendered. Read in the cluster, so it still shows with the workstation off.
2. RED: every `cc:red_now` item with its domain and age, newest first, 6 at most then `+N more`. Then red default branches, sources the workstation could not read, any domain down with no red watch of its own, the domains at warn, and an active `input_text.home_alert`.
3. Needs you: the count split by kind, the top projects, and the oldest ask.
4. Done today: queue items done and PRs merged, then the three busiest projects, each with its own counts and `+N more` for the rest.
5. Agents: session counts, sessions late against their ETA, and the first KPI off target with its limit and how many more.
6. Queue: running, ready and held, then how long the oldest ready item has waited and cost per PR against 28 days. Then CI, naming only what is down.
7. Projects: how many have work in flight, then the first two in flight, each with its percent, ETA and sessions.
8. Claude: each Claude account's weekly use (not the codex or LiteLLM sources), amber when one has no room, and 24h spend.
9. The house: weather and the next event across every calendar.

Every section opens with a bold `Title: value`. Colour marks only what is wrong: a title goes amber or red with its section, and only red items stay red in full, so amber never covers a whole block. Detail lines under a title are grey, so the titles and their values stand out.

It is HTML in an attribute because the Companion app's Template widget renders HTML and an HA state stops at 255 characters. The state is the worse of health and the fleet rows: `ok`, `warn` or `critical`. Text from outside (ask titles, calendar events, the alert) is escaped in the macros.

`scripts/widget-preview.sh` shows the widget as the phone would, at every size in `scripts/phone-widget-sizes.json`: 4x5 (357x600 dp, the whole home screen), 4x4, 4x3 and 4x2, each inside a mock S25 home screen. The sizes were measured off an S25 home-screen screenshot at 2.625 px per dp. It renders this checkout's `phone_widget.jinja` with the live `hub phone --json` rows through Home Assistant's `/api/template`, so the preview and the phone share one renderer, and opens `~/.cache/phone-widget/preview.html`. `--check` lays the page out in headless Chromium, prints each size's cut sections, writes `preview.png`, and exits 3 when a section that size lists in `must_show` is cut (`*` means nothing may clip). `--deployed` shows what the phone has now, `--rows FILE` takes saved rows, `--watch` renders again on every save of the jinja. It needs `rbw unlock` (entry `HASS token`). Chromium draws the HTML where the phone uses Android's text view, so a wrap can land one word differently.

Setup on the phone: long-press the home screen -> Widgets -> Home Assistant -> Template, with the template `{{ state_attr('sensor.agent_widget', 'html') }}` and the smallest text size that reads well. About 4x2 shows parts 1 to 4, about 4x4 adds the fleet lines. A widget already on that template picks all of this up by itself. One still on the old `states('sensor.agent_widget')` template shows only that one word until you edit it. It refreshes when the sensor changes, and tapping it forces a refresh. For a color dot, add an Entity state widget on `binary_sensor.agents_need_attention`. Pages that cannot wait still arrive by ntfy; the widget is only a glance.

| Symptom | Fix |
|---|---|
| Widget shows old numbers, with or without a small red sync mark at the bottom | The time on the System line is when it last rendered. The red mark means the app's last render failed, for example during an HA restart, and it keeps the old text until a render works. Tap the widget to render now; there is no need to re-save it. The app renders on each screen-on only while its process is alive, so if the mark sticks, set the Companion app's battery use to Unrestricted and take it out of Samsung's sleeping apps. If `sensor.agent_sessions` is `unavailable`, its 900 s MQTT expiry ran out, so the workstation publisher (`agent-mqtt-publish.timer`) is down. |
| Widget shows only `ok`, `warn` or `critical` | It still has the old template: edit it to the one above. |
| Red line `no fleet overview` | `sensor.agent_overview` has no rows: its 900 s MQTT expiry ran out, so `agent-mqtt-publish` has stopped, or `hub` on the workstation is too old to know `phone`. Run `hub phone --json` there. |
| Widget shows `unknown` or nothing after a change | `phone_widget.jinja` failed to load. Read the HA log for `TemplateError` or `phone_widget`, and check the init container copied the file to `/config/custom_templates/`. |
| A red line has no age | Its watch has no `since_epoch` and no matching `binary_sensor`, or it is a node or feed row. Those sort first. A `binary_sensor` age restarts with Home Assistant. |
| The bottom lines are cut off | The widget does not scroll, so the least important lines go first. Make it taller or pick a smaller text size. |
| `System ?` in amber | `sensor.system_health` got no rows. It is `unavailable` when Prometheus did not answer Home Assistant; when it answers, query `cc:domain_status` for the rules in `apps/monitoring/prometheus-rules-command-center.yaml`. |
| The Claude line lists codex, lazer or other sources, or an account that was removed | The REST query lost its `kind="claude"` filter, or the render is old: the time on the System line says when it last rendered, and tapping the widget renders it now. |
| The accounts line reads `unknown` | The `Agent accounts` REST sensor got no data. Query `claude_account_week_used_percent` in Prometheus; when it is empty, `claude-acct-usage.timer` on the workstation has stopped writing. |

### 3D Printer Monitoring

| Use Case | Description |
|----------|-------------|
| Neptune (Moonraker) | REST sensors polling Moonraker through the in-cluster `neptune` service — temps, progress, print state |
| Bambu A1 (ha-bambulab) | Custom component installed via init container, config seeded from K8s Secret |
| Dashboard | Dedicated `3D Printing` view with status, progress gauges, and temperature cards |

### Robot Vacuum (Tapo RV30)

| Use Case | Description |
|----------|-------------|
| Tapo RV30 (`tapo_rv30`) | Local-only (TPAP/SPAKE2+) reverse-engineered custom component ([`epg-pers/tapo-rv30-ha`](https://github.com/epg-pers/tapo-rv30-ha) pinned at `v0.3.0`), installed via the `install-tapo-rv30` init container, config seeded from K8s Secret by `seed-tapo-rv30`. Exposes a `vacuum.*` entity (start/pause/stop/dock), fan-speed / water-level / clean-passes selectors, battery + error + consumable sensors, a live map image, and the `tapo_rv30.clean_rooms` service for per-room cleaning. |
| Credentials | `tapo-rv30-secret.yaml` (SOPS) — `host` (RV30 LAN IP), `username` + `password` (TP-Link account). Runtime is LAN-only; the account creds are only used in the SPAKE2+ handshake. |
| ⚠️ Firmware | This integration speaks the **TPAP/SPAKE2+** protocol on port `4433` — the scheme firmware `1.3.x` *switched to*, so it targets current firmware (confirmed through `1.3.2`; this RV is on `1.3.3`). What `1.3.x` broke is the **official `tplink`** integration's older protocol, which we deliberately don't use. The real risk is a *future* firmware that changes the protocol again — disable auto-update to stay on a known-good build. Fallback: Matter (start/stop/dock only, no room cleaning). |

### Litter Box (Fumoi / LocalTuya)

| Use Case | Description |
|----------|-------------|
| Fumoi "Cat Litter Box M4" (`localtuya`) | Generic **Tuya** device controlled fully on-LAN via **LocalTuya** ([`xZetsubou/hass-localtuya`](https://github.com/xZetsubou/hass-localtuya) pinned `2025.11.0`), installed via the `install-localtuya` init container. Discovered: Tuya category `msp`, protocol **v3.5**, IP `192.168.1.215`. Exposes **Clean** / **Start** buttons, **Mode** + **Light Color** selects, **Auto Clean** switch, **Clean Delay** + **Light Brightness** numbers, and `status` / `cat_weight` / `excretion_*` / `use_time` / `trigger_source` sensors. (No power control — the unit just plugs in.) |
| Credentials | `localtuya-secret.yaml` (SOPS) — `device-id`, `local-key`, `host`, `protocol-version`. Extracted via the reusable Tuya recipe in [`docs/smart-home-control.md`](../../docs/smart-home-control.md); keys stay LAN-only after discovery. |
| Config entry | Seeded by the `seed-localtuya` init container as a `no_cloud` hub entry (`localtuya` domain, `ENTRIES_VERSION=4`). Unlike Bambu/Tapo (flat 3-field entries) it's a **nested, versioned structure with a per-DP entity map**, authored against `config_flow.py` @ 2025.11.0 (per-platform required keys) + validated locally, not blind-seeded. The seed is **version-gated** (`SEED_VERSION` marker at `/config/.localtuya_seed_version`): bump it to re-seed a corrected map — the init container drops the stale entry and purges its entity-registry rows so old slugs don't linger. |
| DP labels | **Corrected 2026-07-03** from the Tuya cloud spec **+ a live button-by-button correlation** (raw `tinytuya` poll while pressing each app control). Fixed the sensor guesses (DP6 `cat_weight`, DP7 `excretion_times_day`, DP8 `excretion_time_day` — **not "Litter Level"**), and the live pass then: proved **DP1 `switch` is a dead code** (no power button — dropped), reassigned **DP9 → the "Clean" button**, mapped **DP102/DP107 to the Light colour/brightness**, dropped **DP108 `work_state`** (static noise), and confirmed **DP110 `data_identification`** is the real status (`Cat_into`/`Cleaning`/`Clean_Pause`). See the DP table in [`docs/smart-home-control.md`](../../docs/smart-home-control.md). |
| ⚠️ Gotcha | `localtuya/__init__.py` reads `region`/`client_id`/`client_secret`/`user_id` **unconditionally** (before the `no_cloud` branch), so the seed includes those keys empty — omit them and setup `KeyError`s. The generic Tuya schema **lies**: it advertises functions the unit ignores (`switch`) and misnames the ones it uses (`doorbell_song` = light colour) — trust the live correlation over the schema names. `factory_reset` (DP23) is omitted (destructive). |

### Smart Plugs (Shelly Plug US Gen4)

| Use Case | Description |
|----------|-------------|
| 2× Shelly Plug US Gen4 (`shelly`) | UL-listed metered WiFi plugs (model `S4PL-00116US`, `gen 4`) on the **native, core HA `shelly` integration** (local RPC/WebSocket) — **no custom component**. Config entries seeded by the `seed-shelly` init container (two entries; schema mirrors HA core `shelly/config_flow.py` @ 2026.7: `data={host,port,sleep_period,model,gen}`, `unique_id=MAC` uppercase-no-colons, `VERSION=1 MINOR_VERSION=3`; idempotent on `domain+data.host`). Expose `switch.*` (relay) + power/voltage/current/energy/temperature `sensor.*`. Pinned DHCP `.104` (free) / `.105` (lamp). |
| Credentials | **None** — auth is disabled on the plugs, so hosts are inlined in the seed (nothing secret; no SOPS file). |
| ⚠️ MQTT ≠ HA discovery | Shelly Gen4 MQTT is **RPC-style and emits no HA discovery** — MQTT alone yields zero HA entities, which is why control comes from the native integration. The plugs *also* publish MQTT to Mosquitto (`192.168.1.20:31883`) **in parallel, only** so `apps/mqtt2prom` (mqtt-exporter, subscribes `#`) feeds per-plug watts to Grafana (`iot-fleet-devices` dashboard, "Smart Plugs — Energy" row). See [`docs/smart-home-control.md`](../../docs/smart-home-control.md) for the always-hot-outlet + power-cycle gotchas. |

### IR/RF Blaster (Broadlink RM4 Pro)

| Use Case | Description |
|----------|-------------|
| Broadlink RM4 Pro (`broadlink`) | IR + 433 MHz RF blaster on the **native, core HA `broadlink` integration** — **no custom component**. Config entry seeded by `seed-broadlink` (schema mirrors HA core `broadlink/config_flow.py` @ 2026.7: `data={host,mac,type,timeout}`, `type=21003` = devtype `0x520B` "RM4 pro", `unique_id=mac.hex()`, `VERSION=1`; idempotent on `domain+data.mac`). Pinned DHCP `.103`. The device re-authenticates at runtime — if `auth()` fails the unit is cloud-locked (unlock in the Broadlink app). |
| Credentials | **None** — host/MAC inlined in the seed. |
| ⏳ Codes | IR (AC) + 433 RF (UFO lamp) codes are learned via the `broadlink.learn` service — **never the phone app** (app codes are cloud-encrypted). A proper AC `climate` entity needs **SmartIR** (init container + `climate:` package) with a device code; the wall/Devices Climate sections auto-populate once it exists. |
| ⚠️ Firmware | **Decline firmware updates** — newer RM4 firmware can lock out the local control HA needs. |

## Deployment

```bash
# Render locally
kubectl kustomize apps/home-assistant

# Deploy all resources directly
kubectl apply -k apps/home-assistant/

# Render the Flux entrypoint used by the live cluster
kubectl kustomize infra/flux/apps/prod
```

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | Creates `home-assistant` namespace |
| `pvc.yaml` | Persistent volume for HA config |
| `deployment.yaml` | Main HA deployment with managed config sync init container |
| `service.yaml` | ClusterIP service on port 8123 |
| `ingress.yaml` | Traefik-routed Ingress for `hass.kblab.me` |
| `kustomization.yaml` | Generates the managed config ConfigMap from checked-in files |

## Configuration

### Trusted Proxies

Home Assistant requires `trusted_proxies` config to work behind Traefik on K3s:

```yaml
# In /config/configuration.yaml (inside the pod)
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 10.42.0.0/16    # K3s pod network
    - 10.43.0.0/16    # K3s service network
    - 192.168.1.0/24  # Local network
```

### Managed Config Sync

The deployment copies repo-managed files into `/config` on every rollout:

```yaml
# Base config
/config/configuration.yaml

# Package files
/config/packages/*.yaml

# Dashboard files
/config/ui-lovelace.yaml
/config/dashboards/*.yaml
```

### Storage

Uses the K3s `local-path` storage class for the Home Assistant PVC.

## Troubleshooting

### Can't access via hass.kblab.me

1. Check DNS rewrite exists in AdGuard
2. Verify your device uses AdGuard as DNS
3. Test with: `nslookup hass.kblab.me`

### 400 Bad Request

Add `trusted_proxies` to HA configuration (see above).

### Check pod status

```bash
kubectl get pods -n home-assistant
kubectl logs -n home-assistant -l app.kubernetes.io/name=home-assistant
kubectl logs -n home-assistant deploy/home-assistant -c sync-managed-config
```

### Announce does nothing

| Symptom | Fix |
|---|---|
| Log: `Language '{{ states(...) }}' not supported` | A dashboard passes templates in `tap_action` data, which the frontend never renders. Use `custom:announce-card`, which sends plain values. |
| No error, no sound | `group.announce_rooms` in `packages/voice_targets.yaml` names players that are gone or all `unavailable` (their chips show greyed out). Compare it with `media_player.*` in Developer Tools > States. |
| Card says "recording needs an admin login" | HA allows media uploads only for admin users. Log the tablet into an admin profile, or send typed messages. |
| Card says "Microphone unavailable: NotAllowedError" | The browser blocked the mic: the page must be HTTPS, and a wall tablet needs FreeKiosk 1.2.20 or newer (`docs/wall-panels.md`, voice section). |
| Card change does not show on a tablet | Bump `?v=` on `announce-card.js` in `configuration.yaml`; the WebView caches `/local`. |

### Restart Home Assistant

```bash
kubectl rollout restart deployment home-assistant -n home-assistant
```

## Code-First Policy

All Home Assistant changes are managed through this git repository. No manual UI configuration.

- **Dashboards**: YAML-mode Lovelace, defined in `config/dashboards/`
- **Sensors**: REST platform in `config/packages/` — no config-flow integrations for new sensors
- **Custom components**: Installed via init containers with pinned versions, not HACS
- **Integration config**: Seeded into `.storage/core.config_entries` from K8s Secrets on first boot
- **Credentials**: Stored in SOPS-encrypted K8s Secrets (age key)
- **Runtime state**: `.storage/` persists on PVC — the only non-git-managed state

When adding a new device or integration:
1. Prefer HA's built-in REST/MQTT/template platforms (100% YAML)
2. If a custom component is required, add an init container to download a pinned release
3. Seed the config entry from a K8s Secret if the integration is config-flow-only
4. Never install HACS or use the HA UI for configuration

## Away alarm

The Control and Dog views have the `alarm_control_panel.away` card. It can be armed or disarmed by hand without a code. Automatic arming requires `zone.home` to be 0 and every person to have a known state for ten continuous minutes; an unknown or unavailable person blocks it. Returning home disarms it. Frigate person occupancy on either camera triggers the armed alarm and a high-priority home push. Barking while armed sends a default-priority push at most once per 15 minutes. Arm and disarm pushes are low priority. The panel stays outside the Assist allow-list.

Companion-app presence is still required for both people; the existing human-sheet row owns that setup. Persons and their trackers remain in HA runtime storage. After HA restarts or automations reload, the ten-minute presence window starts again.

| Symptom | Check |
|---|---|
| Empty home does not arm | Check `zone.home` and every `person.*`; unknown or unavailable presence blocks automatic arming. Confirm companion-app location updates and tracker assignments. |
| Dog movement triggers an alert | Check Frigate's person occupancy classification; generic motion is not an alarm input. |
| Another bark sends no push | Check the bark automation's `last_triggered`; the cooldown is 15 minutes across arm/disarm cycles. |

Validate the guard and cooldown in the HA image by copying `scripts/test-ha-away-alarm.py` and `config/packages/away_alarm.yaml` into a temporary directory in the pod and running `python test-ha-away-alarm.py away_alarm.yaml`. Run HA `check_config --fail-on-warnings` against a separate temporary config before deploying through Flux; without that flag a package schema error can still return exit code 0.

## Related Services

| Service | Purpose |
|---------|---------|
| Prometheus | Metrics source (kube-prometheus-stack) |
| Traefik | Ingress controller |
| AdGuard Home | DNS server (on Pi 3) |
| Frigate | NVR for cameras |
