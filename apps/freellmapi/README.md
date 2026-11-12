# freellmapi

[freellmapi](https://github.com/tashfeenahmed/freellmapi) pools free-tier LLM providers (Groq, Cerebras, Mistral, OpenRouter free, Gemini and more) behind one OpenAI-compatible `/v1`. Here it is one upstream behind LiteLLM, never a router the fleet calls. The LiteLLM side is one route per model group (`freellm-<model>` in `apps/litellm/config.yaml`), priced 0 with no fallback edge, reached only by the gateway key `fleet-freellm`; those routes are added once a provider key is loaded and its model groups are known, so until then nothing calls this app.

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

The app keeps its state in SQLite, so `scripts/freellmapi-bootstrap.sh` applies it through the admin API: dashboard account, Unify on, auto update check off, the Premium license (rbw `free_llm_api_key`, $19/yr, bought 2026-11-12) and a key for each `providers.tsv` platform whose rbw entry exists. It is idempotent.

- Add a provider: sign up (a Google key comes from a new Cloud project, see `providers.tsv`), `rbw add freellmapi-<platform>` with the key as its password, run `scripts/freellmapi-bootstrap.sh`, then add its model groups as `freellm-<model>` routes in `apps/litellm/config.yaml`.
- Rotate a provider key: update its rbw entry and re-run the script; it rewrites only a key whose value differs.
- Client key for LiteLLM: `scripts/freellmapi-bootstrap.sh --client-key` prints only the key on stdout, stored as `FREELLMAPI_API_KEY` in `apps/litellm/secret.sops.yaml`.
- A lost volume is rebuilt from rbw and SOPS, which hold every input, so the volume has no backup. Re-run the script, then re-store the client key in LiteLLM: the app mints a new one on a fresh database and no env var can set it. The request logs are lost.
- Dashboard: `kubectl --context home-k3s -n ai-gateway port-forward svc/freellmapi 13001:3001`, then http://127.0.0.1:13001, login from SOPS.
- Upgrade: bump the image tag by PR. The app's self-update needs Docker and watchtower and is off.
- Model groups on offer: `curl -s localhost:13001/v1/models -H "Authorization: Bearer $(scripts/freellmapi-bootstrap.sh --client-key)"` through the port-forward.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Every stored key reads as invalid after a restore | `ENCRYPTION_KEY` changed. Restore the SOPS value, or wipe the PVC and re-run the bootstrap. |
| `setup_code_required` from the bootstrap | The setup call did not arrive from loopback. Run the script as written (it goes through `kubectl port-forward`). |
| Every freellm route answers 401 after the pod's volume was replaced | The fresh database minted a new client key. Re-store `--client-key` as `FREELLMAPI_API_KEY`. |
| `premium/key -> 400` after a rebuild | The license service refused the key (expired, canceled, or a seat limit). Recover or renew it at freellmapi.co, update rbw `free_llm_api_key`, re-run. |
| A `freellm-<model>` route answers 503 `no_providers_configured` | No platform with a key serves that model. Add a provider key, or drop the route. |
