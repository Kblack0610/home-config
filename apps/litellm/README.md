# LiteLLM

LLM gateway deployment in the `ai-gateway` namespace, exposed through Traefik at `llm.kblab.me`.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, app, service, ingress, and backup job |
| `configmap.yaml` | LiteLLM config mounted into the container |
| `secret.sops.yaml` | SOPS-encrypted `litellm-db-secrets`: DATABASE_URL, salt key, UI creds, Lazer key |
| `deployment.yaml` | Main gateway workload and health checks |
| `service.yaml` | Cluster service on port `4000` |
| `ingress.yaml` | TLS ingress for `llm.kblab.me` |
| `pvc.yaml` | Persistent application data |
| `backup-cronjob.yaml` | Daily local and NAS backup job |
| `backup-nas-secret.yaml` | NAS SMB credentials for backup upload |

The litellm tenant role + database in Postgres is provisioned by `apps/postgres/litellm-db-bootstrap-job.yaml` (idempotent psql Job in the `databases` namespace).

## Configuration and Secrets

- `litellm-secrets` (out-of-band, holds `LITELLM_MASTER_KEY`) and `litellm-db-secrets` (SOPS, this directory) are both merged via `envFrom`.
- Config is mounted from `litellm-config` to `/etc/litellm/config.yaml`.
- Persistent state for keys/spend/budgets lives in Postgres (`postgres.databases.svc.cluster.local`); the `model_list` stays in `configmap.yaml` (`store_model_in_db: false`).

## Virtual Keys (Cost & Rate Control)

Every consumer of this gateway should hold a *scoped virtual key* — never `LITELLM_MASTER_KEY`. The master key creates/manages keys; runtime traffic uses the scoped keys.

| Key alias | Allowed models | `max_budget` (30d) | `tpm` / `rpm` | Consumer |
|---|---|---|---|---|
| `karakeep-tagging` | `fast (Qwen3-4B)` | none (free upstream) | rpm 60 | `apps/karakeep/` |
| `mem0-embeddings` | `embedding (modernbert-embed-base-4bit)`, `fast (Qwen3-4B)` | none (free upstream) | rpm 120 | `apps/mem0/` |
| `openclaw-coding` | `code (...)`, `reasoning (...)`, `fast (...)` | none (free upstream) | tpm 100k | `apps/openclaw/` |
| `opencode-default` | free local + the toggle aliases (`code`/`reasoning`/`fast`) | $50 | tpm 50k, rpm 30 | opencode default mode |
| `opencode-premium` | the paid Lazer routes (`code (qwen-3-coder)`, `reasoning (deepseek-v4-pro)`, `fast (gpt-oss-120b)`, `vlm (gemini-3-flash)`) | $150 | tpm 100k | opencode build/escalation mode |

> **Toggle aliases (2026-07):** `router_settings.model_group_alias` in `configmap.yaml` exposes short stable names (`code`, `reasoning`, `fast`, `vlm`, `embedding`, `stt`) that resolve to whichever concrete route a category currently points at. Consumers/keys can allow the short alias and stay put while you flip local-vs-paid in one line. Each category also has explicit local (`... local model ...`) and paid (`... (lazer) ...`) routes for direct A/B testing.
>
> **Consumers now call the aliases (so a flip actually reaches them):** mem0 (`MEM0_DEFAULT_LLM_MODEL: "fast"`), karakeep (`INFERENCE_TEXT_MODEL: "fast"`), and openclaw (primary `litellm/code`, fallback `litellm/reasoning`, image/pdf `litellm/vlm`, compaction `litellm/fast`) reference category aliases rather than pinned route names. Flip a `model_group_alias` value -> reconcile -> every consumer follows with no per-app edit.
>
> **Two safety rails that make a local flip non-destructive:**
> - `router_settings.fallbacks` has local->cloud chains keyed by each local route (e.g. `fast (Qwen3-4B-8bit)` -> `fast (gpt-oss-120b)` -> `fast (gemini-2.0-flash)`), so a down box or a cold on_demand timeout spills to cloud instead of erroring.
> - **Embedding is intentionally NOT wired to any stateful consumer via its alias.** mem0/karakeep pin an explicit embedder because a vector store cannot hot-swap embedder dimensions (local Qwen3 = 1024-dim, pgvector column = 768). Chat/vision/stt are stateless and safe to toggle; embedding is stateful and stays pinned.
>
> **How to flip a category to local** (once the Mac Studio box is verified up for it): set the alias value to the local route, e.g. `fast: "fast (Qwen3-4B-8bit)"`, then `git push forgejo master && flux reconcile kustomization apps --with-source && kubectl -n ai-gateway delete pod -l app=litellm` (configmap remount). Revert the one line to roll back.

