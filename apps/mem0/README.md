# mem0

Self-hosted [mem0-api-server](https://github.com/mem0ai/mem0/tree/main/server) (Apache-2.0) deployed in the `memory` namespace. Cross-project, cross-tool long-term memory backing the `mem0-ops` skill at `~/.claude/skills/mem0-ops/`.

LLM and embedder calls are routed through the in-cluster LiteLLM gateway (`apps/litellm/`) — no third-party API keys leave the cluster. Postgres + pgvector lives in the shared `apps/postgres/` deployment.

Background and architecture rationale: `~/.agent/plans/home-config/active/2026-04-26_home-config_mem0-self-host.md` (note: plan was rewritten across Sessions 3-6 — the architecture in the plan reflects Session 2's older Qdrant-based design and is now stale; the manifests here are the source of truth).

## Why a skill, not an MCP server

The previous iteration of this app shipped its own custom MCP server bridging Claude Code → mem0 REST. That layer was unnecessary: mem0 publishes an official Apache-2.0 [SKILL.md](https://github.com/mem0ai/mem0/tree/main/skills/mem0) and the `mem0` Python/TypeScript SDKs work directly against any OpenAI-shaped REST endpoint. The `mem0-ops` skill at `~/.claude/skills/mem0-ops/` instructs the agent to call `https://mem0.kblab.me/v1/...` directly via Bash + curl or the SDK. Cross-tool reach (OpenCode, Codex CLI) goes through `AGENTS.md`, not MCP.

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | `memory` namespace |
| `configmap.yaml` | Non-secret env (Postgres host, OpenAI base URL, LLM/embedder model names, AUTH_DISABLED) |
| `secret.yaml.template` | SOPS-encryption template (POSTGRES_PASSWORD, OPENAI_API_KEY, JWT_SECRET, ADMIN_API_KEY) |
| `pvc.yaml` | 1Gi local-path PVC for `history.db` |
| `deployment.yaml` | mem0-api-server with init container running `alembic upgrade head` |
| `service.yaml` | ClusterIP `:8000` |
| `ingress.yaml` | TLS at `mem0.kblab.me`, restricted to RFC1918 |
| `kustomization.yaml` | Roll-up (excludes secret until encrypted file exists) |

## Dependencies (deploy order)

1. `apps/postgres/` — must be reconciled and running first (mem0 init container fails if Postgres is unreachable).
2. `apps/litellm/` — already running; new entry for `nomic-embed-text-v1.5` model is in its configmap.
3. Mac Studio — must be serving the `nomic-embed-text-v1.5` embedding endpoint that LiteLLM fronts (handled by `ansible/roles/launchd-mlx-openai-server/`, in-flight migration).

## Bootstrap

1. Generate the values:

   ```bash
   openssl rand -hex 32                                          # JWT_SECRET
   openssl rand -hex 32                                          # ADMIN_API_KEY (held for later)
   kubectl --context home-k3s -n ai-gateway \
     get secret litellm-secrets \
     -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d        # OPENAI_API_KEY
   # POSTGRES_PASSWORD = same value as POSTGRES_MEM0_PASSWORD in apps/postgres/secret.yaml
   ```

2. Copy template, fill in, encrypt:

   ```bash
   cp apps/mem0/secret.yaml.template apps/mem0/secret.yaml
   $EDITOR apps/mem0/secret.yaml
   sops --encrypt --in-place apps/mem0/secret.yaml
   ```

3. Add `- secret.yaml` to `apps/mem0/kustomization.yaml`. Commit + push. Flux reconciles.

## Verify

```bash
kubectl --context home-k3s -n memory get all
kubectl --context home-k3s -n memory logs deployment/mem0 -c alembic-upgrade   # should show migrations applied
kubectl --context home-k3s -n memory logs deployment/mem0 --tail=50            # uvicorn startup
kubectl --context home-k3s -n memory get ingress
```

Smoke test from inside the cluster (avoids the LAN allowlist):

```bash
kubectl --context home-k3s -n memory port-forward svc/mem0 8000:8000
# in another shell:
curl http://localhost:8000/docs       # 200 OK = uvicorn up
curl -X POST http://localhost:8000/configure \
  -H "Content-Type: application/json" \
  -d '{"version": "v1.1"}'            # configure endpoint
```

End-to-end memory round-trip (with AUTH_DISABLED=true; once auth is on, add `Authorization: Bearer <ADMIN_API_KEY>`):

```bash
curl -X POST http://localhost:8000/memories \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "I prefer shell+neovim over Obsidian for notes."}],
    "user_id": "kblack0610"
  }'

curl "http://localhost:8000/memories?user_id=kblack0610"
```

## Verify against the running cluster Postgres

```bash
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql -U postgres -c "\l" | grep -E "mem0|mem0_app"   # both DBs exist
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql -U postgres -d mem0 -c "\dt"                    # vector tables created post-first-add
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql -U postgres -d mem0_app -c "\dt"                # alembic-managed tables
```

## Wiring CLIs via the skill

After this deployment is healthy, the `~/.claude/skills/mem0-ops/SKILL.md` skill teaches Claude Code to call `https://mem0.kblab.me/v1/...` directly. Cross-tool: place `AGENTS.md` at the repo root (or `~/.dotfiles/AGENTS.md` for cross-repo) referencing the skill so OpenCode + Codex CLI pick it up.

## Backup

mem0's durable state is the Postgres data (vector store + app metadata) — backed up via the shared `apps/postgres/` (CronJob to be added). The `history.db` on this app's PVC is replaceable; not critical to back up.

## Upstream patches applied at runtime

The deployment's container `command:` wraps the upstream image's CMD with a
single `sed` patch before launching uvicorn. The patches are visible in
`deployment.yaml`; this section explains *why*.

| # | Patch | Reason | Drop when |
|---|-------|--------|-----------|
| 1 | `pgvector.py` line 251: `score=float(r[1])` → `score=float(1.0 - r[1])` | mem0 v2.0.1's pgvector backend returns cosine **distance** as the `score` field, but `score_and_rank` (used by `/search`) treats `score` as similarity and sorts `reverse=True`. Without this fix, the LEAST-similar memories rank first — exact-text matches end up dead last. The TS SDK was fixed in upstream PR [#4944](https://github.com/mem0ai/mem0/pull/4944); the Python equivalent is not yet upstream. | A new image tag containing the Python equivalent of #4944 lands. Then drop `command:` + `args:` from `deployment.yaml`, restoring the image's default CMD. |

The container fails fast (`grep -q ... || exit 1`) if a future image bump
silently breaks the patch (e.g., upstream rewrites the line) — better than
running with broken ranking.

## Switching auth on later

When you want auth (e.g., adding more devices to the network, broader Tailscale exposure):

1. Edit `apps/mem0/configmap.yaml`: set `AUTH_DISABLED: "false"`.
2. Commit + push + reconcile + restart pod.
3. Hit the dashboard at `https://mem0.kblab.me` and run the setup wizard, OR use the held `ADMIN_API_KEY` for headless registration via `scripts/seed.sh` upstream.
4. Update the `mem0-ops` skill to include `Authorization: Bearer <api-key>` in its curl examples.
