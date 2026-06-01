# Cal.com (self-hosted scheduling)

Public Calendly-style booking page on `cal.kennethblack.me`. Reads free/busy
from up to 5 Google accounts via OAuth and merges them; writes confirmed
bookings to the destination calendar you designate inside the Cal.com UI.

We deploy the upstream `calcom/cal.com` Docker image rather than the
[cal.diy](https://github.com/calcom/cal.diy) fork — cal.diy is a code fork
that strips enterprise features but does not publish a separate image. For a
single-user lab the difference is moot; setting `NEXT_PUBLIC_LICENSE_CONSENT=agree`
plus not enabling teams/orgs gives the same MIT-only feature set.

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | `cal` namespace |
| `secret.yaml.template` | SOPS-encryption template (DB url, NextAuth secret, encryption key, Gmail SMTP) |
| `secret.yaml` | SOPS-encrypted secret (created during bootstrap) |
| `configmap.yaml` | Public URLs, telemetry-off flags, SMTP host/port |
| `pvc.yaml` | 5Gi local-path PVC for Next.js build cache + uploads |
| `web-deployment.yaml` | `calcom/cal.com:v6.2.0` (pinned), single replica, Recreate strategy |
| `web-service.yaml` | ClusterIP `:80` → pod `:3000` |
| `ingress.yaml` | Traefik + cert-manager `letsencrypt-dns`, host `cal.kennethblack.me` |
| `kustomization.yaml` | Roll-up |

## Bootstrap

Full step-by-step is at [`docs/runbooks/cal-com-setup.md`](../../docs/runbooks/cal-com-setup.md). Summary:

1. **Provision the database** on the shared Postgres in `databases` namespace
   (one role + one DB; no pgvector needed).
2. **Generate secrets** (`NEXTAUTH_SECRET`, `CALENDSO_ENCRYPTION_KEY`,
   `POSTGRES_CAL_PASSWORD`) and a Gmail app password.
3. **Fill `secret.yaml.template` → `secret.yaml`**, SOPS-encrypt.
4. **Stamp the same `POSTGRES_CAL_PASSWORD`** into `apps/postgres/secret.yaml`.
5. **Add `cal.kennethblack.me` DNS record** in Cloudflare (CNAME or A → home-k3s
   ingress IP, same target the portfolio uses).
6. **Activate** by adding `- cal` to `apps/kustomization.yaml`, commit, push.
7. **Configure Google Calendar inside the Cal.com UI** — install the Google
   Calendar app, OAuth-connect each of your 5 Gmails, pick one as the booking
   destination.

## Verify

```bash
# Flux reconciled
kubectl --context home-k3s -n cal get all
flux --context home-k3s reconcile kustomization apps --with-source

# Cert issued
kubectl --context home-k3s -n cal get certificate cal-tls

# Public URL responds + Cal.com health endpoint
curl -I https://cal.kennethblack.me
curl https://cal.kennethblack.me/api/auth/session   # 200 with empty session JSON

# DB connectivity from the cal pod
kubectl --context home-k3s -n cal exec deploy/cal-web -- \
  sh -c 'apt list --installed 2>/dev/null | grep -q postgresql-client && \
    psql "$DATABASE_URL" -c "SELECT 1" || \
    echo "no psql in image; use postgres pod for DB checks"'
```

## Image bumps

Cal.com tags are at <https://hub.docker.com/r/calcom/cal.com/tags>. After
bumping the tag in `web-deployment.yaml`:

- The pod's entrypoint runs `prisma migrate deploy` automatically before
  starting Next.js, so schema changes apply on rollout. Watch the first
  `kubectl logs` after the rollout.
- Major-version bumps (`v6 → v7`) — read upstream release notes first; cal.com
  has had migration-locking issues on multi-replica deployments. We're
  single-replica + Recreate so this is mostly moot.

## Backup

The actual data lives in Postgres (`databases.svc.cluster.local`), which
should be backed up via the shared Postgres app's pg_dump CronJob (tracked
as a follow-up in `apps/postgres/README.md`). The `cal-data` PVC only holds
Next.js build cache + uploaded avatars; loss is annoying not catastrophic.
