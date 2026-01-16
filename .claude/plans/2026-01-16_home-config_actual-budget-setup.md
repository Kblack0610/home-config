# Actual Budget Self-Hosted Setup Plan

**Date**: 2026-01-16
**Project**: home-config
**Feature**: Actual Budget deployment for personal finance tracking

## Overview

Deploy Actual Budget to the existing k3s cluster with Traefik ingress, SimpleFIN bank sync, and automated backups via CronJob.

## Target Repository

`/home/kblack0610/dev/home/home-config`

## Requirements

- Self-hosted finance tracking software
- Automated bank syncing via SimpleFIN (~$1.50/month)
- Daily automated backups with 30-day retention
- Accessible via `finance.blackk.dev`

## Directory Structure

```
home-config/apps/actual-budget/
├── kustomization.yaml
├── namespace.yaml
├── deployment.yaml
├── service.yaml
├── ingress.yaml
├── pvc.yaml
├── backup-cronjob.yaml
└── README.md
```

## Implementation Details

### 1. Namespace

Dedicated `actual-budget` namespace for isolation.

### 2. PersistentVolumeClaim

5Gi storage for budget data persistence.

### 3. Deployment

- Image: `actualbudget/actual-server:latest`
- Port: 5006
- Strategy: Recreate (required for single-writer PVC)
- Resources: 128Mi-512Mi memory, 100m-500m CPU
- Health checks: HTTP probes on `/`

### 4. Service

ClusterIP service mapping port 80 → 5006.

### 5. Traefik IngressRoute

- Host: `finance.blackk.dev`
- EntryPoint: websecure
- TLS: letsencrypt cert resolver
- Middleware: secure-headers

### 6. Backup CronJob

- Schedule: Daily at 3 AM
- Retention: 30 days
- Location: `/var/backups/actual-budget/` on host
- Format: tar.gz archives

## Post-Deployment Steps

1. Access https://finance.blackk.dev
2. Create new budget file
3. Set server password
4. Configure SimpleFIN:
   - Sign up at https://simplefin.org/
   - Link bank accounts
   - Get access URL
   - Add to Actual Budget Settings → Linked Accounts

## Verification Checklist

- [ ] Namespace created: `kubectl get ns actual-budget`
- [ ] PVC bound: `kubectl get pvc -n actual-budget`
- [ ] Pod running: `kubectl get pods -n actual-budget`
- [ ] Service exists: `kubectl get svc -n actual-budget`
- [ ] Ingress configured: `kubectl get ingressroute -n actual-budget`
- [ ] Web UI accessible via domain
- [ ] Backup CronJob scheduled: `kubectl get cronjob -n actual-budget`
- [ ] SimpleFIN syncs accounts

## Deployment Commands

```bash
cd ~/dev/home/home-config

# Apply the manifests
kubectl apply -k apps/actual-budget/

# Verify deployment
kubectl get all -n actual-budget

# Check pod logs
kubectl logs -n actual-budget -l app=actual-budget

# Port forward for initial setup (if needed)
kubectl port-forward -n actual-budget svc/actual-budget 5006:80
```

## Alternatives Considered

| Tool | Pros | Cons | Decision |
|------|------|------|----------|
| Firefly III | Feature-rich, popular | Complex, heavier | Not chosen |
| Actual Budget | Modern UI, TypeScript, easy SimpleFIN | Newer | **Chosen** |
| Maybe Finance | Very modern | Still maturing | Not chosen |

Actual Budget was chosen due to:
- Clean, modern UI
- TypeScript codebase (familiar)
- Easy SimpleFIN integration
- Lightweight resource usage
- Active development

## Resources

- [Actual Budget Docs](https://actualbudget.org/docs/)
- [SimpleFIN](https://simplefin.org/)
- [GitHub - Actual](https://github.com/actualbudget/actual)
