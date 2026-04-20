# Neptune

Reverse-proxy for the Neptune printer's built-in Fluidd and Moonraker services.

The printer itself stays on the LAN at `192.168.1.54`, but Traefik exposes the Fluidd UI internally at `https://neptune.kblab.me` and the cluster can reach Moonraker through the `neptune.neptune.svc.cluster.local` service.

## Files

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, service/endpoints, and ingress |
| `namespace.yaml` | Creates the `neptune` namespace |
| `service.yaml` | ClusterIP service plus manual endpoints for the printer's LAN IP |
| `ingress.yaml` | TLS ingress for `neptune.kblab.me`, restricted to local-network source ranges |

## Verify

```bash
kubectl --context home-k3s -n neptune get svc,endpoints,ingress
kubectl --context home-k3s -n neptune describe ingress neptune
curl -kI https://neptune.kblab.me/
curl http://neptune.neptune.svc.cluster.local:7125/printer/info
```
