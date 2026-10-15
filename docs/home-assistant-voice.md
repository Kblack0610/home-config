# Home Assistant voice assistant — "Binks"

A staged, code-first conversational assistant for the home. Backed by the cluster
**LiteLLM** gateway (model `assist (gemini-flash)` — a Gemini -> Lazer -> local
fallback chain), able to control exposed devices.
Built and shipped in stages so each layer is tested in isolation.

| Stage | What | Status |
|-------|------|--------|
| 0 | Fix broken cast TTS announcements (`tts.google_say`) | ✅ shipped (PR #77) |
| 1 | **Text chat** — Binks conversation agent + home control | ✅ shipped |
| 2 | **Press-to-talk voice** — STT via LiteLLM whisper + Google TTS | ✅ shipped |
| 3 | **Wake word (server)** — `wyoming-openwakeword` for *hardware* satellites; unused by the tablet card | ◑ server side shipped |
| 4 | **Wall-tablet wake word** — in-browser `voice_satellite` card → Binks; custom `binks` microWakeWord live on all 4 tablets | ✅ shipped (`binks` live) |
| 5 | **HA Voice PE hardware satellites** — 2 pucks, ESPHome, on-device wake → Binks | ✅ both adopted, on Binks pipeline (Kitchen + Bedroom) |
| 6 | **Local-first room + fixture commands** - built-in intents before Binks, rooms on every light and satellite | shipped 2026-10-12 |

Everything is code-first: custom components are vendored by `install-*` init containers
and config entries are written by idempotent `seed-*` init containers (see
`apps/home-assistant/deployment.yaml`). No HACS, no UI clicking, reproducible from git.

---

## Why these choices

- **HA is the `stable` container in k3s (2025.11.x), not HA OS** → no add-ons; every
  helper service is a plain container wired by config.
- **Conversation agent = `jekalmin/extended_openai_conversation`**, now pinned to `3.0.0-beta11` (see `install-extended-openai-conversation` in `deployment.yaml`); it exposes `conversation.binks`. The 1.1.0 notes below are history from the HA 2025.11 era.
  HA's *built-in* OpenAI Conversation cannot target a custom `base_url` in 2025.11
  (hard-locked to api.openai.com, on the Responses API), so it can't reach LiteLLM.
  `1.1.0` is the last EOC release compatible with HA 2025.11 (2.x needs HA ≥ 2026.3)
  and it requires `openai~=2.2.0`, which HA 2025.11 already bundles → no runtime pip.
- **STT (Stage 2) rides LiteLLM's `stt (whisper-turbo)`**, not a Wyoming whisper
  container — the litellm configmap already provisioned that model "for future
  voice-input use cases". Zero new k8s deployments for Stages 1–2.
- EOC 1.1.0 uses the **legacy conversation-agent API**, so **the agent_id IS the
  config entry_id** (there is no `conversation.binks` entity). The pipeline seed looks
  the entry_id up dynamically; verification must use it too.

---

## The LiteLLM key

Binks authenticates to LiteLLM with a scoped virtual key (alias `home-assistant-assist`),
minted out-of-band per `apps/litellm/README.md` and stored SOPS-encrypted in
`apps/home-assistant/litellm-assist-secret.yaml` (Secret `litellm-assist-credentials`,
key `api-key`). Allowed models — Binks's 3-tier chat fallback chain plus STT:

```
assist (gemini-flash)  # PRIMARY — gemini-3.5-flash-lite on GEMINI_API_KEY_FAMILY
fast (gpt-oss-120b)    # fallback tier 2 — Lazer (verified tool-calling)
fast (Qwen3-4B-8bit)   # fallback tier 3 — local MLX (best-effort control)
stt (whisper-turbo)    # Stage 2 speech-to-text
```

Change the allowed-models WITHOUT re-minting (keeps the same key value, so no
secret edit) via `/key/update`:

```bash
MASTER_KEY=$(kubectl -n ai-gateway get secret litellm-secrets -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
AKEY=$(kubectl -n home-assistant get secret litellm-assist-credentials -o jsonpath='{.data.api-key}' | base64 -d)
curl -sX POST https://llm.kblab.me/key/update -H "Authorization: Bearer $MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"key\":\"$AKEY\",\"models\":[\"assist (gemini-flash)\",\"fast (gpt-oss-120b)\",\"fast (Qwen3-4B-8bit)\",\"stt (whisper-turbo)\"]}"
```

Re-mint / rotate the key VALUE (only when the value itself must change):

```bash
MASTER_KEY=$(kubectl -n ai-gateway get secret litellm-secrets -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
curl -sX POST https://llm.kblab.me/key/generate -H "Authorization: Bearer $MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"key_alias":"home-assistant-assist","models":["assist (gemini-flash)","fast (gpt-oss-120b)","fast (Qwen3-4B-8bit)","stt (whisper-turbo)"],"tpm_limit":100000}'
# put .key into apps/home-assistant/litellm-assist-secret.yaml stringData.api-key, then: sops -e -i <file>
```

---

## Model routing & resilience

Binks's `chat_model` is one LiteLLM group, `assist (gemini-flash)`, with an ordered
fallback chain (`router_settings.fallbacks` in `apps/litellm/config.yaml`):

```
assist (gemini-flash)     gemini-3.5-flash-lite  Google (GEMINI_API_KEY_FAMILY)  PRIMARY, 8s timeout
  -> fast (gpt-oss-120b)  gpt-oss-120b       Lazer (Deloitte proxy)           on Gemini error
  -> fast (Qwen3-4B-8bit) Qwen3-4B           Mac Studio MLX (local)           last resort
```

- **Gemini is primary on purpose:** it keeps personal home voice off the Lazer
  (Deloitte) proxy in the normal path — Lazer is only touched on a Gemini failure. The
  local tier answers but emits text-style tool calls, so device *control* is
  best-effort there; it is a "still talks during a cloud outage" net, not a full tier.
- **Two Gemini keys, don't confuse them:** `GEMINI_API_KEY` is embeddings-only (chat
  generateContent quota = 0); `GEMINI_API_KEY_FAMILY` has chat quota and is the assist
  route's key. Both live in `apps/litellm/secret.sops.yaml`.
