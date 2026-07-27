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

Everything is code-first: custom components are vendored by `install-*` init containers
and config entries are written by idempotent `seed-*` init containers (see
`apps/home-assistant/deployment.yaml`). No HACS, no UI clicking, reproducible from git.

---

## Why these choices

- **HA is the `stable` container in k3s (2025.11.x), not HA OS** → no add-ons; every
  helper service is a plain container wired by config.
- **Conversation agent = `jekalmin/extended_openai_conversation` pinned to `1.1.0`.**
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
assist (gemini-flash)  # PRIMARY — gemini-2.5-flash on GEMINI_API_KEY_FAMILY
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
fallback chain (`router_settings.fallbacks` in `apps/litellm/configmap.yaml`):

```
assist (gemini-flash)     gemini-2.5-flash   Google (GEMINI_API_KEY_FAMILY)   PRIMARY
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

Two monitoring layers exist, and as of 2026-07 **neither covers voice**:

- **gatus** (`apps/gatus/config.yaml`) probes LiteLLM `/health/readiness` and the
  `llm.kblab.me` ingress — these confirm the *gateway* is up, NOT that Binks's model
  route answers. A dead model route (the incident above) passes both. Fix: a
  **synthetic assist-route probe** — a gatus endpoint that POSTs a trivial prompt to
  `assist (gemini-flash)` with the assist key and asserts the body is not an error.
- **HA `fleet_alerts.yaml`** turns `device_class: connectivity` binary_sensors (from
  `site_status.yaml`) into ntfy alerts on the `homelab-alerts` topic. The voice
  **satellites are not in it** — if a Voice PE puck or a wall tablet drops off WiFi,
  nothing alerts. Fix: a connectivity `binary_sensor` per satellite (derived from the
  `assist_satellite.*` availability) so it flows into the existing pipeline.

Status: both are **known gaps / TODO**. The satellite entities *are* visible in HA
today (`assist_satellite.*` state = idle / unavailable, plus the Assist pipeline Debug
view), just not alerted.

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
  sensitivity → moderate, noise-gate + stop-word-interruption **on**.

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
