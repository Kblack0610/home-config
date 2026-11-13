# binks-api

LLM chat backend for `binks.chat`. TypeScript replacement for the legacy Rust `binks-agent`. Source lives in [`platform`](https://github.com/BlackNBrownStudios/platform) at `apps/binks/api/`.

## Topology

- **Namespace**: `binks-api`
- **Image**: `git.kblab.me/kblack0610/binks-api` (Forgejo registry — `forgejo-registry` imagePullSecret)
- **DB**: in-namespace `binks-postgres` StatefulSet on a 5Gi PVC. Anonymous and signed-in chats both persisted here.
- **LLM gateway**: `http://litellm.ai-gateway.svc.cluster.local:4000`, routes `binks-chat` (default) and `binks-chat-hard`. Signed-in users with an active $8/mo Square subscription get their own $2 / 1mo key, minted with `binks-api-secret/LITELLM_ISSUER_KEY`; anonymous chats use `LITELLM_API_KEY` (`binks-anon`). See `apps/litellm/README.md`, "binks.chat keys"
- **Ingress**: `binks.chat` (Traefik IngressRoute `binks-chat` in `ingress.yaml`): the SPA on `/`, the API on `/api` and `/ws`. TLS ends at Cloudflare.

## Deploy lifecycle

1. Tag in `platform`: `git tag binks-api-v<x.y.z> && git push origin binks-api-v<x.y.z>`.
2. The Forgejo workflow [`deploy-binks-api.yml`](https://github.com/BlackNBrownStudios/platform/blob/develop/.forgejo/workflows/deploy-binks-api.yml) builds + pushes `git.kblab.me/kblack0610/binks-api:v<x.y.z>`.
3. Same workflow bumps `images[].newTag` in this directory's `kustomization.yaml` and pushes to `home-config@master`.
4. Flux reconciles within ~1 min.

The `binks-chat` SPA follows the same steps with a `binks-chat-v<x.y.z>` tag and `deploy-binks-chat.yml`, which bumps its own `images[]` entry. Its manifest names `:latest`, so without that entry prod floats on an unnamed image and the `release-drift` watch trips.

## Secrets

`secrets.yaml` is SOPS-encrypted with the home-k3s Age recipient. Holds:

- `binks-postgres-secret`: `username`, `password` for the in-namespace Postgres
- `binks-api-secret`: `DATABASE_URL`, `JWT_SECRET`, `LITELLM_API_KEY` (rbw `litellm-binks-anon-key`), `LITELLM_ISSUER_KEY` (rbw `litellm-binks-issuer-key`), `SQUARE_ACCESS_TOKEN` (rbw `square_sandbox`), `SQUARE_WEBHOOK_SIGNATURE_KEY` (printed by the platform repo's `square:setup`)
- `binks-api-secret`, MCP connectors: `CONNECTOR_SECRET_KEY` (32 random bytes, base64; encrypts users' connector tokens at rest, so rotating it makes every stored token unreadable and those connectors must be re-added; unset turns connectors off)
- `binks-api-secret`, sign-in: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (Google Cloud project `binks-chat`, org blacknbrownstudios.com, account kenneth.black@blacknbrownstudios.com: Google Auth Platform > Clients > "binks-chat web", origin `https://binks.chat`, redirect `https://binks.chat/api/auth/google/callback`; consent screen "Binks", External, in Testing) and `APPLE_CLIENT_ID` (Services ID `chat.binks.web`), `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` (the .p8 PEM of key "Binks Sign in with Apple", primary App ID `chat.binks.app`, return URL `https://binks.chat/api/auth/apple/callback`). A new Google secret or Apple key is minted in those consoles on the agent machine (`store-console` skill) and piped into this file without touching disk; Apple shows a key once, so a lost one is revoked and replaced.

The `JWT_SECRET` belongs to Binks alone; tokens carry `aud: binks`. Square runs in sandbox: the plain env in `deployment.yaml` says so, and the switch to real money (production token, plan and webhook) is the owner's, per `apps/binks/api/README.md` in the platform repo.
