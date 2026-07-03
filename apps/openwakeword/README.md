# wyoming-openwakeword

Server-side wake-word detection for the **Binks** voice assistant (Stage 3). Runs
`rhasspy/wyoming-openwakeword` and speaks the Wyoming protocol on TCP `10400`; Home
Assistant's built-in **Wyoming** integration connects to it and exposes a
`wake_word.openwakeword` entity that the Binks Assist pipeline uses.

See the full assistant runbook: [`docs/home-assistant-voice.md`](../../docs/home-assistant-voice.md).

## What it does (and doesn't)

- It **detects** a wake word in an audio stream. It does **not** capture audio — a
  *satellite* must stream mic audio to it. On this setup the first mic source is the
  **wall-tablet browser** (HA frontend Assist streams the tablet mic to this server, and
  since detection is server-side it supports a custom word). A dedicated satellite
  (M5 Atom Echo / Pi) is the more reliable fallback.
- Built-in words ship in the image (offline): **`okay_nabu`** (note: not `ok_nabu`),
  `hey_jarvis`, `alexa`, `hey_mycroft`, `hey_rhasspy`. The path is validated with
  `okay_nabu` first.

## Custom "hey binks" word

1. Train `hey_binks.tflite` with the openWakeWord notebook
   (<https://www.home-assistant.io/voice_control/create_wake_word/> — Piper-generated
   samples, ~1 hr, English). **`.tflite` only** (this image dropped `.onnx`). Name the
   file exactly `hey_binks.tflite` so the model id is `hey_binks`.
2. Ship it into `/custom` in `deployment.yaml` — a `ConfigMap` with `binaryData` if the
   file is < 1 MiB, otherwise a small download initContainer into the `custom-models`
   volume. Only the wake `.tflite` goes here; `melspectrogram.tflite` /
   `embedding_model.tflite` are already baked into the image.
3. Flip the pipeline `wake_word_id` from `okay_nabu` to `hey_binks` (the
   `seed-binks-pipeline` init container in `apps/home-assistant/deployment.yaml`).

## Notes

- `image: rhasspy/wyoming-openwakeword:2.1.0` (multi-arch; pinned to amd64 nodes).
- **No k8s `command:`** — the entrypoint already binds `tcp://0.0.0.0:10400`; we set
  `args:` only (`--custom-model-dir /custom --threshold 0.5 --trigger-level 1 --debug`).
- HA's Wyoming entry is seeded code-first (`seed-wyoming-openwakeword` init container).
  If this service is down when HA boots, the entry goes to `setup_retry` (boot-safe) and
  the `wake_word.openwakeword` entity appears once it connects.
- Tune `--threshold` (higher = fewer false triggers) and `--trigger-level` if the word
  fires too eagerly or misses.
