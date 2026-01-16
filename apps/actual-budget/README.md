# Actual Budget

Self-hosted personal finance application with automated bank syncing.

## Overview

[Actual Budget](https://actualbudget.org/) is a local-first personal finance tool with sync capabilities. This deployment uses SimpleFIN for automated bank account synchronization.

## Components

| Resource | Purpose |
|----------|---------|
| Deployment | Actual Budget server container |
| Service | ClusterIP service on port 80 |
| IngressRoute | Traefik ingress at `finance.blackk.dev` |
| PVC | 5Gi persistent storage for budget data |
| CronJob | Daily backups at 3 AM (30-day retention) |

## Deployment

```bash
# Apply all manifests
kubectl apply -k apps/actual-budget/

# Verify deployment
kubectl get all -n actual-budget

# Check logs
kubectl logs -n actual-budget -l app=actual-budget

# Port forward for initial setup (if ingress not ready)
kubectl port-forward -n actual-budget svc/actual-budget 5006:80
```

## Access

- **URL**: https://finance.blackk.dev
- **Port Forward**: http://localhost:5006 (if needed during setup)

## Initial Setup

1. Access the web UI
2. Create a new budget file (or upload existing)
3. Set a password for your server
4. Configure SimpleFIN for bank sync (see below)

## SimpleFIN Bank Sync Setup

SimpleFIN provides automated bank account syncing for ~$1.50/month.

1. **Sign up**: https://simplefin.org/
2. **Link your banks** via SimpleFIN's secure portal
3. **Get access URL** from SimpleFIN dashboard
4. **Configure in Actual**:
   - Go to Settings → Linked Accounts
   - Click "Link account with SimpleFIN"
   - Paste your SimpleFIN access URL
   - Select accounts to sync

## Backups

Backups run daily at 3 AM and are stored at `/var/backups/actual-budget/` on the host.

```bash
# List backups
ls -la /var/backups/actual-budget/

# Manual backup
kubectl create job --from=cronjob/actual-budget-backup manual-backup -n actual-budget

# Restore from backup (stop pod first)
kubectl scale deployment actual-budget -n actual-budget --replicas=0
tar -xzf /var/backups/actual-budget/actual-YYYYMMDD-HHMMSS.tar.gz -C /path/to/pvc
kubectl scale deployment actual-budget -n actual-budget --replicas=1
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ACTUAL_UPLOAD_FILE_SYNC_SIZE_LIMIT_MB` | 20 | Max upload size for budget files |

### Resources

- **Memory**: 128Mi request / 512Mi limit
- **CPU**: 100m request / 500m limit

## Troubleshooting

### Pod not starting
```bash
kubectl describe pod -n actual-budget -l app=actual-budget
kubectl logs -n actual-budget -l app=actual-budget --previous
```

### PVC not bound
```bash
kubectl get pvc -n actual-budget
kubectl describe pvc actual-budget-data -n actual-budget
```

### Ingress not working
```bash
kubectl get ingressroute -n actual-budget
kubectl logs -n traefik -l app.kubernetes.io/name=traefik
```

## Mobile Apps

Actual Budget has companion mobile apps that sync with your self-hosted server:
- iOS: App Store
- Android: Play Store

Configure them with your server URL: `https://finance.blackk.dev`

## Resources

- [Actual Budget Docs](https://actualbudget.org/docs/)
- [SimpleFIN](https://simplefin.org/)
- [GitHub - Actual](https://github.com/actualbudget/actual)
