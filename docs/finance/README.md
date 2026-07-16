# Finance Stack Documentation

Self-hosted personal and business finance for the home lab. Front door for the finance docs.

## Current Apps

| App | Status | URL | Purpose |
|-----|--------|-----|---------|
| [Actual Budget](./finance-stack.md#actual-budget-personal-finance) | Live | https://finance.kblab.me | Personal + business budgeting (envelope) |

## Access

Actual Budget is served at https://finance.kblab.me via Traefik ingress (cert-manager TLS, CrowdSec bouncer). No port-forward or DNS setup needed - the `finance.kblab.me` record has resolved since the kblab.me migration. Log in with the Actual server password.

Port-forward is only an emergency fallback if ingress is broken:
```bash
kubectl --context home-k3s port-forward -n actual-budget svc/actual-budget 5006:80
```

## Documentation

- [Finance Stack Overview](./finance-stack.md) - what Actual is/isn't for, and the future business-finance options.
- [Tax categories + business/personal separation](./tax-categories.md) - the category scheme that makes exports tax-meaningful.
- [SimpleFIN reconnect](./simplefin-reconnect.md) - revive bank sync (stale since 2026-04-13).
- [Tax export](./tax-export.md) - activation-gated actual-http-api bridge + quarterly CSV to the NAS.

## Operations

- Manifests: `apps/actual-budget/` (Flux-managed).
- Backups: daily 03:00, 30-day local retention PLUS off-box NAS copy to `backups/home-k3s/actual-budget/` (SQLite is on a single-node local-path PVC, so the NAS copy is the durability tier). Verified weekly by `nas-backup-verify`.
- Manual backup: `kubectl --context home-k3s create job --from=cronjob/actual-budget-backup manual-backup-$(date +%s) -n actual-budget`
- Monitoring: gatus probes the service and the public ingress + cert expiry.

## Status (2026-07-16 audit)

Infra healthy (pod up 160d, TLS renewing, backups now off-box). Two open follow-ups that need you: (1) bank sync is stale since 2026-04-13 - see the SimpleFIN runbook; (2) the tax-export subsystem is built but activation-gated on your Actual password - see the tax-export doc.
