# binks-api

LLM chat backend for `binks.chat`. TypeScript replacement for the legacy Rust `binks-agent`. Source lives in [`platform`](https://github.com/BlackNBrownStudios/platform) at `apps/binks/api/`.

## Topology

- **Namespace**: `binks-api`
- **Image**: `git.kblab.me/kblack0610/binks-api` (Forgejo registry — `forgejo-registry` imagePullSecret)
- **DB**: in-namespace `binks-postgres` StatefulSet on a 5Gi PVC. Anonymous and signed-in chats both persisted here.
- **LLM gateway**: `http://litellm.ai-gateway.svc.cluster.local:4000` — uses the cluster's `litellm-secrets/LITELLM_MASTER_KEY` (mirrored into `binks-api-secret/LITELLM_API_KEY`).
- **Shadow ingress**: `binks-next.kblab.me` (LAN-only, Traefik IngressRoute). The production `binks.chat` ingress is **not** managed here — it still points at the legacy `binks-agent`.

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

## Cutover (post-soak)

When `binks-next.kblab.me` looks healthy, edit the production `binks.chat` IngressRoute (currently in the `ai-services` namespace pointing at `binks-agent`) to swap the backend service to `binks-api` in this namespace. Then scale `binks-agent` to zero.
