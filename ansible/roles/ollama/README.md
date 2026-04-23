# ollama

Installs Ollama via Homebrew on macOS and manages it as a brew service. Ollama serves as the fallback LLM path on `mac-studio`; the primary path is MLX via the `launchd-mlx-services` role.

## What it does

1. `brew install ollama` (idempotent).
2. `brew services start ollama`.
3. Probes `http://localhost:11434/api/tags` to confirm the API is live.

## Out of scope

- **Model pulls.** Run `ollama pull <model>` manually (or add a companion role/playbook that takes a `ollama_models` list and runs `ollama pull` per entry). Ansible-managing large model downloads ties up disk I/O on every play run.
- **GPU / Metal config.** Ollama on Apple Silicon uses Metal by default; no config needed.

## Binding in site.yml

Authored + unbound. Enable with:

```yaml
- name: Ollama on mac-studio
  hosts: mac-studio
  gather_facts: true
  roles:
    - role: ollama
      tags: [llm]
```
