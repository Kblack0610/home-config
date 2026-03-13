# Headscale

Self-hosted Tailscale control plane running in the `headscale` namespace on `home-k3s`.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the full Headscale stack |
| `configmap.yaml` | Headscale server config, DNS settings, DERP config, and base domain |
| `deployment.yaml` | Headscale server and optional Headscale UI sidecar |
| `service.yaml` | Cluster services for the API/UI |
| `ingress.yaml` | Public ingress for `headscale.kblab.me` and `headscale-ui.kblab.me` |
| `pvc.yaml` | Persistent storage for the SQLite database and keys |
| `secret.sops.yaml` | Encrypted application secrets for cluster use |
| `tailscale-secret.yaml` | Additional secret material used by the subnet router |
| `subnet-router.yaml` | In-cluster subnet router for LAN access from the tailnet |

## Configuration and Secrets

- Core runtime config lives in `configmap.yaml`.
- Persistent data is stored in the `headscale-data` PVC.
- The deployment writes the noise private key into the data volume during init from `headscale-secrets`.
- Local-only testing values belong in `secret.local.yaml`; cluster-safe committed secrets belong in `secret.sops.yaml`.

## Deploy

```bash
kubectl apply -k apps/headscale
```

For normal operations, prefer the Flux workflow from [../../docs/gitops.md](../../docs/gitops.md).

## Verify

```bash
kubectl -n headscale get all
kubectl -n headscale logs deployment/headscale --tail=100
kubectl -n headscale get ingress
```

Expected ingress hosts:

- `headscale.kblab.me`
- `headscale-ui.kblab.me`

## Related Docs

- [../../docs/headscale-setup.md](../../docs/headscale-setup.md)
- [../../docs/gitops.md](../../docs/gitops.md)
- [../../infrastructure.md](../../infrastructure.md)
