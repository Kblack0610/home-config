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
| `Dockerfile` | Custom image overlay (`gh`, `jq`, `rg`, `tmux`, `opencode`) on top of `ghcr.io/openclaw/openclaw:latest` to unlock bundled skills. **Built locally and pushed manually** — see the rebuild block below. The Forgejo Actions workflow at `.forgejo/workflows/openclaw-image.yaml` is checked in but currently broken (dind-on-bridge NAT hangs apt-get); it's kept for future fix. |

## Rebuild the overlay

After upstream openclaw bumps a tag, or after editing the Dockerfile:

```bash
docker login git.kblab.me -u kblack0610
DOCKER_BUILDKIT=0 docker build apps/openclaw \
  -t git.kblab.me/kblack0610/openclaw:latest \
  -t git.kblab.me/kblack0610/openclaw:$(date +%Y%m%d)
docker push git.kblab.me/kblack0610/openclaw:latest
kubectl -n openclaw delete pod -l app.kubernetes.io/name=openclaw
kubectl -n openclaw exec deploy/openclaw -- openclaw skills check | head -15
```

`DOCKER_BUILDKIT=0` is required — the modern buildx-driven build doesn't run RUN containers on host network and apt-get hangs.

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
