# Finance Stack Documentation

Self-hosted personal finance and budgeting tools.

## Current Apps

| App | Status | URL | Purpose |
|-----|--------|-----|---------|
| [Actual Budget](./finance-stack.md#actual-budget-personal-finance) | Deployed | `http://localhost:5006` (port-forward) | Personal budgeting |

## Quick Access

### Actual Budget

```bash
# Port forward (until DNS is configured)
kubectl port-forward -n actual-budget svc/actual-budget 5006:80 --context home-k3s

# Then open: http://localhost:5006
```

**Intended URL:** https://finance.kblab.me

## DNS Setup Required

Add DNS rewrite in AdGuard Home:
- Domain: `finance.kblab.me`
- Answer: `192.168.1.124` (or any Traefik ingress IP)

## Documentation

- [Finance Stack Overview](./finance-stack.md) - Full guide including future business finance options
