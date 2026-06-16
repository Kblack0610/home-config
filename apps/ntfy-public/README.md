# ntfy-public — the "ship feed"

A second, **public** ntfy broker (separate from `apps/ntfy/`, which is private and
LAN-only) that powers a curated, plain-English notification feed for non-technical
teammates. Reachable from the open internet at **https://ship.blacknbrownstudios.com**,
topic **`ship`**.

## What it's for

- **Per-deploy milestones** — `placemyparents` production deploys post here
  (`.github/workflows/deploy-production.yml`, job `broadcast-ship`): "🚀 PlaceMyParents
  v1.8.11 is going live in production."
- **Weekly digest** — a Monday roundup (`.github/workflows/ship-weekly-digest.yml`):
  "📊 This week: 4 changes shipped, current version v1.8.11."

No CI noise, no infra alerts — just human-readable release news.

## Why a separate instance (not the existing `apps/ntfy/`)

The private `ntfy` broker has **no app-layer auth**; its only protection is the
`monitoring-local-network-only` Traefik middleware, and the phone reads `notes-sync`
anonymously over Tailscale. ntfy's ACL is global per instance, so opening one public topic
on it would force tokens onto the working notes-sync phone/bridge flow. This instance
isolates the public surface completely:

- `auth-default-access: deny-all` — nothing open by default.
- `everyone` → **read-only** on `ship` (anyone with the link can subscribe; no account).
- `publisher` user → **read-write** on `ship` (CI + weekly digest publish, Basic auth).

The publisher credential is **self-hosted and fully ours** (SOPS-encrypted in
`secret.yaml`, mirrored into the platform repo's GitHub secret `NTFY_SHIP_PASS`). No
third-party SaaS key anywhere; the teammate needs nothing.

## How a teammate subscribes (one-time, ~2 min)

1. Install **ntfy** from the App Store / Play Store.
2. In the app: **+ Add subscription** → server `https://ship.blacknbrownstudios.com`,
   topic `ship` → Subscribe.
3. Done — release news pushes to their phone. (Or just bookmark
   https://ship.blacknbrownstudios.com/ship in a browser.)

## One-time setup checklist (operator)

- [ ] **Cloudflare tunnel** — add a public hostname `ship.blacknbrownstudios.com` →
  `https://traefik...` (same target as the other public sites) in the Cloudflare Zero Trust
  dashboard. The tunnel (`apps/cloudflared-public-sites/`) is token/remote-managed, so this
  routing lives in Cloudflare, not in git. This also creates the DNS CNAME.
- [ ] **GitHub secrets** on `BlackNBrownStudios/platform`: `NTFY_SHIP_PASS` (the publisher
  password, same value as `secret.yaml`) and optionally `NTFY_SHIP_USER` (defaults to
  `publisher`).
- [ ] Merge → Flux reconciles → cert issues via `letsencrypt-dns`.

## Access model details

Seeded idempotently by the deployment's init container on every pod start (handles password
rotation):

```sh
ntfy access everyone ship read-only
NTFY_PASSWORD="$NTFY_PUBLISHER_PASSWORD" ntfy user add --role=user publisher || true
NTFY_PASSWORD="$NTFY_PUBLISHER_PASSWORD" ntfy user change-pass publisher
ntfy access publisher ship read-write
```

## Verification

```bash
kubectl --context home-k3s -n ntfy-public get pods
curl -s https://ship.blacknbrownstudios.com/v1/health | jq          # health (unauthenticated)
curl -sN https://ship.blacknbrownstudios.com/ship/json &            # anonymous read works
curl -X POST -d hi https://ship.blacknbrownstudios.com/ship         # anonymous publish → 403
# Publish (Basic auth) — should appear in the open stream above:
curl -u "publisher:<password>" -d "test from operator" https://ship.blacknbrownstudios.com/ship
```

## Backups

Skipped, same rationale as `apps/ntfy/`: the PVC holds a 12h cache + the auth-file. The
auth-file is reseeded from `secret.yaml` on every pod start, so its loss is self-healing.