- The EOC config-entry seed **reconciles** `chat_model` on every HA start (not
  create-only), so changing the model in `deployment.yaml` propagates to the running
  entry on the next roll instead of stranding it on the old value.

### Which model actually served a turn

- **Terminal:** `GET /spend/logs?api_key=<assist key>` on the gateway returns
  `{model, status}` per request — tail it while testing.
- **Response headers:** `x-litellm-model-api-base` (which upstream served it),
  `x-litellm-attempted-fallbacks` (did it fail over).
- **Dashboard:** langfuse.kblab.me — the `langfuse_otel` callback traces every call
  (model, fallback chain, latency, cost).

### Incident — 2026-07-27: Binks silently dead

`chat_model` was `vlm (gemini-3-flash)`, a Lazer route. Lazer's catalog dropped the
`gemini-3-flash` name (the 2026-07-24 "Claude-behind-Lazer" remap), so **every** turn
400'd (`unknown model: gemini-3-flash`) — voice fully broken. It went unnoticed because
gatus only probes LiteLLM `/health/readiness` (gateway up), which stays green while a
single model route is dead. Fixed by moving to the `assist (gemini-flash)` chain
(PR #166). See **Monitoring** below — a synthetic assist-route probe is the fix so this
cannot degrade silently again.

---

## Monitoring & health

`apps/home-assistant/config/packages/voice_health.yaml` feeds the existing
`fleet_alerts.yaml` ntfy pipeline (topic `homelab-alerts`) with three
`device_class: connectivity` binary sensors:

- **`binary_sensor.binks_brain`** - a `command_line` probe POSTs a 1-token prompt to the real `assist (gemini-flash)` route with the scoped assist key every 30 min, with `disable_fallbacks: true`. ON = HTTP 200 from Gemini itself; with fallbacks on, the probe stayed green while Gemini was 429 all day (2026-10-15). This is what catches a dead model route (the 2026-07-27 incident) that gatus's LiteLLM `/health/readiness` check passes.
- **`binary_sensor.voice_kitchen_puck`** and **`binary_sensor.voice_bedroom_puck`** - OFF when the Voice PE puck's `assist_satellite` is unavailable (off WiFi, unplugged). The `for: 00:03:00` debounce in `fleet_alerts.yaml` absorbs a startup blip.

Still not alerted, on purpose: the four wall tablets. Their `assist_satellite.wall_*` goes unavailable on every dashboard reload and in night mode, so a liveness alert would be noise. Check them by hand from the entity states or the Assist pipeline debug view.

---

## Stage 1 — text chat + control (this ship)

Three init containers do the work (all in `deployment.yaml`):

1. `install-extended-openai-conversation` — vendors EOC `1.1.0` into `/config/custom_components`.
2. `seed-binks-conversation` — writes the EOC config entry: `base_url` = the in-cluster
   LiteLLM `/v1`, `chat_model` = `assist (gemini-flash)`, `use_tools: true`,
   `skip_authentication: true` (so HA boot doesn't depend on LiteLLM being up that
   instant — the key is verified out of band), and the **Binks persona prompt**.
3. `seed-binks-expose` — exposes an action-only allow-list of domains
   (`light, switch, scene, script, fan, cover, climate, media_player, vacuum,
   input_boolean` — **deliberately not `lock`/`alarm_control_panel`**) to the Assist
   `conversation` assistant so Binks can control them. Non-destructive: it never
   overrides an entity you've explicitly un-exposed in the UI.
4. `seed-binks-pipeline` — creates a **"Binks" Assist pipeline** (chat-only in Stage 1;
   stt/tts/wake are null) pointing at the Binks agent and makes it the **preferred**
   pipeline. The prior "Home Assistant" pipeline is kept selectable.

### Talk to Binks

- **UI:** the Assist dialog (top-right conversation icon, or the voice dashboard) now
  defaults to Binks.
- **Service / API:** `conversation.process`, with `agent_id` = the EOC entry_id.

Find the entry_id and test:

```bash
POD=$(kubectl -n home-assistant get pods -l app.kubernetes.io/name=home-assistant -o jsonpath='{.items[0].metadata.name}')
AGENT=$(kubectl -n home-assistant exec "$POD" -c home-assistant -- python -c \
  "import json;print(next(e['entry_id'] for e in json.load(open('/config/.storage/core.config_entries'))['data']['entries'] if e['domain']=='extended_openai_conversation'))")

TOKEN=$(rbw get "HASS token")
curl -sX POST https://hass.kblab.me/api/services/conversation/process \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$AGENT\",\"text\":\"Are you there, Binks?\"}" | jq '.response.speech.plain.speech'
```

Then in the Assist dialog, ask Binks to toggle a test light and confirm it acts.

### Control surface

Binks can act on whatever is exposed to the `conversation` assistant. Review/trim in
**Settings → Voice assistants → Expose**. The seed only *adds* a conservative default;
narrowing it there is safe and persists (the seed won't re-add what you remove).

### Fallbacks / knobs

- **Gemini failover is automatic** now — the `assist (gemini-flash)` group falls back
  to `fast (gpt-oss-120b)` (Lazer) then `fast (Qwen3-4B-8bit)` (local) at the LiteLLM
  router (see **Model routing & resilience**). No manual switch needed for a transient
  Gemini hiccup. To change the primary permanently, edit `chat_model` in
  `seed-binks-conversation` (the seed reconciles it on the next roll).
- To make the built-in local agent the default again: **Settings → Voice assistants →**
  set "Home Assistant" as preferred (the pipeline is still there).

---

## Local-first commands (room + fixture)

The Binks pipeline has `prefer_local_intents: True` (`seed-binks-pipeline` in `deployment.yaml`, rewritten on every boot). Every utterance is first tried against HA's built-in intents, strict match, exposed entities only. If one matches and finds a target, HA acts and answers itself with no LLM turn (`processed_locally: true` in the pipeline run). If nothing matches, or the match finds no target, the turn goes to Binks unchanged. Question-style intents (`HassGetState`, media search) are always left to Binks, because HA filters them out when the agent can control devices.

There is no custom grammar for device control. What makes it work is registry hygiene:

- every lamp is a `light` (lamp plugs are wrapped by `switch_as_x`, see `seed-switch-as-x-lights`), with a room and a speakable name;
- every satellite device has a room, which becomes the default area for its commands;
- names, rooms and aliases are declared in `apps/home-assistant/config/pyscript/room_layout.yaml` and seeded on boot (non-destructive; a UI edit wins).

### What you can say

| Say | Intent | Acts on |
|-----|--------|---------|
| "bedroom light off", "turn off the bedroom light" | HassTurnOff | the light named or in Bedroom |
| "living room lights on" | HassTurnOn | every light in Living Room |
| "light off", "turn on the light" (to a satellite) | HassTurnOn/Off | lights in the satellite's own room |
| "UFO light on", "light strips off", "monkey light on", "printer light on" | HassTurnOn/Off | that light, by name or alias |
| "turn off the office AC" | HassTurnOff | `switch.shellyplugusg4_acebe6f28334` |
| "never mind", "cancel", "forget it" | HassNevermind | ends the turn silently |
| anything else ("what's the weather", "dim it a bit and play jazz") | none | Binks |

Bare "stop" stays the built-in HassMediaPause; while a satellite is speaking, its on-device stop word handles it. `apps/home-assistant/config/custom_sentences/en/` holds only phrasings the built-ins miss (today: "cancel" and "forget it" for HassNevermind). Add a file there only after checking the built-ins with the recognizer below; do not grow a parallel grammar.

Kitchen has no lights yet, so "light off" to a Kitchen satellite finds no target and goes to Binks.

### Check what a sentence resolves to

Recognize without acting (pass a satellite `device_id` to test its room):

```
{"type":"conversation/agent/homeassistant/debug","language":"en",
 "device_id":"<satellite device id>","sentences":["light off"]}
```

over the websocket API. To test the full pipeline, send `assist_pipeline/run` with `start_stage`/`end_stage` = `intent` and `input.text`; the `intent-end` event carries `processed_locally`. Note that `POST /api/conversation/process` does NOT go through a pipeline: with no `agent_id` it always uses the built-in agent, and with `agent_id: conversation.binks` it always uses the LLM, so it cannot show the local-first behaviour.

### Registry changes made 2026-10-12

Made live through the websocket/REST API and mirrored into git (`room_layout.yaml`, `seed-switch-as-x-lights`), so a rebuild converges on the same state:

| Thing | Before | After |
|-------|--------|-------|
| Wall Office / Living Room / Bedroom / Kitchen satellite devices | no area | office / living_room / bedroom / kitchen |
| Lamp plugs 600fec, 6027f0, 6043c8, 6058b8, 60a188 | `switch` only | wrapped as `light.athom_plug_v3_<id>_switch` via the switch_as_x config flow (source switch hidden + unexposed, name copied) |
| Those five wrapper lights | no area | bedroom, office, office, living_room, living_room |
| `light.athom_plug_v3_613888_switch` | name "Athom Plug V3 613888 Switch", no area | "Monkey Light", living_room |
| `switch.athom_smart_plug_v3_613888_switch` | visible | hidden (user), unexposed |
| `light.athom_plug_v3_6043c8_switch` aliases | none | "UFO Light" |
| `light.athom_plug_v3_6027f0_switch` aliases | none | "Light Strips" |
| `light.shelly_plug_1`, `light.shelly_plug_2_lamp` (switch_as_x on the Office/Bedroom AC plugs) | exposed lights, so "lights off" could cut an AC | config entries removed |
| `switch.shellyplugusg4_acebe6f28334` / `_a1e8` (Office AC / Bedroom AC) | not exposed | exposed to Assist |
| Voice PE puck LED rings | exposed lights in kitchen/bedroom | unexposed (a room "lights on" would light the puck ring) |
| `light.a1_03900d5b1110644_chamber_light` (Bambu A1) | exposed, no name override (spoken as "A1_03900D5B1110644 Chamber light"), no area | named "Printer Light" by the `room_layout.yaml` seed on the next boot; still no area, on purpose |

---

## Stage 2 — press-to-talk (shipped)

Speech-to-text rides LiteLLM's `stt (whisper-turbo)`; the reply is spoken with the
existing Google Translate TTS. No new k8s deployment.

Two init containers (`deployment.yaml`) plus a pipeline update:

1. `install-openai-whisper-cloud` — vendors `fabio-garavini/ha-openai-whisper-stt-api`
   (domain `openai_whisper_cloud`), pinned to a **commit SHA** (no releases exist), no
   PyPI deps.
2. `seed-openai-stt` — config entry in **"Custom" provider** mode. **Gotcha:** in custom
   mode the component uses the URL *as-is* (it does NOT append `/v1/audio/transcriptions`),
   so the entry URL is the **full endpoint** `…:4000/v1/audio/transcriptions`. `model` =
   `stt (whisper-turbo)`. No auth check at load → boot stays independent of LiteLLM.
   Creates entity **`stt.binks_stt`**.
3. `seed-binks-pipeline` now sets `stt_engine` (resolved from the registry, else
   `stt.binks_stt`), `stt_language: en`, `tts_engine: tts.google_translate_en_com`,
   `tts_language: en` on the same Binks pipeline.

**Use it:** open the Assist dialog on the wall tablet or the Companion app and press the
**mic** button (browser/app mic — no satellite hardware). Say a command → LiteLLM
whisper transcribes → Binks acts → Google TTS speaks the reply.

Notes / knobs:
- Google Translate TTS is **non-streaming** (fetches the full clip) — fine, just not
  chunked. Keep `tts_language: en` to avoid `tts-not-supported`.
- Verified out of band (2026-07): `POST …/v1/audio/transcriptions` with the scoped key +
  `model=stt (whisper-turbo)` returns `200` + `{"text": …}` for a spoken WAV.

## Stage 3 — wake word (server side shipped; `hey_binks` model is the follow-up)

### Why a wake word needs *something* holding the mic open

A wake word needs (a) a mic held open 24/7 and (b) a program that continuously *listens*.
A wake engine (openWakeWord / microWakeWord) **detects** the word in an audio stream; it
doesn't capture audio. A store-bought "satellite" is just a mic + a listener in a box.

> **Correction (read this):** an earlier version of this doc claimed the **bare tablet
> browser** could stream to this server-side detector for a custom word. That's **wrong** —
> **HA's frontend has no browser wake-word feature**; the dashboard mic is push-to-talk
> only. The way to get a *custom* "hey binks" on the tablet with **no extra hardware** is a
> **custom Lovelace card that runs the wake engine in-browser** — that's **Stage 4** below,
> and it makes this server-side `wyoming-openwakeword` **unused by the tablet**.

Options, by what you care about:
- **Stage 4 — vendored `voice_satellite` card (recommended, no hardware, custom word).**
  In-browser microWakeWord on the wall tablet → the Binks pipeline. See Stage 4.
- **HA Companion app (Android).** Rock-solid on-device wake word, but only **3 fixed
  words** (Okay Nabu / Hey Jarvis / Hey Mycroft) — *cannot* do a custom "hey binks".
- **Dedicated satellite** (M5 Atom Echo ~$13 / Pi) streaming to *this* server engine —
  the robust hardware option for a room with **no** always-on computer.

The server-side engine below is what a **hardware** satellite (or the card's optional
"Home Assistant" detection mode) streams to. The Stage-4 card does detection in-browser and
does **not** use it.

### What shipped

- New Flux app **`apps/openwakeword/`** — `rhasspy/wyoming-openwakeword:2.1.0` (amd64),
  Wyoming protocol on TCP `10400`, in the `home-assistant` namespace. `args:` only
  (`--custom-model-dir /custom …`); built-in words are baked in, offline.
- `seed-wyoming-openwakeword` init container — seeds the **Wyoming** config entry
  `{host: wyoming-openwakeword.home-assistant.svc.cluster.local, port: 10400}`. Boot-safe
  (`setup_retry` if the service isn't up yet). Creates entity **`wake_word.openwakeword`**.
- `seed-binks-pipeline` now sets `wake_word_entity: wake_word.openwakeword`,
  `wake_word_id: okay_nabu` on the Binks pipeline — the path validated with the built-in
  **"okay nabu"** word.

### Finishing "hey binks"

1. **Train `hey_binks.tflite`** (openWakeWord notebook, Piper samples, ~1 hr, English;
   `.tflite` only). Name it exactly `hey_binks.tflite` → id `hey_binks`.
2. **Ship it into `/custom`** in `apps/openwakeword/deployment.yaml` (ConfigMap
   `binaryData` if < 1 MiB, else a download initContainer into the `custom-models` volume).
3. **Flip `wake_word_id`** from `okay_nabu` to `hey_binks` in `seed-binks-pipeline`.
4. **Point a mic at it:** enable Assist wake word in the tablet browser
   (Settings → Voice assistants → the tablet's Assist), or connect a satellite. Say the
   word → HA logs a wake event → Binks listens, transcribes, acts, and replies.

Tuning: raise `--threshold` (fewer false triggers) / `--trigger-level` if it fires too
eagerly or misses.

---

## Stage 4 — hands-free "hey binks" on the wall tablet (no companion app, no hardware)

The Lenovo Tab M11 wall panel becomes an always-on voice satellite via a **self-hosted
custom Lovelace card**: it holds the tablet mic open, runs **in-browser microWakeWord**,
and on wake drives the existing **Binks** pipeline (litellm whisper → Binks/gemini → Google
TTS). Vendored FOSS (`jxlarrea/voice-satellite-card-integration`, MIT) — our own software
in our own dashboard, not a companion app. Works because the tablet loads
`https://hass.kblab.me` on a trusted Let's Encrypt cert (a secure context → `getUserMedia`).

### What shipped (server side, code-first)

- **`install-voice-satellite`** init container — unzips the `2026.6.3` release asset FLAT
  into `/config/custom_components/voice_satellite/` (Python + `frontend/*.js` + `models/` +
  `sounds/`). The card JS **auto-registers** via `add_extra_js_url` (browser_mod pattern) —
  **no `lovelace: resources:` entry**, works in YAML mode. One pip dep (`mutagen`)
  auto-installs at boot.
- **`seed-voice-satellite`** init container — seeds the config entry `{name: "Wall
  Kitchen"}` → the satellite **device** + `assist_satellite.wall_kitchen` + 19 entities
  (Pipeline/wake-word **selects**, gating switches, media_player, …).
- **`config/packages/voice_satellite_binks.yaml`** — a startup automation that pins the
  per-device selects on every HA start (reproducible): Pipeline 1 → **Binks**, detection →
  **On Device (microWakeWord)**, wake word → `ok_nabu` (validation; → `hey_binks` later),
  noise-gate + stop-word-interruption **on**, finished speaking detection → **aggressive**, finish delay → **15 s** (the reply bubble stays on screen; 15 is the entity max).

Detection is 100% in-browser → the Stage-3 `wyoming-openwakeword` server is **not** used by
this path (it stays for future hardware satellites; retire later if unwanted).

### One-time tablet steps (not git-seedable — inherent per-browser identity)

- Kiosk app: the MIT **FreeKiosk** (`com.freekiosk`, RushB-fr), **v1.2.20-beta.4+ REQUIRED for
  voice** (earlier claim of "v1.2.17+ fixed WebRTC mic" was WRONG — 1.2.17-1.2.19 declare only
  `RECORD_AUDIO`, missing `MODIFY_AUDIO_SETTINGS` that Chromium's WebView also needs to select
  the mic → `NotReadableError`. 1.2.20-beta.4 adds it + intercom 2-way audio. Verified
  2026-07-17). Grant `RECORD_AUDIO` via Device-Owner/ADB (`MODIFY_AUDIO_SETTINGS` auto-grants
  at install ONLY on >= 1.2.20). Enable **Keep-Screen-On** + a black screensaver (avoid true
  screen-off); **disable URL Rotation / Dashboard auto-return** (they tear down the page + mic).
- In the dashboard, open the **Voice Satellite sidebar panel** and assign **this browser →
  the "Wall Kitchen" satellite** (like browser_mod's browser id).

**Validate 4A:** say **"ok nabu"** at the tablet → wake chime → speak a command → Binks
acts → spoken reply. Confirms mic-liveness + pipeline + TTS before training a custom word.

### Finishing "hey binks" (Stage 4B — microWakeWord)

1. Train **`hey_binks.tflite`** with the microWakeWord Colab (`kahrendt/microWakeWord`,
   synthetic Piper-TTS samples, ~1–2 h) → also produces a small `hey_binks.json` (mirror
   `ok_nabu.json`). **microWakeWord `.tflite` ≠ the openWakeWord `.tflite` used server-side**
   (different architecture) — train specifically for microWakeWord.
2. Ship it code-first into the **persistent** `/config/voice_satellite/models/` (commit +
   ConfigMap `binaryData` copy — a MWW tflite is ~tens of KB), **restart HA** (so
   `_load_user_custom_models` picks it up).
3. Flip `select.wall_kitchen_wake_word_1` → `hey_binks` (in the startup package or UI).

**Watch items:** `mutagen` pip at boot (needs pod egress — it has it); FreeKiosk mic
survival across true screen-off is undocumented (rely on keep-screen-on + black
screensaver; verify on the M11).

---

## Stage 5 - HA Voice PE hardware satellites (adopting)

Two Home Assistant Voice Preview Edition pucks - the first *hardware* satellites on this setup (until now every mic was a wall-tablet browser, Stage 4, or hypothetical, the appendix). The Voice PE runs the stock "Home Assistant Voice" ESPHome firmware: it holds its own mic open, runs microWakeWord on-device, and streams post-wake audio to whatever Assist pipeline it is assigned - here, Binks. It brings its own mic + wake engine, so it does NOT use the Stage-3 `wyoming-openwakeword` server or the Stage-4 card.

### Why this works on container-HA (no HA OS, no add-on)

Adopting an already-flashed ESPHome device needs only HA's built-in **ESPHome integration** - NOT the ESPHome *builder* add-on (that only compiles firmware, and this setup has no add-ons anyway). So the puck drops into the k3s-container HA with **zero new Flux deployments**.

### Onboarding (one-time per puck - inherent per-device identity, NOT git-seedable)

The ONLY step needing physical presence is the first Wi-Fi handshake — a fresh puck has no IP, so it must be reached over BLE or USB. Everything after that is headless (verified 2026-07-27: both pucks adopted + configured entirely via the HA API, **no companion app and no encryption key** — stock firmware exposed the native API *unencrypted*, so the config flow created the entry immediately).

1. Power the puck - USB-C, any 5V source (retail box ships bare in some regions; a phone charger is fine).
2. **Get it on Wi-Fi** (the one hands-on step): the HA Companion app or `improv-wifi.com` in Chrome, next to the puck, over BLE. Or flash your own ESPHome firmware with Wi-Fi baked in for a fully code-first path (see the appendix). It then appears on the LAN as `home-assistant-voice-<macsuffix>.local:6053`.
3. **Adopt + configure headlessly** (`$BASE` = https://hass.kblab.me, `$TOKEN` = the HA token):

```bash
# adopt: start the esphome config flow, submit host:port -> create_entry (no key)
FLOW=$(curl -sX POST "$BASE/api/config/config_entries/flow" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"handler":"esphome","show_advanced_options":true}' | jq -r .flow_id)
curl -sX POST "$BASE/api/config/config_entries/flow/$FLOW" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"host":"<puck-ip>","port":6053}'
# then: select.<dev>_assistant -> "Binks", select.<dev>_wake_word -> "Okay Nabu"
# (select/select_option service), and the WS API config/device_registry/update ->
# area_id + name_by_user "Voice <Room>".
```

Entities derive from the device name (`home_assistant_voice_<macsuffix>_*`): the `assist_satellite`, `select.*_assistant` (pipeline), `select.*_wake_word`, `number.*_volume`, `switch.*_mute`, `light.*_led_ring`, plus sensors. The two pucks are **Kitchen (0a9a90)** and **Bedroom (0aa6d9)**. Area assignment is tied to device identity, so you can adopt from anywhere and physically move the puck later — it keeps its Area, pipeline, and name.

### Wire it to Binks (per-puck; pinnable once entity IDs are known)

- **Pipeline -> Binks:** set the satellite's preferred pipeline (Settings -> the `assist_satellite` device -> Preferred pipeline, or the Voice assistants page). Reuses the entire existing stack: litellm whisper STT -> Binks/`assist (gemini-flash)` -> Google TTS. No new services.
- **Wake word -> Okay Nabu (for now):** the puck's on-device microWakeWord select. Stock firmware ships **Okay Nabu / Hey Jarvis / Hey Mycroft** only. Validate end-to-end on Okay Nabu before touching a custom word.
- **Reproducibility (follow-up):** the config entry + select states are runtime state (on the PVC), not git. Entity IDs are now known (`home_assistant_voice_0a9a90_*`, `home_assistant_voice_0aa6d9_*`), so the pin is a concrete TODO: a startup automation package (sibling of `config/packages/voice_satellite_binks.yaml`) that re-selects `select.*_assistant` -> Binks + `select.*_wake_word` -> Okay Nabu on every HA start, so a PVC rebuild doesn't strand the pucks. Note the ESPHome pipeline knob is `select.<dev>_assistant`, NOT the card's `_pipeline_1`.

### Validate

Say **"Okay Nabu"** at the puck -> wake chime + LED ring -> speak a room command ("turn off the lights") -> Binks acts on that room's exposed entities -> spoken reply via Google TTS. Confirms mic + wake + pipeline + area routing before touching a custom word.

### Custom "hey binks" on the Voice PE = firmware rebuild (reuses the Stage-4B model)

The wall tablets already run a trained **`binks` microWakeWord** model (Stage 4B: `binks.tflite`, ~0.97 cutoff, ~0.3 false-accepts/hr, ~4% miss). The Voice PE also uses microWakeWord, but its models are **baked into the ESPHome firmware**, not loaded at runtime - so unlike the tablet you cannot just drop the file in. To get "hey binks" on the puck:

1. Take the existing `binks` microWakeWord model (`.tflite` + `.json`) from Stage 4B.
2. Add it to a Voice PE ESPHome firmware config (`micro_wake_word:` model list) and **compile + flash** the puck (USB or OTA).
3. The `hey binks` option then appears in the puck's wake-word select; pin it like the tablets.

Until then a mixed fleet is fine - pucks on **Okay Nabu**, tablets on **binks** - since wake word is per-device.

### Placement (two pucks)

One puck covers one room; mic pickup is roughly 3-5 m in a quiet room and drops with noise or a closed door. Put them where you *talk to* HA, not the biggest rooms - big rooms are harder for far-field mics. The tablets already cover Kitchen / Office / Living Room / Bedroom as browser satellites, so the pucks are best where you want a dedicated always-listening mic with a real speaker (3.5mm jack for a bigger speaker) rather than relying on a tablet page staying loaded.

---

## Appendix — Voice input: options & decision record

The one idea that unlocks it: a wake word needs **a mic held open** + **a program that
listens**. Neither needs special hardware — the engines are pure software. A store-bought
satellite is just "a mic + a listener in a box" for a room with **no** always-on computer.

| Path | Custom "hey binks"? | Extra hardware? | Our software? | Verdict |
|---|---|---|---|---|
| **`voice_satellite` card, on-device microWakeWord** | ✅ (train `.tflite`) | ❌ | ✅ self-hosted FOSS, in-repo | **CHOSEN** (Stage 4) |
| Same card, on-device **openWakeWord** (`.onnx`) | ✅ (convert oww→onnx) | ❌ | ✅ | Alt; needs WebGPU + conversion |
| Same card, **vsWakeWord** (WebGPU) | ❌ (no public trainer) | ❌ | ✅ | Best accuracy, only shipped words |
| Same card, detection = **"Home Assistant"** → server owww | ✅ (server `.tflite`) | ❌ | ✅ | Reuses Stage 3; streams audio continuously → higher latency/less reliable on tablets |
| **Build the card from scratch** (~600–1200 lines JS) | ✅ | ❌ | ✅✅ bespoke | Rejected — reinvents fragile mic-liveness/audio-framing |
| **Wyoming satellite on the gungan Linux box** (`cachyos-x8664-main`) | ✅ | ❌ (its mic) | ✅ | Great for the **desk**; mic hears that room only |
| **HA Companion app** on-device wake | ❌ (3 fixed words) | ❌ | ⚠️ 3rd-party app | Zero-effort "Hey Jarvis" fallback |
| **ViewAssist Companion App** (Android) | ✅ (its engine) | ❌ | ⚠️ companion app | Works, but a companion app |
| **StreamAssist + RTSP audio app** → server owww | ✅ | ❌ | ⚠️ | Fiddly, experimental |
| **M5 Atom Echo (~$13)** → server owww | ✅ | ✅ ($13) | ✅ | Cheapest *hardware* mic for a computer-less room |
| **Raspberry Pi + mic** | ✅ | ✅ | ✅ | More capable, more setup |
| **HA Voice PE** | ✅ (firmware build, reuses Stage-4B model) | ✅ ($$) | ✅ | **ADOPTED (Stage 5)** — 2 pucks; nicest hardware; custom word = firmware rebuild |

**Why the "obvious" tablet paths fail for a custom word:** a bare Fully/FreeKiosk page can't
do wake word — **HA's frontend has no browser wake-word feature** (push-to-talk only); the
Stage-4 *card* adds the listener. The Companion app listens on-device but only for its **3
fixed words**.

**Sub-decisions:** wake engine = **FOSS microWakeWord** (offline, no keys) over **Porcupine**
(proprietary AccessKey + periodic phone-home — wrong for a self-hosted LAN); STT = **litellm
`stt (whisper-turbo)`** (no new container) over a Wyoming faster-whisper deployment; kiosk =
MIT **`com.freekiosk`** v1.2.20-beta.4+ (needs `MODIFY_AUDIO_SETTINGS`, added there) over
proprietary Fully Kiosk; and the tablet must load a **trusted-HTTPS** URL
(`https://hass.kblab.me`) or `getUserMedia` is silently blocked.

## Turn latency (2026-10-15)

Measured with the pipeline debug view (`assist_pipeline/pipeline_debug/*` over the HA websocket):

- **Brain was the big one.** `gemini-2.5-flash` on the free-tier family key allows 20 requests/day, and the 10-minute brain probe alone spent that, so every turn silently fell back to Lazer `gpt-oss-120b` at ~5.5 s. The route now runs `gemini-3.5-flash-lite` (0.7-0.8 s direct, no thinking tokens, correct tool calls) with an 8 s per-route timeout so a stalled upstream falls back fast instead of waiting the 900 s router default.
- **End of speech.** Satellites (tablets and pucks) are pinned to `aggressive` finished speaking detection. If it clips people mid-sentence, set `finished_speaking` back to `default` in `voice_satellite_binks.yaml`.
- **STT is batch, not streaming.** Whisper gets the clip after end-of-speech; that costs ~0.6 s, so it is not where the wait was.
- LiteLLM config lives in `apps/litellm/config.yaml` and is generated into a hash-named ConfigMap, so a route change restarts the gateway. Before, edits only took effect on an unrelated pod restart.

## Rich answers on the wall tablets (2026-10-15)

Binks's pipeline runs on **home-llm** (`llama_conversation`, Generic OpenAI backend -> LiteLLM `assist (gemini-flash)`), not extended_openai_conversation, because only home-llm can attach HA LLM APIs. Attached: Assist (device control) plus `voice_satellite_llm_tools` Web Search, Image Search, Wikipedia and Weather. The tablet card draws those tool results: an image grid for "show me recipes for chicken", a summary card with a featured image for Wikipedia and web answers, and a forecast card for weather.

- Config is code: `apps/home-assistant/config/binks/agent.yaml` (agent options, tool entries, which agent the pipeline uses) and `prompt.jinja` (persona + device list), applied every boot by the `seed-binks-agent` init container. Edit there, not in the UI.
- Search is the in-cluster `apps/searxng` (no ingress, no API keys). Image quality is tuned in `apps/searxng/settings.yml` by removing engines; the tool's own engine list does not filter because it also sends `categories=images`.
- `tool_response_as_string` must stay `false`, or results are spoken but never drawn.
- Rollback: set `pipeline_agent: extended_openai_conversation` in `agent.yaml`. EOC stays installed and seeded.
- Cost of the switch: plain chit-chat is ~1.5 s slower than EOC (bigger prompt with the Assist tool definitions); a search answer is two model rounds plus SearXNG, ~3-4 s.
