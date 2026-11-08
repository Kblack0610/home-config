# Traefik

Traefik is bundled with k3s. Shared middleware is managed by Flux in [apps/traefik](../../apps/traefik/), included in the `apps` Kustomization. This directory holds the ingress template and an optional dashboard example.

## Shared middleware

`redirect-https` in `kube-system` redirects HTTP requests to HTTPS permanently. An Ingress opts in with:

```yaml
traefik.ingress.kubernetes.io/router.middlewares: kube-system-redirect-https@kubernetescrd
```

Define app-specific headers or rate limits in the app's Flux directory. There are no shared `secure-headers`, `rate-limit` or `secure-chain` middlewares.

## Changes and verification

Edit [apps/traefik/middlewares.yaml](../../apps/traefik/middlewares.yaml), commit and push to Forgejo. Flux reconciles it through `apps`; follow [the GitOps workflow](../../docs/gitops.md).

```bash
kubectl kustomize apps/traefik
kubectl --context home-k3s -n kube-system get middleware redirect-https
kubectl --context home-k3s -n flux-system get kustomization apps
```

| Symptom | Check and fix |
|---------|---------------|
| Ingress reports a missing `kube-system-redirect-https` middleware | Check the Flux `apps` status and its inventory for `kube-system_redirect-https_traefik.io_Middleware`. Confirm `apps/kustomization.yaml` includes `traefik`; fix the git source and reconcile through Flux. |

## Examples

- [INGRESS-TEMPLATE.yaml](INGRESS-TEMPLATE.yaml): copy into an app's Flux directory and replace the placeholders.
- [dashboard-ingress.yaml](dashboard-ingress.yaml): optional dashboard example, excluded from Flux. Enable only through a reviewed manifest change.
- [Public ingress and DNS](../../docs/public-sites.md)
