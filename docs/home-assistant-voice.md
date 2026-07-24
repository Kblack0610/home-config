# Home Assistant voice assistant — "Binks"

A staged, code-first conversational assistant for the home. Backed by the cluster
**LiteLLM** gateway (model `vlm (gemini-3-flash)`), able to control exposed devices.
Built and shipped in stages so each layer is tested in isolation.

| Stage | What | Status |
|-------|------|--------|
| 0 | Fix broken cast TTS announcements (`tts.google_say`) | ✅ shipped (PR #77) |
| 1 | **Text chat** — Binks conversation agent + home control | ✅ shipped |
| 2 | **Press-to-talk voice** — STT via LiteLLM whisper + Google TTS | ✅ shipped |
| 3 | **Wake word (server)** — `wyoming-openwakeword` for *hardware* satellites; unused by the tablet card | ◑ server side shipped |
| 4 | **Wall-tablet wake word** — in-browser `voice_satellite` card → Binks; custom `binks` microWakeWord live on all 4 tablets | ✅ shipped (`binks` live) |
| 5 | **HA Voice PE hardware satellites** — 2 pucks, ESPHome, on-device wake → Binks | ◑ adopting (onboard + wire to Binks) |

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
key `api-key`). Allowed models:

```
vlm (gemini-3-flash)   # Binks chat + tool calling
fast (gpt-oss-120b)    # documented fast fallback
stt (whisper-turbo)    # Stage 2 speech-to-text
```

Re-mint / rotate:

```bash
MASTER_KEY=$(kubectl -n ai-gateway get secret litellm-secrets -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
curl -sX POST https://llm.kblab.me/key/generate -H "Authorization: Bearer $MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"key_alias":"home-assistant-assist","models":["vlm (gemini-3-flash)","fast (gpt-oss-120b)","stt (whisper-turbo)"],"tpm_limit":100000}'
# put .key into apps/home-assistant/litellm-assist-secret.yaml stringData.api-key, then: sops -e -i <file>
```

---

## Stage 1 — text chat + control (this ship)

Three init containers do the work (all in `deployment.yaml`):

1. `install-extended-openai-conversation` — vendors EOC `1.1.0` into `/config/custom_components`.
2. `seed-binks-conversation` — writes the EOC config entry: `base_url` = the in-cluster
   LiteLLM `/v1`, `chat_model` = `vlm (gemini-3-flash)`, `use_tools: true`,
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

- If Gemini tool-calling ever misbehaves through LiteLLM, switch `chat_model` in the
  `seed-binks-conversation` options to `fast (gpt-oss-120b)` and roll the deployment.
  (Verified 2026-07: gemini-3-flash returns correct `execute_services` tool calls.)
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

- Kiosk app: the MIT **FreeKiosk** (`com.freekiosk`, RushB-fr), **v1.2.17+** (that release
  fixed WebRTC mic). Grant the OS `RECORD_AUDIO` prompt (or pre-grant via Device-Owner/ADB
  — see the `adb-ops` skill). Enable **Keep-Screen-On** + a black screensaver (avoid true
  screen-off); **disable URL Rotation / Dashboard auto-return** (they tear down the page +
  mic). Confirm the installed APK — `docs/wall-panels.md` says `uk.freekiosk`; verify.
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

Like the Stage-4 tablet's browser->satellite binding, the ESPHome config entry carries a per-device API encryption key generated on the puck, so it can't be seeded from git.

1. Power the puck - USB-C, any 5V source (retail box ships bare in some regions; a phone charger is fine).
2. Onboard Wi-Fi + HA via the **HA Companion app** (Bluetooth/Improv): open it next to the puck, it is discovered over BLE, pick Wi-Fi, it hands off to HA. Desktop also shows a "New device discovered" (ESPHome) prompt; the Improv flow passes the encryption key automatically.
3. **Name it per room** at adoption (e.g. "Voice Kitchen"). Entities derive from the name: `assist_satellite.voice_kitchen`, `select.voice_kitchen_wake_word`, `number.voice_kitchen_volume`, `switch.voice_kitchen_mute`, `light.voice_kitchen` (LED ring), plus sensors. Verify the exact IDs after adoption.
4. **Assign the Area** (Settings -> Devices -> the puck -> Area). This is what makes "turn off the lights" resolve to that room without naming entities.

### Wire it to Binks (per-puck; pinnable once entity IDs are known)

- **Pipeline -> Binks:** set the satellite's preferred pipeline (Settings -> the `assist_satellite` device -> Preferred pipeline, or the Voice assistants page). Reuses the entire existing stack: litellm whisper STT -> Binks/gemini-3-flash -> Google TTS. No new services.
- **Wake word -> Okay Nabu (for now):** the puck's on-device microWakeWord select. Stock firmware ships **Okay Nabu / Hey Jarvis / Hey Mycroft** only. Validate end-to-end on Okay Nabu before touching a custom word.
- **Reproducibility:** once a puck is adopted and its exact entity IDs are known, mirror the Stage-4 pattern with a startup automation package (sibling of `config/packages/voice_satellite_binks.yaml`) that pins pipeline + wake word on every HA start. Deferred until the IDs exist - the ESPHome pipeline knob differs from the card's `_pipeline_1` select, so verify on adoption before writing the package.

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
MIT **`com.freekiosk`** v1.2.17+ over proprietary Fully Kiosk; and the tablet must load a
**trusted-HTTPS** URL (`https://hass.kblab.me`) or `getUserMedia` is silently blocked.
