# Home Assistant on K3s

Home Assistant deployment for the home K3s cluster with cluster monitoring capabilities.

## Access

| Method | URL |
|--------|-----|
| Local (via AdGuard DNS) | `http://homeassistant.home.lan` |
| Port Forward | `kubectl port-forward -n home-assistant svc/home-assistant 8123:8123` then `http://localhost:8123` |

### DNS Setup (Required for local access)

Add DNS rewrite in AdGuard Home (Pi 3) at **Filters → DNS Rewrites**:
- Domain: `homeassistant.home.lan`
- Answer: `192.168.1.124`

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Local Access                                                     │
│   Browser → AdGuard DNS → Traefik (192.168.1.124) → HA Pod      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Cluster Monitoring                                               │
│   HA Pod → Netdata API (each node:19999) → REST Sensors         │
└─────────────────────────────────────────────────────────────────┘
```

## Features

### Cluster Monitoring (via Netdata)

| Metric | Description |
|--------|-------------|
| Node CPU | CPU usage per node (Nodes 1-5) |
| Node RAM | Memory usage per node |
| Node Disk | Disk usage per node |
| Cluster Average | Average CPU/RAM across all nodes |
| Node Health | Count of online nodes |
| K3s Server | Control plane resource usage |
| Containerd | Container runtime metrics |
| Pod Metrics | CPU for key pods (PlaceMyParents, Cloudflared, Traefik, etc.) |

### Smart Home Capabilities

| Category | Features |
|----------|----------|
| **Lighting** | Zigbee/Z-Wave lights, schedules, motion triggers |
| **Climate** | Thermostat control, temperature automations |
| **Presence** | Phone tracking, door sensors, geofencing |
| **Voice** | Alexa, Google Home integration |
| **Cameras** | Frigate NVR integration (configured) |
| **Notifications** | Push alerts to phone for any event |
| **Energy** | Smart plug monitoring, solar tracking |
| **Media** | TV, speaker, streaming device control |
| **Security** | Door/window sensors, locks, alarms |

### Homelab-Specific Use Cases

| Use Case | Description |
|----------|-------------|
| Cluster alerts | Notification when node offline >5 min |
| Dashboard display | Wall tablet showing cluster status |
| Wake-on-LAN | Start servers remotely |
| UPS monitoring | Graceful shutdown on power loss |
| Backup alerts | Notify on backup completion/failure |
| Network status | Monitor router/switch health |

## Deployment

```bash
# Deploy all resources
kubectl apply -k apps/home-assistant/

# Or apply individually
kubectl apply -f apps/home-assistant/namespace.yaml
kubectl apply -f apps/home-assistant/pvc.yaml
kubectl apply -f apps/home-assistant/configmap-cluster.yaml
kubectl apply -f apps/home-assistant/deployment.yaml
kubectl apply -f apps/home-assistant/service.yaml
kubectl apply -f apps/home-assistant/ingressroute.yaml
```

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | Creates `home-assistant` namespace |
| `pvc.yaml` | Persistent volume for HA config |
| `deployment.yaml` | Main HA deployment with init container |
| `service.yaml` | ClusterIP service on port 8123 |
| `ingressroute.yaml` | Traefik IngressRoute for local access |
| `configmap-cluster.yaml` | Cluster monitoring sensor configurations |

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

### Adding Cluster Monitoring Sensors

The `configmap-cluster.yaml` contains REST sensors that pull from Netdata. To include them in HA:

```yaml
# In configuration.yaml
sensor: !include sensors_cluster.yaml
```

### Pod Chart Names in Netdata

Pod metrics use Netdata chart names that include pod IDs (e.g., `cgroup_k8s_pod_abc123`). These change when pods restart. Use the helper script to find current names:

```bash
./scripts/update-pod-sensors.sh
```

## Troubleshooting

### Can't access via homeassistant.home.lan

1. Check DNS rewrite exists in AdGuard
2. Verify your device uses AdGuard as DNS
3. Test with: `nslookup homeassistant.home.lan`

### 400 Bad Request

Add `trusted_proxies` to HA configuration (see above).

### Check pod status

```bash
kubectl get pods -n home-assistant
kubectl logs -n home-assistant -l app.kubernetes.io/name=home-assistant
```

### Restart Home Assistant

```bash
kubectl rollout restart deployment home-assistant -n home-assistant
```

## Related Services

| Service | Purpose |
|---------|---------|
| Netdata | Metrics source (DaemonSet on all nodes) |
| Traefik | Ingress controller |
| AdGuard Home | DNS server (on Pi 3) |
| Frigate | NVR for cameras |
