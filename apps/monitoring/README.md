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
| `prometheus-rules-node-health.yaml` | `NodeDown` / `NodeExporterAbsent` — pages when any monitored machine stops responding |
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

## Alerting → ntfy

Alertmanager routes every firing alert (except the `Watchdog` heartbeat) to the
in-cluster **ntfy** as a webhook — config is inline under `alertmanager.config`
in `helm-values.yaml`. Before this, Alertmanager had no receiver and alerts went
nowhere (the thinkcentre power outage paged no one).

- **Topic:** `homelab-alerts`
- **Subscribe (phone/desktop):** open `https://ntfy.kblab.me/homelab-alerts` in
  the ntfy app, or `ntfy subscribe homelab-alerts` (LAN/Tailscale only).
- **Criticals** (e.g. `NodeDown`) remind hourly until resolved; warnings every 12h.
- The receiver is plain webhook → ntfy renders the Alertmanager JSON payload.
  A formatting relay for prettier pages is a noted follow-up.

> ⚠️ `helm-values.yaml` is **plain-Helm, not Flux**. Alerting changes only take
> effect after the `helm upgrade` in that file's header — committing alone does
> nothing.

## Notes

- This directory does not install the full Prometheus stack by itself; it layers repo-managed resources on top of the monitoring namespace.
- External node scrape definitions are where macOS and other standalone hosts are connected into Prometheus.
- `prometheus-rules-node-health.yaml` is Flux-managed (reconciles automatically);
  the **routing** to ntfy lives in the plain-Helm `helm-values.yaml` and needs a
  manual `helm upgrade`. Two layers — don't forget the second.

## Related Docs

- [../../infrastructure.md](../../infrastructure.md)
- [../../docs/mac-machines.md](../../docs/mac-machines.md)
- [../../docs/gitops.md](../../docs/gitops.md)
