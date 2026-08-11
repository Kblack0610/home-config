# App Lifecycle Runbook

Checklists for adding and removing apps from the home-k3s cluster.
See [gitops.md](./gitops.md) for Flux workflow details.

## Adding an App

- [ ] **Create manifest directory** — `apps/<name>/` with `namespace.yaml`, deployment or HelmRelease, service, `kustomization.yaml`
- [ ] **Register in Flux** — add entry to `apps/kustomization.yaml` (alphabetical order)
- [ ] **Pin the image tag to a version** — never `:latest`, `:stable`, `:release` or any other floating tag. A floating tag does not keep an app current (nothing repulls until the pod restarts); it only makes the deployed version unknowable and unrollbackable, and it hides the app from Renovate, which cannot bump a tag that encodes no version. If upstream publishes no version tag, pin the digest instead (`image:tag@sha256:...`, as `apps/nas/deployment.yaml` and `apps/vaultwarden/` do). Renovate then keeps it current — see `apps/renovate/README.md`.
- [ ] **All resources go in `apps/`** — Flux only watches `./apps` (defined in `clusters/home-k3s/apps.yaml`). The `infrastructure/` directory is NOT reconciled by Flux; anything placed there requires manual `kubectl apply`.
- [ ] **Secrets** — use SOPS encryption. File must match `.*secret.*\.yaml$`. Encrypt with `sops -e -i <file>`. Flux decrypts automatically via the `sops-age` secret.
- [ ] **Storage** (see `docs/architecture.md` Storage section for the full model):
  - **Default**: `PersistentVolumeClaim` with `storageClassName: local-path`. Reference patterns: `apps/forgejo/pvc.yaml` (20Gi), `apps/qdrant/pvc.yaml` (5Gi), `apps/karakeep/pvc.yaml` (multi-PVC). No `nodeSelector` needed — the local-path provisioner pins the PV to whichever node first schedules the pod.
  - **Use `hostPath: /mnt/nas/private/<app>` only** when the user needs to reach the data over SMB from a non-cluster client (e.g., Finder browsing the photo library). This couples the pod to `asus-laptop` permanently. Examples: `apps/immich/` upload dir, `apps/gatus/` data.
  - **Use `hostPath: /etc/localtime`, `/dev/...`, etc.** for host-resource passthrough — never for app state.
  - **Don't introduce NFS/Longhorn/Ceph.** No app uses them; the convention is local-path-per-node.
  - **Backups**: daily CronJob mounts the data PVC `readOnly`, tars to `/var/backups/<app>` hostPath, keeps last 30. Optional second hop: `smbclient` push to the in-cluster `nas` Samba pod (`apps/immich/backup-cronjob.yaml:34-46` is the canonical two-hop example).
- [ ] **Ingress** (if exposing via Traefik):
  - Use `*.kblab.me` domain
  - Annotations: `kubernetes.io/ingress.class: traefik`, `cert-manager.io/cluster-issuer: letsencrypt-dns`
  - For public services: add CrowdSec bouncer middleware annotation (`crowdsec-bouncer@kubernetescrd`)
  - DNS: internal `*.kblab.me` is handled by the AdGuard wildcard rewrite (no action needed). For public services: add a Cloudflare DNS record.
- [ ] **Status monitoring** — for ingress endpoints, run `./scripts/gen-gatus-ingress-checks.py` (auto-updates between the `BEGIN_GENERATED_INGRESS` / `END_GENERATED_INGRESS` markers in `apps/gatus/configmap.yaml`). Opt out per Ingress with `gatus.kblab.me/monitor: "false"`. For non-ingress endpoints (TCP, DNS, internal services), add manually above the generated block.
- [ ] **Launcher tile** — run `./scripts/gen-ha-launcher.py` to add the app to the HA Launcher dashboard at `kblab.me` (auto-groups by namespace, falls back to `mdi:application` icon). Opt out per Ingress with `homepage.kblab.me/launcher: "false"`. Extend the namespace→group and host→icon maps in the script when a new namespace lands.
- [ ] **Infrastructure inventory** — update `infrastructure.md` (namespaces, service directory, helm releases, domains)
- [ ] **Documentation** — create `apps/<name>/README.md`, add to `docs/README.md` index
- [ ] **Commit, push, reconcile** — `flux reconcile kustomization apps --with-source`
- [ ] **Verify** — check pod status, service reachability, Gatus status page

## Removing an App

- [ ] Remove directory from `apps/kustomization.yaml`
- [ ] Delete the `apps/<name>/` directory
- [ ] Regenerate status checks — run `./scripts/gen-gatus-ingress-checks.py` (drops the removed ingresses). Delete any non-ingress entries manually.
- [ ] Regenerate launcher — run `./scripts/gen-ha-launcher.py` (drops the removed tiles)
- [ ] Update `infrastructure.md`
- [ ] Remove from `docs/README.md`
- [ ] Commit, push, reconcile — Flux prune will delete cluster resources
- [ ] Verify namespace is gone: `kubectl get ns <name>`

## Common Pitfalls

- **Infrastructure path trap** — `infrastructure/` is not in Flux's reconciliation path. All Flux-managed resources must be under `apps/`.
- **Cross-namespace middleware** — reference format is `<namespace>-<name>@kubernetescrd`.
- **HelmRelease** — requires Flux helm-controller (part of standard bootstrap). HelmRepository + HelmRelease go in the app's directory.
- **Secret encryption** — must encrypt before committing. SOPS auto-applies via `.sops.yaml` creation rules.
