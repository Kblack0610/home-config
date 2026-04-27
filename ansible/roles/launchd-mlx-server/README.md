# launchd-mlx-server

Manages a single [`mlx-openai-server` (cubist38)](https://github.com/cubist38/mlx-openai-server) launchd service on `mac-studio`. One process serves chat, vision, and embedding models from one OpenAI-compatible endpoint at `:8090`.

Replaces what was previously two roles + an Ollama LaunchAgent: `launchd-mlx-services` (three `mlx_lm.server` processes for chat) and `launchd-ollama-embeddings` (Ollama for embeddings on `:11434`). Both are retired.

> **About the upstream package name (`mlx-openai-server`):** the "openai" refers to API SHAPE compatibility (`/v1/chat/completions`, `/v1/embeddings`) — not the OpenAI vendor. Everything we own (this role, the launchd label, config dir, variable prefix) drops that prefix to avoid muscle-memory confusion.

## What this role does

1. Asserts the `~/mlx-oai-env` venv exists and `mlx-openai-server` is installed inside it.
2. Renders the server's YAML config (model list, port, log level) to `~/.config/mlx-server/config.yaml`.
3. Renders a single per-user LaunchAgent plist to `~/Library/LaunchAgents/com.mlx-server.plist`.
4. `launchctl bootout`+`bootstrap` if either file changed.

## What this role does NOT do

- **Create the venv or pip-install `mlx-openai-server`.** Same precedent as the legacy `launchd-mlx-services` — the venv is a one-time manual setup.
- **Download model weights.** `mlx-openai-server` pulls from HuggingFace on first use; pre-warm to avoid stalling the first request.
- **Touch services whose plist + config did not change.** Re-running the role with no diff leaves an in-flight server alone.

## One-time mac-studio setup

The venv is `~/mlx-oai-env` (Python 3.12 — newer `mlx-openai-server` caps at `<3.13`). Once the legacy `~/mlx-env` is retired, the path can be renamed `~/mlx-oai-env` → `~/mlx-env` as a follow-up; this role's `mlx_venv_path` default just needs the matching update.

```bash
# 1. Create the venv (skip if it already exists)
ssh mac-studio '/opt/homebrew/bin/python3.12 -m venv ~/mlx-oai-env'

# 2. Install the server (transitively brings mlx-lm + mlx-vlm + mlx-embeddings)
ssh mac-studio '~/mlx-oai-env/bin/pip install -U mlx-openai-server'

# 3. Pre-warm model weights. The chat models are likely already cached
#    from the legacy setup; the embedding model is small (~250 MB).
ssh mac-studio bash -lc "
  ~/mlx-oai-env/bin/hf download mlx-community/Qwen3-Coder-Next-4bit
  ~/mlx-oai-env/bin/hf download mlx-community/Qwen3-235B-A22B-4bit-DWQ
  ~/mlx-oai-env/bin/hf download mlx-community/DeepSeek-R1-Distill-Qwen-32B-MLX-4Bit
  ~/mlx-oai-env/bin/hf download mlx-community/nomicai-modernbert-embed-base-4bit
"
```

The `huggingface-cli` command is deprecated as of 2026-Q1; `hf download` is the replacement.

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
