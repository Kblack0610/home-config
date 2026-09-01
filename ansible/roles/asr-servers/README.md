# asr-servers

Local speech-to-text daemons for the [gungan](https://github.com/Kblack0610/gungan-speak)
push-to-talk dictation client. Solves Whisper's silence-hallucination problem
(it invents "Thank you." / "you" when you say nothing) by making the default
engine **Parakeet**, a transducer trained on silence→empty that *structurally*
cannot hallucinate, and hardening the Whisper fallback with VAD.

## What it provisions (systemd `--user` services)

| Service | Port | Engine | Role |
|---|---|---|---|
| `parakeet-asr` | 8771 | Parakeet TDT 0.6B ONNX (sherpa-onnx, CPU) | **default** — never hallucinates on silence |
| `whisper-asr`  | 8772 | whisper.cpp-server + Silero VAD | fallback / A-B |

Both expose an HTTP transcription endpoint that returns `{"text": …}`; the
gungan client picks one via `GUNGAN_BACKEND` and falls back automatically.

- Parakeet: a `uv` venv (Python {{ pinned 3.12 }}) running a ~50-line FastAPI shim
  (`parakeet_server.py`) over sherpa-onnx. CPU-only by default (sub-second for
  short clips on a Ryzen 9 7900X); GPU is unnecessary for push-to-talk.
- Whisper: builds `whisper-server` from source only if the binary is absent,
  then runs it with `--vad` + `--suppress-nst` + confidence thresholds tuned so
  short words ("ok") survive while silence is trimmed before the decoder.

## Accuracy: hotword biasing

Parakeet's transducer has almost no internal language model. That is what makes
it fast and silence-safe, but it also means rare proper nouns decode
phonetically, with nothing to fall back on. Whisper's seq2seq decoder was
silently repairing those; Parakeet cannot.

The counter is contextual biasing: a hotword list, applied under
`modified_beam_search` (greedy decoding ignores hotwords entirely). Edit
`parakeet_hotwords` in `defaults/main.yml` and re-run the role.

Measured on `cachyos-x8664-main`, 2026-08-25, same synthesized audio through
each config:

| | config | result |
|---|---|---|
| A | int8, greedy (old default) | `the Fargo runner logs`, `Run cubect to get pods`, `The Viconya Task Tracker`, `SAPS encrypted` |
| B | fp16, greedy | `the 4GO runner logs`, `Run cubect to get pods`, `The Vikonya task tracker`, `SARPS encrypted` |
| C | int8, beam + hotwords | `the Forgejo runner logs`, `Run kubectl get pods`, `The Vikunja Task Tracker`, `sops encrypted` |

Two things this settles:

- **Precision is not the lever.** fp16 changed nothing measurable while costing
  ~2x disk and ~30% latency, so `parakeet_precision` defaults to `int8`. The
  variable exists so you can re-test on your own voice by flipping one word.
- **Biasing is the lever**, and it costs ~30ms on a 7s clip.

Biasing sharpens a near-miss; it cannot recover a word the acoustics lost. In
the run above `Traefik` and `Immich` stayed wrong despite being listed, because
the synthesized audio did not carry them.

`parakeet_hotwords_score` is tuned, not arbitrary. Below 2.0 the weaker
matches (`Immich`, `sops`) fall back to phonetic; at 3.0 hotwords start bleeding
into unrelated audio (`Grafana` surfaced as `Graf`, `Forgejo` as `Forge`).

If the hotwords or derived `bpe.vocab` file is missing, the shim logs a warning
and falls back to greedy rather than failing: sherpa-onnx aborts the process on a
bad biasing path, and losing dictation entirely is worse than losing the boost.

### Biasing re-introduces silence hallucination

Enabling hotwords costs the property Parakeet was picked for. Measured on
2s of `anullsrc` digital silence:

| decoding | silence decodes as |
|---|---|
| greedy | `''` |
| beam, no hotwords | `''` |
| beam + hotwords, score 1.0 | `"I'm sorry."` |
| beam + hotwords, score 2.0 | `"I"` |

Beam search alone is safe; biasing is what breaks it, and non-monotonically in
score, so no score is trustworthy on its own. The shim therefore enforces the
guarantee explicitly with a `SILENCE_PEAK` gate rather than relying on an
emergent model property that we just proved is fragile. The threshold is one
16-bit quantization step, derived from `pw-record`'s s16 capture format: a buffer
peaking at or below it carries no signal. Verified not to swallow quiet
dictation, speech attenuated 40dB still peaks 244x above the gate and
transcribes.

## Run

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit cachyos-x8664-main --tags asr --check --diff
ansible-playbook playbooks/site.yml --limit cachyos-x8664-main --tags asr
```

## Verify

```bash
systemctl --user status parakeet-asr whisper-asr
# hotwords:true confirms biasing is actually armed (false = silently greedy):
curl -s 127.0.0.1:8771/health
# silence -> empty (no hallucination):
ffmpeg -f lavfi -i anullsrc=r=16000:cl=mono -t 2 /tmp/s.wav -y
curl -s -F file=@/tmp/s.wav 127.0.0.1:8771/v1/audio/transcriptions   # {"text":""}
curl -s -F file=@/tmp/s.wav 127.0.0.1:8772/inference                 # {"text":""}
```

## Troubleshooting: "dictation is broken" is usually the mic, not the daemons

Both daemons return an empty string on silence **by design** (that is the whole
point of defaulting to Parakeet). The gungan client then reports "No speech
detected". So a dead microphone and a working one produce the *same* end-user
symptom, and every arrow points at the ASR stack while the real cause is input.

Check the input **before** touching the daemons:

```bash
gungan health          # reports the live RMS of the default source; fails on dead input
wpctl status           # which source is actually default (* marks it)
```

Measure directly if `gungan` is not available:

```bash
pw-record --format=s16 --rate=16000 --channels=1 /tmp/probe.wav   # ^C after ~5s
sox /tmp/probe.wav -n stats | grep 'RMS lev'
```

Reference points: live mic in a quiet room reads about **-60 dB**; a disconnected
input reads **-90 dB or below** (pure dither, `Bit-depth 2/16`).

Known trap on `cachyos-x8664-main`: the Focusrite Scarlett Solo presents as a
healthy, selectable capture device **even with nothing plugged into its XLR
jack** - it just streams silence. WirePlumber will also fall back to it when the
previously configured default source (e.g. a USB webcam) is unplugged, silently
redirecting capture to a dead jack. Repin the real device with:

```bash
wpctl set-default <id>   # id from `wpctl status` Sources
```

This persists to `~/.local/state/wireplumber/default-nodes` as
`default.configured.audio.source`.

## Notes

- Requires `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`) and the
  whisper transcription model at `~/.local/share/whisper-models/ggml-large-v3-turbo.bin`.
- `loginctl enable-linger` is set so the daemons run before/without an interactive login.
- The gungan **client** config lives separately at `~/.config/gungan/config`
  (shipped in the gungan-speak repo as `config.example`); this role provisions
  only the servers.
- GPU upgrade path: install `cuda`/`cudnn` and point sherpa-onnx at the CUDA
  execution provider — not needed for this workload.
