# Home IoT Roadmap

Forever-document for the home smart-home buildout. Tracks the multi-phase Home
Assistant work, the hardware shopping list, the per-room rollout plan, and the
decisions explicitly out of scope so they don't get re-litigated.

## Vision

Home Assistant on `home-k3s` is the smart-home brain. Google Home /
Nest / Chromecast devices are the voice + ambient-display front-end. Dedicated
wall tablets are the touch control surface. mmWave sensors handle presence.
Every IoT device — current and future — is added through HA and re-exposed to
Google for "Hey Google" control. All config is in Git, all additions are
runbook-driven.

End state: walking into any room, voice control works, the wall panel shows
the right things, presence drives screen wake + lighting, and any new device
(Zigbee bulb, ESPHome sensor, Nest thermostat) is a 3-step PR away.

## Current state (2026-06)

| What | Where | Status |
|---|---|---|
| Home Assistant (k3s) | `apps/home-assistant/`, `hass.kblab.me` | Running. YAML-managed Lovelace, packages, dashboards. |
| 3D-printer monitoring | Bambu A1 + Neptune via `printing_neptune.yaml` | Running. |
| Cast + TTS to Google Homes | `voice_targets.yaml` package + Voice dashboard | **Phase 1 shipped 2026-06-03 (PR #23).** |
| External access | LAN only (`hass.kblab.me` via AdGuard rewrite) | Phase 2 pending — Tailscale + CF Tunnel. |
| Voice control via "Hey Google" | None | Phase 3 pending. |
| Nest thermostats / cameras in HA | None | Phase 4 pending. |
| MQTT broker → HA | `apps/mosquitto/` exists but unwired | Phase 5 pending. |
| New IoT (Zigbee, ESPHome) | None | Phase 5 pending — needs Zigbee stick decision. |
| Wall control panels | None | Phase 5.5 pending — hardware purchase confirmed. |
| Creative voice pipelines (OpenRouter → ElevenLabs → cast) | None | Phase 6 pending. |

## Phase roadmap

Source-of-truth plan: `~/.claude/plans/plese-tell-me-what-radiant-frog.md`.
Land each phase as its own PR, green before the next begins.

| # | Phase | What ships | Status |
|---|---|---|---|
| 1 | **Cast + TTS to Google Homes** | `cast:` discovery, `tts.google_translate_say`, `script.tts_announce`, `script.tts_announce_all_cast`, Voice dashboard | ✅ PR #23 merged 2026-06-03 |
| 1.5 | **mDNS for cast discovery** (open decision) | One of: `hostNetwork: true` on HA Deployment, manual `cast:` hosts, Avahi reflector DaemonSet | ⏳ decision pending |
| 2 | **Tailscale Operator + mobile access** | Tailscale Operator Flux Kustomization, HA Service annotated, `internal_url`/`external_url`/`trusted_proxies`, mobile Companion app works off-LAN | ⏳ pending |
| 3 | **Google Assistant Smart Home Action** | `hass.kennethblack.me` via cloudflared, Google Cloud project + Actions Console, `google_assistant:` package, SOPS service-account secret | ⏳ pending |
| 4 | **Nest SDM (Google devices INTO HA)** | `nest:` package, Device Access Console registration ($5 one-time), Pub/Sub subscription | ⏳ pending |
| 5 | **MQTT + new IoT pipeline** | Wire `mosquitto` to HA, deploy `zigbee2mqtt` + `esphome-dashboard` apps, document the "add a new IoT category" 3-step pattern | ⏳ pending |
| 5.5 | **Wall control panels** | First Lenovo Tab M11 in the kitchen, Apollo MSR-2 mmWave sidecar, optional HA Voice PE — see [hardware roadmap](#hardware-roadmap) | ⏳ pending |
| 6 | **Creative voice pipelines** | `rest_command.openrouter_chat` + `rest_command.elevenlabs_tts`, generalized `script.llm_speak` with random voice pool, Lovelace triggers | ⏳ pending |
| 7 | **Runbook + lessons** | This doc + `apps/home-assistant/README.md` updates + `~/.agent/lessons/home-config.md` entries | ⏳ pending |

## Hardware roadmap

Researched 2026-06-09 — full cited report at
[`claudedocs/research_ha-wall-panel-hardware_2026-06-09.md`](../claudedocs/research_ha-wall-panel-hardware_2026-06-09.md).
The picks below survived an adversarial verify pass; rejected alternatives are
in the [Out of scope](#out-of-scope) section.

### Phase 5.5 shopping list — first room

| Item | Cost | Why | Notes |
|---|---|---|---|
| **Lenovo Tab M11** (Wi-Fi, 4GB/64GB) | $130–180 (target ~$140 on sale) | OEM Battery Maintenance toggle caps charge at 40–60% — verified by independent 1.5-year test (97% battery health after 67 cycles). Stock Android, no FireOS sideloading. Quad speakers + Dolby Atmos. Ambient light sensor (unique in budget Android class). | Lenovo direct or Best Buy doorbuster. Avoid LTE variant. |
| **Apollo MSR-2** mmWave presence sensor | ~$35 | LD2410B mmWave, 6m still-detection, ESPHome native (first ESPHome device with "Works With Home Assistant" cert). Drives screen wake + room-based automations. | apolloautomation.com |
| **USB-C keystone insert** | ~$10 | Flush USB-C run from in-wall outlet to tablet — single cable visible. | Poyiccot or DataPro on Amazon |
| **3D-printed wall mount for Lenovo Tab M11** | ~$25 | Push-button release + USB-C cutout. Print yourself or buy on Etsy. | Printables #1490590 |
| **Fully Kiosk Browser license** | $8 (one-time, per device) | Real kiosk lockdown, motion-wake via front camera, charger-state HA entity. | fully-kiosk.com |
| **Subtotal — kitchen** | **~$220** | | |

### Phase 5.5 optional add-ons (decide later)

| Item | Cost | Why | Notes |
|---|---|---|---|
| **HA Voice Preview Edition** | $60 | Purpose-built far-field voice satellite with XMOS XU316 echo-cancelling mic array. Pairs with the Tab M11 on the same wall when the tablet's mic isn't enough in a noisy room. | Nabu Casa shop — no Voice PE 2 announced as of mid-2026, buying now is the consensus advice. |
| **Sonoff ZBDongle-E** Zigbee stick | $20 | For Phase 5 zigbee2mqtt deployment. Plug into one Pi via USB. | itead.cc |
| **Everything Presence Lite** (alternate to Apollo MSR-2) | $35–39 | LD2450 multi-target presence (X/Y coords, up to 3 targets), Bluetooth proxy bonus. | shop.everythingsmart.io |
| **Aqara FP2** (premium presence) | $58 | Multi-zone (up to 30), best for hallway covering multiple panels with one sensor. | Skip for one-room install; revisit if scaling to 3+ panels. |

### Phase 5.5 — per-room scale-out

Add one room at a time. Each subsequent room is one Tab M11 + one mmWave +
one mount + one USB-C run, ~$220 each. The HA Voice PE only adds value in
rooms where the existing Google Home doesn't cover the listening area.

Recommended rollout order (highest-utility-first):

1. **Kitchen** — most-used dashboard surface, the announcement target.
2. **Bedroom** — bedside controls + presence-driven lighting.
3. **Living room** — secondary; Nest Hub already covers the glanceable case via Phase 1 cast.
4. **Office / lab** — controls for cluster/printer dashboards.

Total at 4-room buildout: ~$880 + 1× Voice PE wherever the Google Home is too
far ($940).

### Phase 5 hardware

| Item | Cost | Why | When |
|---|---|---|---|
| **Sonoff ZBDongle-E** | $20 | Zigbee 3.0 coordinator for zigbee2mqtt. Plug into pi5-master or thinkcentre via USB. | Before deploying `apps/zigbee2mqtt/` |
| **Zigbee starter devices** | varies | Whatever Zigbee bulbs/switches/sensors are wanted; auto-paired through z2m. | After Phase 5 ships |

### Phase 6 — no new hardware

OpenRouter + ElevenLabs are API services. Existing Google Homes are the cast
targets. Phase 6 is pure software.

## Open decisions (need a call before the next phase)

1. **mDNS for cast discovery (Phase 1.5).** Three options laid out in the
   plan file. Recommendation: `hostNetwork: true` on the HA Deployment.
   1-line change, unblocks every future Zeroconf integration (HomeKit, Shelly,
   Sonos, the ROAMiQ-class market, the Apollo sensors). Cost: HA pod implicitly
   pinned to one node, but it already is via local-path PVC.
2. **Tailscale Operator install method (Phase 2).** Helm chart via HelmRelease
   vs. plain manifest under `clusters/home/infrastructure/`. Helm is the
   official path; plain manifest is more transparent. Recommend Helm.
3. **Nabu Casa vs self-hosted (Phase 3 + 4).** Already decided self-hosted in
   the original plan. Re-litigate only if Cloudflare Tunnel auth gets ugly.
4. **Zigbee stick host (Phase 5).** USB stick must be on a node that won't get
   recycled. Recommend `thinkcentre` (the Forgejo node — least disruption to
   homelab).

## Out of scope (intentionally — don't re-research these)

- **Nabu Casa Home Assistant Cloud subscription** — rejected in favor of
  Tailscale + Cloudflare Tunnel. Self-hosted infrastructure already exists.
- **Fire HD as the wall-panel default** — superseded by Lenovo Tab M11.
  Lithium swell on 24/7 charge + FireOS sideloading + Amazon OTA breakage are
  not worth the $50 savings.
- **ROAMiQ purpose-built HA display** — verified $599, RV/van-life market,
  "built on HA" is marketing-speak for Android + HA Companion.
- **NSPanel Pro 120 (Gen 1)** — Gen 2 ships June 2026 at similar price.
  Wait if the category is interesting.
- **Pixel Tablet** — viable but the dock-as-Cast-media_player claim couldn't
  be independently verified for 2026, and the Android 16 QPR2 "hard 80% cap"
  story turned out to be a bug fix to a flaky existing toggle, not a new
  feature. Lenovo Tab M11 wins on verified longevity.
- **Pi 5 + DSI DIY wall panel** — DRAM shortage pushed builds to $175–300, no
  longer the value pick. Pi stays as the HA server, not the panel.
- **Reflashing firmware on Google Home / Nest / Echo Show devices** — not a
  thing in 2026. Stock devices, no rooting.
- **Wallee mounting system** — premium feel, premium price, community moved
  on to 3D-printed or ELAGO mounts.

## Verification at each milestone

A new IoT device, added in one sitting, must traverse the whole stack
end-to-end to declare the platform real:

1. Plug in an ESPHome device → it appears in HA via Phase 5.
2. Friendly-rename to "Desk Lamp" → entity becomes `light.desk_lamp`.
3. *"Hey Google, sync my devices"* on the Nest Mini → Google Home app sees
   Desk Lamp (Phase 3 round-trip).
4. *"Hey Google, turn on the desk lamp"* → it turns on.
5. From cellular (Tailscale on), open the HA mobile app → dashboard shows
   Desk Lamp state changing in real time (Phase 2).
6. Walk into the kitchen → MSR-2 mmWave triggers → Tab M11 wakes from
   screen-off → kitchen dashboard renders (Phase 5.5).
7. Trigger `script.llm_speak` with `target: media_player.kitchen_nest_mini`
   → cloned voice insult plays (Phase 6).
8. Cast the launcher to the Nest Hub from your phone (Phase 1) — still works
   after all the above.

If all 8 pass: the platform is real. Adding the 11th device should be a
3-step PR, not a runbook of its own.

## Related docs

- Active plan (mutable, source of truth):
  `~/.claude/plans/plese-tell-me-what-radiant-frog.md`
- Hardware research (cited, with verify column):
  `../claudedocs/research_ha-wall-panel-hardware_2026-06-09.md`
- HA app runbook: [`../apps/home-assistant/README.md`](../apps/home-assistant/README.md)
- GitOps + Flux: [`gitops.md`](./gitops.md)
- Ansible host layer: [`ansible.md`](./ansible.md)
- Homelab catalog: [`homelab-catalog.md`](./homelab-catalog.md)
