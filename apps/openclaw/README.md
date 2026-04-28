# OpenClaw

OpenClaw gateway deployment in the `openclaw` namespace, exposed internally through Traefik at `openclaw.kblab.me`.

The gateway keeps its durable state on a PVC and routes all inference through the in-cluster LiteLLM gateway (`litellm.ai-gateway.svc.cluster.local:4000`), which fronts the MLX backends on the Mac Studio (`192.168.1.4`).

The ingress is restricted to RFC1918 and loopback source ranges via the shared `monitoring/local-network-only` Traefik middleware.

## Files

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the namespace, config, secret, storage, app, service, and ingress |
| `configmap.yaml` | Gateway config and seeded workspace `AGENTS.md` |
| `secret.yaml` | SOPS-encrypted gateway token for Control UI auth |
| `registry-secret.yaml` | SOPS-encrypted pull credentials for `git.kblab.me` (overlay image) |
| `pvc.yaml` | Persistent volume for OpenClaw state |
| `deployment.yaml` | OpenClaw gateway workload |
| `service.yaml` | ClusterIP service on port `18789` |
| `ingress.yaml` | TLS ingress for `openclaw.kblab.me` |
| `Dockerfile` | Custom image overlay (`gh`, `jq`, `rg`, `tmux`, `opencode`) on top of `ghcr.io/openclaw/openclaw:latest` to unlock bundled skills. Built by `.forgejo/workflows/openclaw-image.yaml` and pushed to `git.kblab.me/kblack0610/openclaw:latest`. |

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
