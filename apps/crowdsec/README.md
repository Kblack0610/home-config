# CrowdSec — Public Ingress Protection

IDS/IPS for the home-k3s cluster. Ingests Traefik access logs, detects malicious
behavior, and blocks offending IPs via a forward-auth bouncer on public ingresses.

## Architecture

```
Internet → Traefik (kube-system)
               │
               ├─ ForwardAuth ──→ CrowdSec Bouncer (crowdsec ns)
               │                        │
               │                   crowdsec-service (LAPI)
               │                        │
               │                   CrowdSec Agent (DaemonSet)
               │                   reads Traefik container logs
               │
           Only applied to:
             finance.kblab.me  (actual-budget)
             git.kblab.me      (forgejo)
```

### Components

| Component | Image | Purpose |
|-----------|-------|---------|
| Security Engine (LAPI + Agent) | `crowdsecurity/crowdsec` (Helm chart v0.22.1) | Parses Traefik logs, detects attacks, maintains ban decisions |
| Forward-Auth Bouncer | `fbonalair/traefik-crowdsec-bouncer:0.5.0` | Checks each request against LAPI decisions, returns 403 for banned IPs |

### How it works

1. Traefik writes JSON access logs to stdout (enabled via `HelmChartConfig`)
2. CrowdSec agent reads Traefik pod logs via host log mounts (`/var/log/pods/`)
3. Agent parses logs with `crowdsecurity/traefik` collection and feeds alerts to LAPI
4. LAPI also pulls community blocklists from CrowdSec CAPI (free, anonymous)
5. On each HTTP request, Traefik's ForwardAuth middleware queries the bouncer
6. Bouncer checks the client IP against LAPI decisions → allow or 403

### External connections

CrowdSec connects to CAPI (Central API) for **free community blocklists** —
crowd-sourced threat intelligence from all CrowdSec users. No account needed.
This is the primary advantage over fail2ban. Everything else runs locally.

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | `crowdsec` namespace |
| `helmrepository.yaml` | Flux source for CrowdSec Helm charts |
| `helmrelease.yaml` | CrowdSec engine (LAPI + agent DaemonSet) |
| `bouncer-deployment.yaml` | Forward-auth bouncer container |
| `bouncer-service.yaml` | ClusterIP service for the bouncer |
| `middleware.yaml` | Traefik ForwardAuth middleware |
| `secret.yaml` | SOPS-encrypted bouncer API key |

### Related files outside this directory

| File | Change |
|------|--------|
| `infrastructure/traefik/helmchartconfig.yaml` | Enables Traefik access logs + `externalTrafficPolicy: Local` |
| `apps/actual-budget/ingress.yaml` | Middleware annotation for finance.kblab.me |
| `apps/forgejo/ingress.yaml` | Middleware annotation for git.kblab.me |

## Operations

### Check engine status

```bash
# Pods running
kubectl --context home-k3s get pods -n crowdsec

# Installed collections
kubectl --context home-k3s exec -n crowdsec deploy/crowdsec-lapi -- cscli collections list

# Active decisions (bans)
kubectl --context home-k3s exec -n crowdsec deploy/crowdsec-lapi -- cscli decisions list

# Registered bouncers
kubectl --context home-k3s exec -n crowdsec deploy/crowdsec-lapi -- cscli bouncers list
```

### Manually ban/unban an IP

```bash
# Ban
kubectl --context home-k3s exec -n crowdsec deploy/crowdsec-lapi -- \
  cscli decisions add --ip 1.2.3.4 --type ban --duration 1h --reason "manual test"

# Unban
kubectl --context home-k3s exec -n crowdsec deploy/crowdsec-lapi -- \
  cscli decisions delete --ip 1.2.3.4
```

### View Traefik access logs

```bash
kubectl --context home-k3s logs -n kube-system deploy/traefik --tail=20
```

### Rotate the bouncer API key

```bash
# Decrypt, replace key, re-encrypt
sops apps/crowdsec/secret.yaml          # opens in $EDITOR
sops -e -i apps/crowdsec/secret.yaml    # re-encrypt after editing

# Then restart both LAPI and bouncer to pick up new key
kubectl --context home-k3s rollout restart -n crowdsec deploy/crowdsec-lapi deploy/crowdsec-bouncer
```

## Phases

- **Phase 1 (this PR)**: Engine + bouncer on public ingresses only
- **Phase 2**: Prometheus ServiceMonitor + Grafana dashboard
- **Phase 3**: OpenWrt firewall bouncer (block at the router level)
- **Phase 4**: CrowdSec AppSec/WAF for virtual patching
- **Phase 5**: Extend to do-nyc3-prod cluster
