# Traefik

Shared Traefik-related manifests for the homelab ingress layer.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles the resources in this directory |
| `middlewares.yaml` | Shared Traefik middleware definitions |
| `dashboard-ingress.yaml` | Ingress for the Traefik dashboard |
| `INGRESS-TEMPLATE.yaml` | Reference template for creating new Traefik ingresses |

## Apply

```bash
kubectl apply -k infrastructure/traefik
```

Use the Flux workflow for normal changes.

## Verify

```bash
kubectl -n kube-system get ingress
kubectl -n kube-system get middleware
```

## Notes

- This directory contains shared ingress-layer helpers, not the Traefik Helm installation itself.
- Prefer reusing the middleware definitions here before introducing service-local variants.

## Related Docs

- [../../docs/gitops.md](../../docs/gitops.md)
- [../../infrastructure.md](../../infrastructure.md)
