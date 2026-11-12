# LiteLLM

LLM gateway deployment in the `ai-gateway` namespace, exposed through Traefik at `llm.kblab.me` on the LAN and the tailnet only.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, app, service, and ingress |
| `configmap.yaml` | LiteLLM config mounted into the container |
| `secret.sops.yaml` | SOPS-encrypted `litellm-db-secrets`: DATABASE_URL, salt key, UI creds, Lazer, Gemini and OpenCode Go keys |
| `deployment.yaml` | Main gateway workload and health checks |
| `service.yaml` | Cluster service on port `4000` |
| `ingress.yaml` | TLS ingress for `llm.kblab.me`, kept off public DNS |
| `ingress-metrics-deny.yaml` | 403s `/metrics` on the ingress host |

The litellm tenant role + database in Postgres is provisioned by `apps/postgres/litellm-db-bootstrap-job.yaml` (idempotent psql Job in the `databases` namespace).

## Exposure

`llm.kblab.me` has no public DNS record. LAN clients get it from AdGuard and tailnet clients from the headscale `extra_records` (`apps/headscale/configmap.yaml`), both pointing at Traefik. Both Ingresses carry `external-dns.alpha.kubernetes.io/controller: none`; drop it from either and external-dns republishes the host through the Cloudflare tunnel. Re-adding the annotation does not take a published host back off: external-dns runs `upsert-only` and never deletes, so remove the `llm.kblab.me` CNAME and its two `extdns-` TXT records in Cloudflare by hand.

Why: while the host was public, a scanner sent 190,844 guessed keys in two hours on 2026-11-07 (wordlist aimed at LiteLLM: `berriai-litellm-2025`, `dify-lab`, `sk-...master`). None matched and spend stayed $0, but the client IP was lost, because tunnel traffic reaches Traefik from the cloudflared pod.

`LiteLLMAuthFailureSpike` (`apps/monitoring/prometheus-rules-litellm.yaml`) fires on more than 30 rejected keys in 15 minutes, counted at Traefik. The keys tried are in Postgres:

```bash
kubectl -n databases exec deploy/postgres -- psql -U postgres -d litellm -c \
  "select date_trunc('hour',\"startTime\"), count(*), count(distinct api_key) from \"LiteLLM_SpendLogs\"
   where status='failure' and metadata->'error_information'->>'error_code'='401' group by 1 order by 1 desc limit 24"
```

## Configuration and Secrets

- `litellm-secrets` (out-of-band, holds `LITELLM_MASTER_KEY`) and `litellm-db-secrets` (SOPS, this directory) are both merged via `envFrom`.
- Config lives in `config.yaml`, generated into the `litellm-config` ConfigMap (hash-suffixed, so an edit rolls the pod) and mounted at `/etc/litellm/config.yaml`.
- Persistent state for keys/spend/budgets lives in Postgres (`postgres.databases.svc.cluster.local`); the `model_list` stays in `config.yaml` (`store_model_in_db: false`).

## Virtual Keys (Cost & Rate Control)

Every consumer of this gateway should hold a *scoped virtual key* — never `LITELLM_MASTER_KEY`. The master key creates/manages keys; runtime traffic uses the scoped keys.

Two teams split the $250/mo Lazer allowance by priority (human, 2026-11-10). A key joins the team its consumer's priority names; a key in no team never reaches a Lazer route except through its own small cap.

| Team | Budget | Holds |
|---|---|---|
| `lazer-core` | $200 / 1mo | `priority: must`: home services, comms triage and the version agents, knowledge-engine, lazer-eval |
| `lazer-pool` | $50 / 1mo | `priority: low`: cl --lazer (`claude-reserve`, last priority), model-lane, opencode-paid, delivery-loop |

Each key's consumer and priority live on the key itself (`metadata.consumer`, `metadata.priority`: must, low, off, reader), so the gateway is the one inventory. A key that is not a priority is blocked (`/key/block`, reversible with `/key/unblock`), as `glm-pilot` is since 2026-11-10. To see every key beside the agentctl runners that spend through it, with priority, budget, spend and fallback, open the Consumers section of the Costs tab (agent-web and the hub TUI, fed by `claude-acct gateway` every 15 min); a must consumer on a low-priority or capped budget shows there as MISMATCH. By hand, list every key, its team, cap, spend, consumer and priority:

```bash
curl -s "https://llm.kblab.me/key/list?return_full_object=true&size=100" -H "Authorization: Bearer $(cat ~/.local/state/agentctl/eval-reader.key)" \
  | jq -r '.keys[] | [.key_alias, (.team_id // "-")[0:8], (.max_budget // "-"), (.budget_duration // "-"), (.spend*100|round/100), (.blocked // false), .metadata.priority, .metadata.consumer] | @tsv' | column -t -s $'\t'
```