### Create a key

The LiteLLM admin UI is reachable at `https://llm.kblab.me/ui/` (Tailscale-gated). Log in with `UI_USERNAME` / `UI_PASSWORD` from `litellm-db-secrets`. Or via API:

```bash
MASTER_KEY=$(kubectl -n ai-gateway get secret litellm-secrets \
  -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)

curl -sX POST https://llm.kblab.me/key/generate \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_alias": "opencode-default",
    "models": ["code (Qwen3-Coder-Next-4bit)", "fast (Qwen3-4B)", "reasoning (Qwen3.6-35B-A3B-4bit)", "code (qwen-3-coder)"],
    "max_budget": 50.00,
    "budget_duration": "30d",
    "tpm_limit": 50000,
    "rpm_limit": 30
  }'
```

The response contains `"key": "sk-..."` — store this where the consumer can read it. After creation, rotate the consumer's `LITELLM_API_KEY` Secret to this value (or place it in shell env for opencode).

### Inspect / retire

```bash
# inspect
curl -s "https://llm.kblab.me/key/info?key=sk-..." -H "Authorization: Bearer $MASTER_KEY"

# spend
curl -s "https://llm.kblab.me/spend/logs?api_key=sk-..." -H "Authorization: Bearer $MASTER_KEY"

# delete
curl -sX POST https://llm.kblab.me/key/delete \
  -H "Authorization: Bearer $MASTER_KEY" \
  -d '{"keys": ["sk-..."]}'
```

## Deploy

```bash
kubectl apply -k apps/litellm
```

Prefer committing manifest changes and reconciling through Flux for normal operations.

## Verify

```bash
kubectl -n ai-gateway get all
kubectl -n ai-gateway logs deployment/litellm --tail=100
kubectl -n ai-gateway get cronjob litellm-backup
```

Health endpoints used by the deployment:

- `/health/liveliness`
- `/health/readiness`

## Backup Notes

- The `litellm-backup` CronJob runs daily at `02:00`.
- Local backups are written to `/var/backups/litellm` on the node.
- NAS upload is best-effort and uses SMB credentials from `backup-nas-credentials`.

### Known gap: keys + spend now live in Postgres, not in `/app/data`

Once `general_settings.database_url` is set, virtual keys, per-key budgets, and spend logs move from sqlite (`/app/data/litellm.db`) to the `litellm` database in `postgres.databases.svc.cluster.local`. The current `backup-cronjob.yaml` only protects `/app/data` and so no longer captures the load-bearing data.

`apps/postgres/` does not yet have a backup CronJob (see `apps/postgres/README.md`'s "Backup: Not yet wired"). The PVC + backup CronJob in this directory are kept as a no-op placeholder until that gap is closed; deleting them would mean zero protection for the new key/spend store. Follow-up: add `apps/postgres/backup-cronjob.yaml` (`pg_dump` → NAS), then drop `pvc.yaml`, `backup-cronjob.yaml`, and `backup-nas-secret.yaml` from this directory.

## Related Docs

- [../../docs/backup-runbook.md](../../docs/backup-runbook.md)
- [../../docs/gitops.md](../../docs/gitops.md)
- [../../infrastructure.md](../../infrastructure.md)
