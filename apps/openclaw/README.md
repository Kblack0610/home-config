# OpenClaw

OpenClaw gateway deployment in the `openclaw` namespace, exposed internally through Traefik at `openclaw.kblab.me`.

The gateway keeps its durable state on a PVC and uses the same MLX-backed model endpoints as the local workstation setup on the Mac Studio (`192.168.1.4`).

The ingress is restricted to RFC1918 and loopback source ranges via the shared `monitoring/local-network-only` Traefik middleware.

## Files

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, config, secret, storage, app, service, and ingress |
| `configmap.yaml` | Gateway config and seeded workspace `AGENTS.md` |
| `secret.yaml` | SOPS-encrypted gateway token for Control UI auth |
| `pvc.yaml` | Persistent volume for OpenClaw state |
| `deployment.yaml` | OpenClaw gateway workload |
| `service.yaml` | ClusterIP service on port `18789` |
| `ingress.yaml` | TLS ingress for `openclaw.kblab.me` |

## Verify

```bash
kubectl --context home-k3s -n openclaw get all
kubectl --context home-k3s -n openclaw logs deployment/openclaw --tail=100
kubectl --context home-k3s -n openclaw get ingress
```

Expected hostname:

- `openclaw.kblab.me`

Retrieve the Control UI token:

```bash
kubectl --context home-k3s -n openclaw get secret openclaw-secrets -o jsonpath='{.data.OPENCLAW_GATEWAY_TOKEN}' | base64 -d && echo
```
