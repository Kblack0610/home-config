# Monitoring

Monitoring overlays and custom resources for the `monitoring` namespace on `home-k3s`.

This directory augments the Prometheus stack with ingress, dashboards, alert rules, and scrape targets for non-cluster machines.

## What This Directory Contains

| Path | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles all monitoring add-ons in this directory |
| `ingress-grafana.yaml` | Traefik ingress for `grafana.kblab.me` |
| `ingress-prometheus.yaml` | Traefik ingress for `prometheus.kblab.me` |
| `ingress-alertmanager.yaml` | Traefik ingress for `alertmanager.kblab.me` |
| `middleware-ip-allowlist.yaml` | Shared middleware for ingress restrictions |
| `prometheus-rules-app-health.yaml` | App-specific alerting rules |
| `external-mac-nodes.yaml` | Scrape definitions for Apple Silicon hosts |
| `external-standalone-nodes.yaml` | Scrape definitions for non-Kubernetes hosts |
| `external-iot-nodes.yaml` | Scrape definitions for IoT devices |
| `dashboards/` | Grafana dashboards committed with the repo |

## Deploy

```bash
kubectl apply -k apps/monitoring
```

For regular changes, commit the manifests and let Flux reconcile them.

## Verify

```bash
kubectl -n monitoring get ingress
kubectl -n monitoring get prometheusrules
kubectl -n monitoring get servicemonitors
```

Expected ingress hosts:

- `grafana.kblab.me`
- `prometheus.kblab.me`
- `alertmanager.kblab.me`

## Notes

- This directory does not install the full Prometheus stack by itself; it layers repo-managed resources on top of the monitoring namespace.
- External node scrape definitions are where macOS and other standalone hosts are connected into Prometheus.

## Related Docs

- [../../infrastructure.md](../../infrastructure.md)
- [../../docs/mac-machines.md](../../docs/mac-machines.md)
- [../../docs/gitops.md](../../docs/gitops.md)
