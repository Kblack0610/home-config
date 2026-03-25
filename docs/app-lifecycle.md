# App Lifecycle Runbook

Checklists for adding and removing apps from the home-k3s cluster.
See [gitops.md](./gitops.md) for Flux workflow details.

## Adding an App

- [ ] **Create manifest directory** — `apps/<name>/` with `namespace.yaml`, deployment or HelmRelease, service, `kustomization.yaml`
- [ ] **Register in Flux** — add entry to `apps/kustomization.yaml` (alphabetical order)
- [ ] **All resources go in `apps/`** — Flux only watches `./apps` (defined in `clusters/home-k3s/apps.yaml`). The `infrastructure/` directory is NOT reconciled by Flux; anything placed there requires manual `kubectl apply`.
- [ ] **Secrets** — use SOPS encryption. File must match `.*secret.*\.yaml$`. Encrypt with `sops -e -i <file>`. Flux decrypts automatically via the `sops-age` secret.
- [ ] **Ingress** (if exposing via Traefik):
  - Use `*.kblab.me` domain
  - Annotations: `kubernetes.io/ingress.class: traefik`, `cert-manager.io/cluster-issuer: letsencrypt-dns`
  - For public services: add CrowdSec bouncer middleware annotation (`crowdsec-bouncer@kubernetescrd`)
  - DNS: internal `*.kblab.me` is handled by the AdGuard wildcard rewrite (no action needed). For public services: add a Cloudflare DNS record.
- [ ] **Status monitoring** — add endpoint(s) to `apps/gatus/configmap.yaml`
- [ ] **Infrastructure inventory** — update `infrastructure.md` (namespaces, service directory, helm releases, domains)
- [ ] **Documentation** — create `apps/<name>/README.md`, add to `docs/README.md` index
- [ ] **Commit, push, reconcile** — `flux reconcile kustomization apps --with-source`
- [ ] **Verify** — check pod status, service reachability, Gatus status page

## Removing an App

- [ ] Remove directory from `apps/kustomization.yaml`
- [ ] Delete the `apps/<name>/` directory
- [ ] Remove Gatus endpoints from `apps/gatus/configmap.yaml`
- [ ] Update `infrastructure.md`
- [ ] Remove from `docs/README.md`
- [ ] Commit, push, reconcile — Flux prune will delete cluster resources
- [ ] Verify namespace is gone: `kubectl get ns <name>`

## Common Pitfalls

- **Infrastructure path trap** — `infrastructure/` is not in Flux's reconciliation path. All Flux-managed resources must be under `apps/`.
- **Cross-namespace middleware** — reference format is `<namespace>-<name>@kubernetescrd`.
- **HelmRelease** — requires Flux helm-controller (part of standard bootstrap). HelmRepository + HelmRelease go in the app's directory.
- **Secret encryption** — must encrypt before committing. SOPS auto-applies via `.sops.yaml` creation rules.
