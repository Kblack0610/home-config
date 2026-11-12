# freellmapi

[freellmapi](https://github.com/tashfeenahmed/freellmapi) pools free-tier LLM providers (Groq, Cerebras, Mistral, OpenRouter free, Gemini and more) behind one OpenAI-compatible `/v1`. Here it is one upstream behind LiteLLM, never a router the fleet calls: LiteLLM holds one route per model group (`freellm-<model>` in `apps/litellm/config.yaml`), priced 0 with no fallback edge, and only the gateway key `fleet-freellm` reaches them.

With Unify on, a request that names a model is pinned to that model's group: freellmapi fails over between the providers serving that model and never to a different model (upstream `docs/en/architecture/01-routing-and-bandit-scoring.md` section 11), so a model-lane score on `freellm-<model>` is a score for that model. Every response carries `X-Routed-Via: <platform>/<model>`.

Free providers may train on prompts, so these routes carry public input only, like `opencode-free`.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the app into `ai-gateway` (the Namespace is `apps/litellm/namespace.yaml`) |
| `secret.sops.yaml` | `freellmapi-secrets`: `ENCRYPTION_KEY` (mounted), `ADMIN_EMAIL` and `ADMIN_PASSWORD` (read only by the bootstrap) |
| `pvc.yaml` | `local-path` volume for its SQLite: provider keys (encrypted with `ENCRYPTION_KEY`), the license, request logs |
| `deployment.yaml` | One pod, image pinned, runs as uid 1000 |
| `service.yaml` | ClusterIP on 3001; no ingress, LiteLLM is the only client |
| `providers.tsv` | Which platform key comes from which rbw entry, and where the human signs up |

## Setup and changes

The app's state is in SQLite, not in env, so `scripts/freellmapi-bootstrap.sh` applies it through the admin API: dashboard account, Unify on, auto update check off, the Premium license (rbw `free_llm_api_key`, $19/yr, bought 2026-11-12) and a key for each `providers.tsv` platform whose rbw entry exists. It is idempotent.

- Add a provider: sign up, `rbw add freellmapi-<platform>` with the key as its password, run `scripts/freellmapi-bootstrap.sh`, then add its model groups as `freellm-<model>` routes in `apps/litellm/config.yaml`.
- Client key for LiteLLM: `scripts/freellmapi-bootstrap.sh --client-key`, stored as `FREELLMAPI_API_KEY` in `apps/litellm/secret.sops.yaml`.
- Dashboard: `kubectl --context home-k3s -n ai-gateway port-forward svc/freellmapi 13001:3001`, then http://127.0.0.1:13001, login from SOPS.
- Upgrade: bump the image tag by PR. The app's self-update needs Docker and watchtower and is off.
- Model groups on offer: `curl -s localhost:13001/v1/models -H "Authorization: Bearer $(scripts/freellmapi-bootstrap.sh --client-key)"` through the port-forward.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Every stored key reads as invalid after a restore | `ENCRYPTION_KEY` changed. Restore the SOPS value, or wipe the PVC and re-run the bootstrap. |
| `setup_code_required` from the bootstrap | The setup call did not arrive from loopback. Run the script as written (it goes through `kubectl port-forward`). |
| A `freellm-<model>` route answers 503 `no_providers_configured` | No platform with a key serves that model. Add a provider key, or drop the route. |
