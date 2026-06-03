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

## Run

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit cachyos-x8664-main --tags asr --check --diff
ansible-playbook playbooks/site.yml --limit cachyos-x8664-main --tags asr
```

## Verify

```bash
systemctl --user status parakeet-asr whisper-asr
curl -s 127.0.0.1:8771/health
# silence -> empty (no hallucination):
ffmpeg -f lavfi -i anullsrc=r=16000:cl=mono -t 2 /tmp/s.wav -y
curl -s -F file=@/tmp/s.wav 127.0.0.1:8771/v1/audio/transcriptions   # {"text":""}
curl -s -F file=@/tmp/s.wav 127.0.0.1:8772/inference                 # {"text":""}
```

## Notes

- Requires `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`) and the
  whisper transcription model at `~/.local/share/whisper-models/ggml-large-v3-turbo.bin`.
- `loginctl enable-linger` is set so the daemons run before/without an interactive login.
- The gungan **client** config lives separately at `~/.config/gungan/config`
  (shipped in the gungan-speak repo as `config.example`); this role provisions
  only the servers.
- GPU upgrade path: install `cuda`/`cudnn` and point sherpa-onnx at the CUDA
  execution provider — not needed for this workload.