### OpenCode Go keys

The `go (...)` routes spend the OpenCode Go plan ($10/mo flat, key `OPENCODE_GO_API_KEY`, from rbw `opencode-go-api-key`), not the Lazer allowance, so their keys sit in no team: a team over its budget (lazer-pool, 2026-11-11) refuses every key in it, whatever the route. Check `team_id` is null with `/key/info` before a key's first run.

| Key | Models | Budgets | Consumer | Key file |
|---|---|---|---|---|
| `fleet-go` | the four `go (...)` routes | `max_budget` $68.80 / 7d (each route also has its own 5h budget in `config.yaml`: $2.85, deepseek $19) | fleet-dispatch harness `opencode-go` (priority low) | `~/.local/state/agentctl/fleet-go.key` |

The budgets mirror Go's windows a little under the plan, so the gateway refuses before Go does and the refusal reads as a budget, not an upstream error. Go gives GLM-5.3, Kimi K3 and Haiku 5.5 $15/mo each and DeepSeek V4.1 Flash $100/mo (docs/go, Go column), spent 20% per 5h and 50% per week. The 5h window is each route's deployment budget (`max_budget` 2.85, `budget_duration` 5h in `litellm_params`, 19%): a key's per-model `model_max_budget` needs a LiteLLM Enterprise license (`/key/generate` refuses it), and only `fleet-go` can reach these routes anyway. The week is the key's `max_budget`, 95% of the four models' weekly allowances ($7.50 x 3 + $50 = $72.50, rounded down), and the month is left to Go itself (Extra Usage off in the OpenCode console, so a spent window is a refusal there, never a charge). LiteLLM's windows reset on a fixed clock where Go's may roll, and the deployment spend is held in memory, so a pod restart clears the 5h counter. Go refuses a call with no `x-opencode-session` header (400 MissingSessionID): opencode sends one, a hand probe must too. Add a route: give it the same `max_budget` / `budget_duration`, add it to the key's `models` (`/key/update`) and raise the key's `max_budget` by 95% of its weekly allowance.

`eval-reader` is a `proxy_admin_viewer`: it reads keys, teams and spend but calls no model. Set a new key's `team_id`, `metadata.consumer` and `metadata.priority` when you mint it.

> **Category aliases:** `router_settings.model_group_alias` in `config.yaml` exposes short stable names (`code`, `reasoning`, `fast`, `vlm`, `embedding`, `stt`) that resolve to whichever concrete route a category points at. A key that allows the concrete route can call its alias (the `local-only` key calls `fast` through `fast (gpt-oss-120b)`). Retarget an alias in one line -> reconcile -> every consumer follows with no per-app edit. mem0 (`MEM0_DEFAULT_LLM_MODEL`), binks-api (`DEFAULT_MODEL`) and karakeep (`INFERENCE_TEXT_MODEL`) call `fast`.
>
> **Embedding is intentionally NOT wired to any stateful consumer via its alias.** mem0/karakeep pin `embedding (gemini-001)` because a vector store cannot hot-swap embedder dimensions (pgvector column = 768). Chat/vision/stt are stateless and safe to retarget; embedding is stateful and stays pinned.
>
> A model a key does not allow is a 403, not a fallback: add the route to the key (`/key/update`, `models`) before a consumer names it. Karakeep's key lacked the alias from 2026-07-19 to 2026-09-29 and every tagging job failed silently.

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
    "models": ["code", "fast", "reasoning", "code (qwen-3-coder)"],
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
```

Health endpoints used by the deployment:

- `/health/liveliness`
- `/health/readiness`

After a LiteLLM image bump, confirm `tool_compat.py` still fires on the `/v1/messages` bridge, because `test_tool_compat.py` stubs LiteLLM and cannot see a hook contract change. A 400 naming `output_config.format.name` means the hook stopped running:

```bash
curl -s https://llm.kblab.me/v1/messages \
  -H "Authorization: Bearer $(cat ~/.local/state/model-lane/litellm.key)" \
  -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":64,"messages":[{"role":"user","content":"Say hi as JSON."}],"output_config":{"format":{"type":"json_schema","schema":{"type":"object","properties":{"greeting":{"type":"string"}},"required":["greeting"],"additionalProperties":false}}}}'
```

## Backup Notes

Keys, budgets and spend live in the `litellm` database in shared Postgres, so `postgres-backup` covers them. Restore: [backup runbook](../../docs/backup-runbook.md), "Restore shared Postgres". The gateway keeps nothing on disk and has no PVC.

## Related Docs

- [../../docs/backup-runbook.md](../../docs/backup-runbook.md)
- [../../docs/gitops.md](../../docs/gitops.md)
- [../../infrastructure.md](../../infrastructure.md)
