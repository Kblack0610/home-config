# Apps Excluded from Flux

Most subdirectories under `apps/` are Kubernetes manifests listed in `apps/kustomization.yaml` and reconciled by Flux. A few are NOT, intentionally — they hold Docker Compose stacks, embedded firmware, or other artifacts that don't belong in the Kubernetes reconciliation loop.

This is the canonical list. If you add a non-Flux directory under `apps/`, add a row here in the same PR.

## Excluded — runs elsewhere

| Directory | Type | Where it runs | How it deploys | Notes |
|-----------|------|---------------|----------------|-------|
| `apps/actual-budget-tools/` | Go service, Docker Compose | workstation or sidecar host | `docker compose up -d` (see [`apps/actual-budget-tools/README.md`](../apps/actual-budget-tools/README.md)) | Companion to the in-cluster `actual-budget` Flux app; reads its DB to detect recurring subscriptions. |
| `apps/esp32-firmware/` | Embedded firmware (Rust/C) | ESP32 microcontrollers | Manual flash from this dir | Not a runtime service; it's the firmware source tree. |
| `apps/frigate/` | Docker Compose | standalone Pi NVR (separate from k3s) | `docker compose up -d` on the NVR Pi (see [`apps/frigate/README.md`](../apps/frigate/README.md)) | Compose-only because frigate's GPU access on Pi NVR predates the home-k3s cluster. |
| `apps/game-servers/` | Docker Compose | x86 CachyOS LAN hosts (BNB multiplayer) | `apps/game-servers/deploy.sh` + `docker compose up -d` (see [`apps/game-servers/README.md`](../apps/game-servers/README.md)) | Compose because dedicated game servers need direct host networking + bind-mounts that the cluster's local-path PVCs make awkward. |
| `apps/iot-fleet/` | Rust source tree (Cargo workspace) | not a service — code only | `cargo build` from this dir | Crates and agents for an embedded-fleet project; lives under `apps/` historically. Candidate to move to `~/.lab/` or a sibling repo. |
| `apps/pi3-adguard-home/` | Docker Compose | standalone Pi3 (separate from k3s) | bootstrap via `apps/pi3-adguard-home/flash-pi.sh` + `setup.sh` (see [`apps/pi3-adguard-home/README.md`](../apps/pi3-adguard-home/README.md)) | Compose because AdGuard sits OUTSIDE the cluster network namespace — it owns DNS for the LAN (`*.kblab.me` rewrites) and must survive cluster outages. |

## Ready for Flux — activation deferred

| Directory | Why deferred | Activation step |
|-----------|--------------|-----------------|
| `apps/cal/` | Requires manual prereqs before Flux can apply cleanly: DNS record for `cal.kennethblack.me`, generated secret material (`NEXTAUTH_SECRET`, `CALENDSO_ENCRYPTION_KEY`, Gmail app password), database role on the shared Postgres in `databases` ns | Follow [`docs/runbooks/cal-com-setup.md`](runbooks/cal-com-setup.md). Final step is adding `- cal` to `apps/kustomization.yaml`. |

## Runtime-managed secrets (not in git, by design)

Some cluster Secrets are deliberately NOT under Flux because they're populated and mutated by the runtime — committing them to git would either expose sensitive state (auth keys, account-keys) or get clobbered on every reconcile when the controller regenerates them.

| Secret | Namespace | Why excluded |
|--------|-----------|--------------|
| `tailscale-state` | `headscale` | Tailnet machine keys, persisted by the tailscale CLI in the pod. Mutates on every node-rotate / re-auth. Backed up out-of-band via `apps/nas/sops-key-backup` cronjob if needed for DR. |
| `letsencrypt-dns-account-key` | `cert-manager` | ACME account private key, generated once by cert-manager on first issuance. Persists across restarts via the secret; recreating it triggers re-acceptance of LetsEncrypt terms but is otherwise harmless. Not sensitive enough to be in git, not stable enough to want to be in git. |
| `cert-manager-webhook-ca` | `cert-manager` | Internal CA for the admission webhook, rotated by cert-manager itself. Auto-managed. |
| `*-tls` (cert-manager-issued) | various | Per-Ingress TLS certs auto-issued from `letsencrypt-dns` ClusterIssuer. Filenames like `forgejo-tls`, `git-kennethblack-me-tls`, `placemyparents-preview-tls`. Owned by the Certificate CRD; never put these in git. |
| `sh.helm.release.v1.*` | helm-installed ns (e.g. `crowdsec`, `monitoring`) | Helm 3 stores release state as Secrets. These are owned by Helm/HelmRelease, not Flux Kustomizations. |
| `kube-prometheus-stack-{admission,grafana,alertmanager-...}` | `monitoring` | Owned by the kube-prometheus-stack chart's HelmRelease. |

## Auditing drift

```bash
# Dirs under apps/ that are neither Flux-active nor here in this doc:
diff <(ls -d apps/*/ | xargs -n1 basename | sort) \
     <(cat <(grep -oE '^\s+- [a-z][^ ]+' apps/kustomization.yaml | awk '{print $2}') \
            <(grep -oE '^\| `apps/[^/]+/`' docs/excluded-from-flux.md | sed 's|.*apps/||;s|/.*||') \
       | sort)
# Expect: no output.
```
