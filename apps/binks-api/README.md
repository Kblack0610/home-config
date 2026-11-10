# binks-api

LLM chat backend for `binks.chat`. TypeScript replacement for the legacy Rust `binks-agent`. Source lives in [`platform`](https://github.com/BlackNBrownStudios/platform) at `apps/binks/api/`.

## Topology

- **Namespace**: `binks-api`
- **Image**: `git.kblab.me/kblack0610/binks-api` (Forgejo registry — `forgejo-registry` imagePullSecret)
- **DB**: in-namespace `binks-postgres` StatefulSet on a 5Gi PVC. Anonymous and signed-in chats both persisted here.
- **LLM gateway**: `http://litellm.ai-gateway.svc.cluster.local:4000`, using the scoped `local-only` virtual key in `binks-api-secret/LITELLM_API_KEY` (team `lazer-core`, see `apps/litellm/README.md`).
- **Ingress**: `binks.chat` (Traefik IngressRoute `binks-chat` in `ingress.yaml`): the SPA on `/`, the API on `/api` and `/ws`. TLS ends at Cloudflare.

## Deploy lifecycle

1. Tag in `platform`: `git tag binks-api-v<x.y.z> && git push origin binks-api-v<x.y.z>`.
2. The Forgejo workflow [`deploy-binks-api.yml`](https://github.com/BlackNBrownStudios/platform/blob/develop/.forgejo/workflows/deploy-binks-api.yml) builds + pushes `git.kblab.me/kblack0610/binks-api:v<x.y.z>`.
3. Same workflow bumps `images[].newTag` in this directory's `kustomization.yaml` and pushes to `home-config@master`.
4. Flux reconciles within ~1 min.

## Secrets

`secrets.yaml` is SOPS-encrypted with the home-k3s Age recipient. Holds:

- `binks-postgres-secret`: `username`, `password` for the in-namespace Postgres
- `binks-api-secret`: `DATABASE_URL`, `JWT_SECRET`, `LITELLM_API_KEY`

The `JWT_SECRET` is binks-only in v1 (no shared SSO with PlaceMyParents yet — that's the post-v1 paid-tier work, requires deciding on a cross-cluster secret strategy).
