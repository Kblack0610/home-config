# binks-api

LLM chat backend for `binks.chat`. TypeScript replacement for the legacy Rust `binks-agent`. Source lives in [`platform`](https://github.com/BlackNBrownStudios/platform) at `apps/binks/api/`.

## Topology

- **Namespace**: `binks-api`
- **Image**: `git.kblab.me/kblack0610/binks-api` (Forgejo registry — `forgejo-registry` imagePullSecret)
- **DB**: in-namespace `binks-postgres` StatefulSet on a 5Gi PVC. Anonymous and signed-in chats both persisted here. `binks-postgres-backup` (`backup-cronjob.yaml`) dumps it nightly at 2:25 to the NAS, `home-k3s/binks/`; restore: `docs/backup-runbook.md`, "Restore binks.chat".
- **Alerts**: `apps/monitoring/prometheus-rules-binks.yaml`: chat-turn and LLM error rates, and the `binks-anon` budget (share left, and spend per hour).
- **LLM gateway**: `http://litellm.ai-gateway.svc.cluster.local:4000`, routes `binks-chat` (default) and `binks-chat-hard`. Signed-in users with an active $8/mo Square subscription get their own $2 / 1mo key, minted with `binks-api-secret/LITELLM_ISSUER_KEY`; anonymous chats use `LITELLM_API_KEY` (`binks-anon`). See `apps/litellm/README.md`, "binks.chat keys"
- **Replicas**: 2, with a PodDisruptionBudget (`pdb.yaml`, minAvailable 1). The HTTP limiter and the per-minute WS limit count per pod; the signed-out daily cap and the spend ceiling are in Postgres and shared.
- **Capacity**: the launch limits (`RATE_LIMIT_MAX`, `ANON_TURNS_PER_IP_PER_DAY`, `DAILY_SPEND_CEILING_USD`, `WAITLIST_ENABLED`) take their defaults from the platform repo, `apps/binks/api/README.md` "Launch capacity". Set one in `deployment.yaml` `env` only to override it (the ceiling is set there to $2 a day); `WAITLIST_ENABLED: 'true'` is the manual switch that stops new chats from non-subscribers.
- **Ingress**: `binks.chat` (Traefik IngressRoute `binks-chat` in `ingress.yaml`): the SPA on `/`, the API on `/api` and `/ws`. TLS ends at Cloudflare.
- **Shared demos**: `play.kblack.dev` (IngressRoute `binks-demos` in `ingress.yaml`) routes only `/p/` to binks-api, which serves published pages there sandboxed (`PUBLISH_*` and `DEMO_*` env in `deployment.yaml`; platform `apps/binks/api/README.md`, Shareable demos). It is a separate site from binks.chat on purpose: a shared page never gets the chat's origin.

## Deploy lifecycle

1. Tag a commit on `develop` in `platform`: `git tag binks-v<x.y.z> && git push origin binks-v<x.y.z>`; one tag releases the api and the SPA. The workflow's `gate` job refuses a ref not on `develop` and runs typecheck, lint and test before it builds.
2. The Forgejo workflow [`deploy-binks-api.yml`](https://github.com/BlackNBrownStudios/platform/blob/develop/.forgejo/workflows/deploy-binks-api.yml) builds + pushes `git.kblab.me/kblack0610/binks-api:v<x.y.z>`.
3. Same workflow bumps `images[].newTag` in this directory's `kustomization.yaml` and pushes to `home-config@master`.
4. Flux reconciles within ~1 min.

A rollout drops no requests: both Deployments wait 10s in `preStop` so Traefik removes a terminating pod's endpoint before it gets SIGTERM (without it, one request 502'd per rollout, bk-45). `terminationGracePeriodSeconds: 45` on binks-api covers that wait plus the api's 30s shutdown timeout.

The `binks-chat` SPA (platform `apps/binks/web`) builds on the same tag in `deploy-binks-chat.yml`, which bumps its own `images[]` entry. Its manifest names `:latest`, so without that entry prod floats on an unnamed image and the `release-drift` watch trips.

## Secrets

`secrets.yaml` is SOPS-encrypted with the home-k3s Age recipient. Holds:

- `binks-postgres-secret`: `username`, `password` for the in-namespace Postgres
- `binks-api-secret`: `DATABASE_URL`, `JWT_SECRET`, `LITELLM_API_KEY` (rbw `litellm-binks-anon-key`), `LITELLM_ISSUER_KEY` (rbw `litellm-binks-issuer-key`), `SQUARE_ACCESS_TOKEN` (rbw `square_sandbox`), `SQUARE_WEBHOOK_SIGNATURE_KEY` (printed by the platform repo's `square:setup`)
- `binks-api-secret`, MCP connectors: `CONNECTOR_SECRET_KEY` (32 random bytes, base64; encrypts users' connector tokens at rest, so rotating it makes every stored token unreadable and those connectors must be re-added; unset turns connectors off)
- `binks-api-secret`, outside clients: `CLIENT_TOKEN_SECRET` (64 random URL-safe characters, separate from `JWT_SECRET`; signs only the 1-hour access tokens binks issues to Claude Code, opencode and the binks CLI, so rotating it makes those clients refresh, and API tokens are unaffected). Read only while `MCP_ENABLED` is `'true'` in `deployment.yaml`, which is the kill switch for every outside door.
- `binks-api-secret`, sign-in: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (Google Cloud project `binks-chat`, org blacknbrownstudios.com, account kenneth.black@blacknbrownstudios.com: Google Auth Platform > Clients > "binks-chat web", origin `https://binks.chat`, redirect `https://binks.chat/api/auth/google/callback`; consent screen "Binks", External, in Testing) and `APPLE_CLIENT_ID` (Services ID `chat.binks.web`), `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` (the .p8 PEM of key "Binks Sign in with Apple", primary App ID `chat.binks.app`, return URL `https://binks.chat/api/auth/apple/callback`). A new Google secret or Apple key is minted in those consoles on the agent machine (`store-console` skill) and piped into this file without touching disk; Apple shows a key once, so a lost one is revoked and replaced.

The `JWT_SECRET` belongs to Binks alone; tokens carry `aud: binks`. Square runs in sandbox: the plain env in `deployment.yaml` says so, and the switch to real money (production token, plan and webhook) is the owner's, per `apps/binks/api/README.md` in the platform repo.

## Binks demo token

The fleet publishes demos (`binks-demo publish`, the `quick-demo` skill) as its own binks user, `fleet-demos@fleet.invalid` (not a real mailbox, so nobody can sign up as it), with a `bk_pat_` API token. `/api/demos` takes that token directly (platform #1590), so it works with `MCP_ENABLED` off.

- Mint or rotate, from the repo root: `scripts/binks-mint-pat.sh fleet-demos@fleet.invalid binks-demo-fleet 365 apps/binks-api/accounts.sops.yaml`. One run creates the user if missing, stores only the token's sha256 in Postgres by `kubectl exec`, revokes the user's older tokens of that name in the same transaction, and writes the new token into the SOPS file. The token is never printed; a failed `sops` aborts before the database is touched.
- Where it lives: `apps/binks-api/accounts.sops.yaml`, key `BINKS_DEMO_TOKEN`, main age key only, never deployed. `~/.config/binks-demo/env` (private dotfiles overlay) names that file in `BINKS_DEMO_SECRET`.
- Life: 365 days (the script takes any whole number of days from 1). Rotate a month early by running the same command.
- Revoke now, over `kubectl exec` into `binks-postgres-0`: `UPDATE personal_access_tokens SET revoked_at = CURRENT_TIMESTAMP WHERE name = 'binks-demo-fleet' AND revoked_at IS NULL`; the next publish gets 401.

| Symptom | Cause and fix |
|---|---|
| `publish: not found (404)` | `PUBLISH_ENABLED` is `false` or the running binks-api tag predates platform #1567. |
| 401 `Unknown API token` | The token in SOPS is not in the database: re-mint. |
| 401 `This API token has expired` or `was revoked` | Mint a new one. |
| 403 `Publishing demos needs Binks Plus` | `DEMO_PUBLISH_TIER` is `plus` and the fleet user has no subscription: set it to `all`. |
