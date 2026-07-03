# Home Assistant voice assistant — "Binks"

A staged, code-first conversational assistant for the home. Backed by the cluster
**LiteLLM** gateway (model `vlm (gemini-3-flash)`), able to control exposed devices.
Built and shipped in stages so each layer is tested in isolation.

| Stage | What | Status |
|-------|------|--------|
| 0 | Fix broken cast TTS announcements (`tts.google_say`) | ✅ shipped (PR #77) |
| 1 | **Text chat** — Binks conversation agent + home control | ✅ shipped |
| 2 | **Press-to-talk voice** — STT via LiteLLM whisper + Google TTS | ✅ shipped |
| 3 | **Wake word** — server openWakeWord + Wyoming; `okay_nabu` validated, `hey_binks` = training follow-up | ◑ server side shipped |

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

openWakeWord **detects** a wake word inside an audio stream — it does not capture audio.
Something must keep a mic open 24/7 and stream it to the server. Three options here, two
of which use the **wall tablet's own mic, no extra hardware**:

- **Tablet browser (try first).** HA's frontend can stream the tablet mic to the
  server-side detector — and because detection is server-side, **it supports the custom
  "hey binks" word**. Caveat: a browser only listens while the tab is focused with mic
  permission; screen-sleep / kiosk / wallpanel screensaver can suspend the tab and drop
  the mic, so 24/7 reliability depends on the kiosk setup.
- **HA Companion app (Android).** Rock-solid on-device wake word, but only **3 fixed
  words** (Okay Nabu / Hey Jarvis / Hey Mycroft) — it *cannot* do a custom "hey binks".
- **Dedicated satellite** (M5 Atom Echo ~$13 / Pi). Most reliable; supports custom words.
  The *robust* option, not the only one.

The server-side detector below is needed for **all three** — the tablet browser streams
to it exactly like a satellite would.

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
