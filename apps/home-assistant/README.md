# Home Assistant on K3s

Home Assistant deployment for the home K3s cluster with Git-managed dashboards and homelab monitoring.

## Access

| Method | URL |
|--------|-----|
| Canonical | `https://hass.kblab.me` |
| Port Forward | `kubectl port-forward -n home-assistant svc/home-assistant 8123:8123` then `http://localhost:8123` |

### DNS Setup

Add DNS rewrite in AdGuard Home (Pi 3) at **Filters → DNS Rewrites**:
- Domain: `hass.kblab.me`
- Answer: `192.168.1.124`

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Local Access                                                     │
│   Browser → AdGuard DNS → Traefik (192.168.1.124) → HA Pod      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Cluster Monitoring                                               │
│   HA Pod → Prometheus API (monitoring.svc:9090) → REST Sensors   │
└─────────────────────────────────────────────────────────────────┘
```

## Layout

Repo-managed Home Assistant files live under `apps/home-assistant/config/`:

- `configuration.yaml`: base HA config, proxies, Lovelace mode
- `packages/`: Prometheus-backed REST sensors and derived template sensors
- `ui-lovelace.yaml`: default dashboard definition
- `dashboards/`: retained views (`Overview`, `Ops`, `Admin`)
- `automations.yaml`, `scripts.yaml`, `scenes.yaml`: checked-in defaults so a fresh PVC can boot cleanly

These files are bundled into a generated ConfigMap and synced into the HA PVC on each rollout. Runtime state such as `.storage` stays on the PVC and is not repo-managed.

## Features

### Homelab Monitoring

| Use Case | Description |
|----------|-------------|
| Cluster status | CPU, RAM, and disk sensors for nodes 1-5 |
| Service health | Home Assistant, Traefik, and LiteLLM container metrics |
| Mac monitoring | Mac Studio and Mac Mini node exporter metrics |
| AI endpoints | MLX model endpoints and Ollama fallback model counts |
| Dashboard cleanup | Retains only `Overview`, `Ops`, and `Admin` views |

## Deployment

```bash
# Render locally
kubectl kustomize apps/home-assistant

# Deploy all resources directly
kubectl apply -k apps/home-assistant/

# Render the Flux entrypoint used by the live cluster
kubectl kustomize infra/flux/apps/prod
```

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | Creates `home-assistant` namespace |
| `pvc.yaml` | Persistent volume for HA config |
| `deployment.yaml` | Main HA deployment with managed config sync init container |
| `service.yaml` | ClusterIP service on port 8123 |
| `ingressroute.yaml` | Traefik IngressRoute for local access |
| `kustomization.yaml` | Generates the managed config ConfigMap from checked-in files |

## Configuration

### Trusted Proxies

Home Assistant requires `trusted_proxies` config to work behind Traefik:

```yaml
# In /config/configuration.yaml (inside the pod)
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 10.42.0.0/16    # K8s pod network
    - 10.43.0.0/16    # K8s service network
    - 192.168.1.0/24  # Local network
```

### Managed Config Sync

The deployment copies repo-managed files into `/config` on every rollout:

```yaml
# Base config
/config/configuration.yaml

# Package files
/config/packages/*.yaml

# Dashboard files
/config/ui-lovelace.yaml
/config/dashboards/*.yaml
```

## Troubleshooting

### Can't access via hass.kblab.me

1. Check DNS rewrite exists in AdGuard
2. Verify your device uses AdGuard as DNS
3. Test with: `nslookup hass.kblab.me`

### 400 Bad Request

Add `trusted_proxies` to HA configuration (see above).

### Check pod status

```bash
kubectl get pods -n home-assistant
kubectl logs -n home-assistant -l app.kubernetes.io/name=home-assistant
kubectl logs -n home-assistant deploy/home-assistant -c sync-managed-config
```

### Restart Home Assistant

```bash
kubectl rollout restart deployment home-assistant -n home-assistant
```

## Related Services

| Service | Purpose |
|---------|---------|
| Prometheus | Metrics source (kube-prometheus-stack) |
| Traefik | Ingress controller |
| AdGuard Home | DNS server (on Pi 3) |
| Frigate | NVR for cameras |
