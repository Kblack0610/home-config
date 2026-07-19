# launchd-mlx-server

Manages a single [`mlx-openai-server` (cubist38)](https://github.com/cubist38/mlx-openai-server) launchd service on `mac-studio`. One process serves chat, vision, and embedding models from one OpenAI-compatible endpoint at `:8090`.

Replaces what was previously two roles + an Ollama LaunchAgent: `launchd-mlx-services` (three `mlx_lm.server` processes for chat) and `launchd-ollama-embeddings` (Ollama for embeddings on `:11434`). Both are retired.

> **About the upstream package name (`mlx-openai-server`):** the "openai" refers to API SHAPE compatibility (`/v1/chat/completions`, `/v1/embeddings`) — not the OpenAI vendor. Everything we own (this role, the launchd label, config dir, variable prefix) drops that prefix to avoid muscle-memory confusion.

## What this role does

1. Asserts the `~/mlx-env` venv exists and `mlx-openai-server` is installed inside it.
2. Renders the server's YAML config (model list, port, log level) to `~/.config/mlx-server/config.yaml`.
3. Renders a single per-user LaunchAgent plist to `~/Library/LaunchAgents/com.mlx-server.plist`.
4. `launchctl bootout`+`bootstrap` if either file changed.

## What this role does NOT do

- **Create the venv or pip-install `mlx-openai-server`.** Same precedent as the legacy `launchd-mlx-services` — the venv is a one-time manual setup.
- **Download model weights.** `mlx-openai-server` pulls from HuggingFace on first use; pre-warm to avoid stalling the first request.
- **Touch services whose plist + config did not change.** Re-running the role with no diff leaves an in-flight server alone.

## One-time mac-studio setup

The venv is `~/mlx-env` (Python 3.12 — newer `mlx-openai-server` caps at `<3.13`). Once the legacy `~/mlx-env` is retired, the path can be renamed `~/mlx-env` → `~/mlx-env` as a follow-up; this role's `mlx_venv_path` default just needs the matching update.

```bash
# 1. Create the venv (skip if it already exists)
ssh mac-studio '/opt/homebrew/bin/python3.12 -m venv ~/mlx-env'

# 2. Install the server (transitively brings mlx-lm + mlx-vlm + mlx-embeddings)
ssh mac-studio '~/mlx-env/bin/pip install -U mlx-openai-server'

# 3. Pre-warm model weights so the first request doesn't stall on a
#    multi-GB download. Pinned fleet:
ssh mac-studio bash -lc "
  ~/mlx-env/bin/hf download mlx-community/Qwen3-Coder-Next-4bit
  ~/mlx-env/bin/hf download mlx-community/Qwen3.6-35B-A3B-4bit
  ~/mlx-env/bin/hf download mlx-community/Qwen3-4B-Instruct-2507-4bit
  ~/mlx-env/bin/hf download mlx-community/Qwen3-4B-Instruct-2507-8bit
  ~/mlx-env/bin/hf download mlx-community/nomicai-modernbert-embed-base-4bit
"

# 3b. on_demand expansion candidates (big — pull before A/B testing them):
ssh mac-studio bash -lc "
  ~/mlx-env/bin/hf download lmstudio-community/Qwen3-Coder-Next-MLX-6bit
  ~/mlx-env/bin/hf download mlx-community/Qwen3-Coder-480B-A35B-Instruct-4bit   # ~250 GB
  ~/mlx-env/bin/hf download mlx-community/Qwen3.5-397B-A17B-4bit                # ~214 GB
  ~/mlx-env/bin/hf download mlx-community/Qwen3-VL-30B-A3B-Instruct-8bit
  ~/mlx-env/bin/hf download mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ
"
```

The `huggingface-cli` command is deprecated as of 2026-Q1; `hf download` is the replacement.

### Local vision (VLM) prerequisite

The `vlm (Qwen3-VL-30B-A3B-8bit)` entry is `model_type: multimodal`. Older `mlx-vlm` hits `RuntimeError: There is no Stream(gpu, N) in current thread` on the worker-thread generation path (mlx-lm #1179/#1256). It is `on_demand` so it can't crash the pinned fleet at boot, but to actually serve it you must bump the venv's `mlx-vlm` to the fixed line:

```bash
ssh mac-studio '~/mlx-env/bin/pip install -U "mlx-vlm>=0.6.5"'
# fallback if a request still crashes: launch the server with --disable-batching
```

Verify a real caption (not a silently-dropped image) before flipping the `vlm` alias in `apps/litellm/configmap.yaml` off `gemini-3-flash`.

### Flagship mode (Kimi K2, ~468 GB — NOT a live entry)

A 1T flagship cannot co-reside with the pinned fleet (~468 GB weights vs ~407 GB usable headroom). Run it as a deliberate mode, not an `on_demand` slot: evict the fleet first (bootout `com.mlx-server`), then serve `mlx-community/Kimi-K2-Instruct-0905-mlx-DQ3_K_M` sole-resident. The `iogpu.wired_limit_mb=491520` (480 GB) tuning it needs is already provisioned by the `macos-llm-node` role (`com.kblab.llm-sysctl` LaunchDaemon). Restore the fleet by re-bootstrapping `com.mlx-server`.

## Apply

```bash
# Dry-run first
ansible-playbook ansible/playbooks/site.yml --limit mac-studio --tags mlx --check --diff

# Real apply
ansible-playbook ansible/playbooks/site.yml --limit mac-studio --tags mlx
```

## Verify

```bash
# launchd state
ssh mac-studio 'launchctl list | grep mlx-server'

# OpenAI-compatible model list
curl -fsS http://192.168.1.4:8090/v1/models | jq '.data[].id'

# Chat smoke test
curl -fsS -X POST http://192.168.1.4:8090/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"mlx-community/Qwen3-Coder-Next-4bit","messages":[{"role":"user","content":"say PONG"}]}' \
  | jq -r '.choices[0].message.content'

# Embedding smoke test (768-dim, matches mem0's pgvector schema)
curl -fsS -X POST http://192.168.1.4:8090/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"mlx-community/nomicai-modernbert-embed-base-4bit","input":"hello"}' \
  | jq '.data[0].embedding | length'
```

## Adding a new model later

1. Add an entry to `mlx_models` in `defaults/main.yml`.
2. Pre-warm the weights on `mac-studio` via `hf download`.
3. Re-run the playbook. The role re-renders config + plist, `bootout`+`bootstrap` reloads the service.
4. Add the matching `model_name` entry to `apps/litellm/configmap.yaml` so LiteLLM routes external callers to it.

## Logs

Server stdout + stderr go to `/tmp/mlx-server.log` (per the rendered plist's `StandardOutPath` / `StandardErrorPath`). Survive-process-only — they rotate on reboot.
