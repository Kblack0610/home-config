# Home-K3s GitOps Hygiene — Follow-up Plan

**Created:** 2026-05-18
**Status:** Executed 2026-05-21 — both workstreams landed on `chore/home-k3s-gitops-hygiene`. See "Lessons" footer for what the original plan got wrong.
**Context:** Surfaced during the `kblab.me → blacknbrownstudios.com` preview-domain migration. See PR #631.

## Goal

Two related cleanups on the `home-k3s` cluster that are out of scope for the preview-domain PR but should ship soon:

1. **Bring un-managed deployments under Flux** — `cloudflared-public-sites` is the most important one, because it's on the critical path for every public site (`blacknbrownstudios.com`, `kennethblack.me`, `kblack.dev`, the preview env). Manually applied = no audit trail, no drift detection, and a single ad-hoc `kubectl delete` breaks every public URL at once.
2. **Unstick the four "zombie" workloads** that are blocking Flux's `apps` Kustomization from completing reconciliation. As long as they're failing the post-apply health check, every other Flux change gets stalled (the symptom that bit us during the cert-manager ClusterIssuer fix).

## Symptom we already paid for

During the bnb.com preview migration, my edit to the `letsencrypt-dns` ClusterIssuer (adding `blacknbrownstudios.com` to `dnsZones`) didn't propagate via Flux even after multiple reconcile attempts. Root cause:

```
HealthCheckFailed (x4017 over 11d): timeout waiting for: [
  Deployment/openclaw/openclaw status: 'InProgress',
  Deployment/comfyui/comfyui  status: 'InProgress'
]
```

The kustomize-controller times out after 5 minutes waiting for those two deployments to become healthy. New revisions get applied, but the kustomization never reaches `Ready=True`, which prevents subsequent reconciles from advancing in a clean way (`Last Applied Revision` lags behind `Last Attempted Revision`).

I unblocked it for the preview migration by applying the ClusterIssuer manifest via `kubectl apply` directly, which is a hack. The right fix is to clear the underlying breakage.

## Workstream A — Pull `cloudflared-public-sites` into Flux

### Why it matters

This single deployment in the `apps` namespace fronts every public-facing service on home-k3s. It currently has no source manifest in `Kblack0610/home-config`; it was `kubectl apply`'d directly long ago. If it gets deleted, replaced, or drifts, there's no recovery path other than "remember what flags we passed."

A second deployment in the same boat: `apps/cloudflared` was the legacy `placemyparents-local-staging` runner — already decommissioned in PR #631 — so this workstream is just about the _remaining_ one.

### Steps

1. **Export the live manifest, strip cluster-managed fields:**
   ```bash
   kubectl --context home-k3s -n apps get deploy cloudflared-public-sites -o yaml | \
     yq 'del(
       .metadata.uid,
       .metadata.resourceVersion,
       .metadata.generation,
       .metadata.creationTimestamp,
       .metadata.managedFields,
       .metadata.annotations."deployment.kubernetes.io/revision",
       .metadata.annotations."kubectl.kubernetes.io/restartedAt",
       .status
     )' > /tmp/cloudflared-public-sites.yaml
   ```
2. **Export the token secret** (sensitive — must be SOPS-encrypted before commit):
   ```bash
   kubectl --context home-k3s -n apps get secret cloudflared-public-sites-tunnel-token -o yaml | \
     yq 'del(.metadata.uid, .metadata.resourceVersion, .metadata.creationTimestamp, .metadata.managedFields)' \
     > /tmp/cloudflared-public-sites-secret.yaml
   ```
3. **Create the home-config kustomize unit:**
   ```
   home-config/apps/cloudflared-public-sites/
   ├── deployment.yaml         # from step 1, cleaned
   ├── secret.yaml             # from step 2, SOPS-encrypted
   └── kustomization.yaml      # lists both resources
   ```
   Encrypt with: `sops -e -i apps/cloudflared-public-sites/secret.yaml`.
4. **Wire it into the root `apps/kustomization.yaml`** in home-config.
5. **Push to forgejo master.** Flux reconciles, adopts the existing in-cluster resources (sets `kustomize.toolkit.fluxcd.io/name=apps` annotation), and from then on owns them. No pod restart needed because the spec matches.
6. **Verify adoption:**
   ```bash
   kubectl --context home-k3s -n apps get deploy cloudflared-public-sites -o jsonpath='{.metadata.labels}'
   # Should now include kustomize.toolkit.fluxcd.io/name=apps
   ```
7. **Document in `docs/deployment/DOMAIN_ROUTING.md`** (this repo) — flip the "Cloudflare tunnel" section's "managed manually" note to "managed via home-config".

### Risk

Low. Adoption is non-destructive. The pod keeps running. Worst case is the secret's SOPS key isn't right and Flux can't decrypt → adoption fails with a clear error; revert by removing the new files from home-config.

### Also-out-of-scope but similar

The old `apps/cloudflared` deployment was deleted in PR #631 but its `cloudflared-public-sites-tunnel-token` secret is still in the `apps` namespace alongside the new token. Probably fine to leave; can be audited as part of this workstream.

## Workstream B — Unstick the four zombies

### The cohort

| Workload                                                   | Symptom                                    | Likely root cause                                                                         |
| ---------------------------------------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------- |
| `comfyui/comfyui`                                          | 1 pod Terminating (35d), 1 Pending (11d)   | Likely PVC stuck, or scheduling constraint (GPU?) unmet                                   |
| `openclaw/openclaw`                                        | 1 pod Terminating (17d), 1 Pending (11d)   | Likely scheduling, or its dependent images repo (Forgejo) was down                        |
| `crowdsec/crowdsec-agent` (DaemonSet)                      | 7 pods stuck `Unknown` for 38d             | Node-agent communication broken; nodes likely restarted in a state crowdsec can't recover |
| `monitoring/prometheus-kube-prometheus-stack-prometheus-0` | Pod Terminating 38d, statefulset 0/1 ready | Stuck finalizer or PVC; common on prometheus operator upgrades                            |

### Recommended order

1. **Decide whether each app is still wanted.** Names suggest `comfyui` (Stable Diffusion UI) and `openclaw` (claw machine controller) are hobbyist projects. If you're not using them, simplest fix is comment them out of `home-config/apps/kustomization.yaml`. Lose nothing valuable, Flux unsticks immediately.
2. **For each kept app**, diagnose:
   - `kubectl describe pod -n <ns> <pod>` — look at events, especially scheduling failures
   - `kubectl get pvc -n <ns>` — stuck PVC? `kubectl describe pvc` shows whether it's bound, lost, or pending
   - `kubectl get events -n <ns> --sort-by='.lastTimestamp'`
3. **For Terminating pods that won't die:** force-delete after confirming the underlying resource is OK:
   ```bash
   kubectl --context home-k3s -n <ns> delete pod <name> --force --grace-period=0
   ```
4. **For crowdsec specifically:** the agent is a DaemonSet; the `Unknown` state typically means the kubelet on those nodes hasn't reported on those pods in a long time. Likely cause: agent died and was never re-scheduled because of a stale `spec.podSelector` from a CRD upgrade. Try `kubectl rollout restart daemonset crowdsec-agent -n crowdsec`.
5. **For prometheus:** if there's no useful metric data being preserved, easiest is to delete the PVC + recreate. If history matters, follow [prometheus-operator's stuck-pod runbook](https://github.com/prometheus-operator/prometheus-operator/blob/main/Documentation/operator.md).

### Alternative: skip health checks for the broken ones

If you want Flux unblocked _now_ without fixing the root cause, edit the `apps` Kustomization manifest to set `healthChecks` only on the resources that matter:

```yaml
# home-config/clusters/home-k3s/apps.yaml (or wherever the Kustomization lives)
spec:
  # ...
  healthChecks:
    - apiVersion: apps/v1
      kind: Deployment
      name: portfolio-web
      namespace: portfolio
    # ... etc for the things you actually care about
  # Don't list comfyui / openclaw — Flux won't wait on them
```

This is a workaround, not a fix. It hides the breakage until somebody actively looks at one of those pods.

### Risk

Low for option 1 (commenting out unused apps). Moderate for the force-delete + rollout-restart approach on crowdsec/prometheus — those are doing actual work in the cluster, so if they recover wrong we lose monitoring coverage temporarily.

## Verification (when this plan ships)

After both workstreams complete:

```bash
# Flux apps kustomization should reach Ready=True
flux --context home-k3s get kustomization apps -n flux-system
# Expect: READY=True, MESSAGE="Applied revision: master@sha1:..."

# cloudflared-public-sites is Flux-labelled
kubectl --context home-k3s -n apps get deploy cloudflared-public-sites \
  -o jsonpath='{.metadata.labels.kustomize\.toolkit\.fluxcd\.io/name}'
# Expect: apps

# Zero pods in non-running state across the cluster (modulo intentional Suspended/Completed)
kubectl --context home-k3s get pods -A --no-headers | \
  awk '$4!~/Running|Completed/ {print}'
# Expect: empty

# Endpoints still 200
curl -sI https://placemyparents.blacknbrownstudios.com
curl -sI https://blacknbrownstudios.com
curl -sI https://kennethblack.me
curl -sI https://kblack.dev
```

## Out of scope

- The fundamentally weird `BBS_HOMELAB.md` doc (refers to a renamed cluster + decommissioned staging stack). Worth archiving but not blocking on this plan.
- Pulling all the other manually-applied resources into Flux. Audit which exist via:
  ```bash
  kubectl get all -A --no-headers | \
    while read ns name rest; do
      label=$(kubectl get "$name" -n "$ns" -o jsonpath='{.metadata.labels.kustomize\.toolkit\.fluxcd\.io/name}' 2>/dev/null)
      [ -z "$label" ] && echo "unmanaged: $ns/$name"
    done
  ```
  Anything that prints "unmanaged" is a candidate. Likely list: `cloudflared-public-sites` (this plan), plus possibly a few namespace + secret resources. Not investigated.

## Links

- Surfaced during: PR #631 (chore/placemyparents/preview-on-bnb-domain)
- home-config: `Kblack0610/home-config` on `forgejo master` (NOT github)

## Lessons (added 2026-05-21 after execution)

The original plan diagnosed the zombies as four independent app-level breakages. In reality, three of the four (`comfyui`, `openclaw`, `prometheus-...-0`) were symptoms of a **single root cause**: `hp-victus` had been off the network since 2026-05-07 (laptop lid had been closed → suspended → kubelet stopped posting status). All three workloads had local-path PVCs pinned to that node, so:

- Their `Terminating` pods couldn't drain (no kubelet to reap).
- Their replacement pods couldn't schedule elsewhere (PVC node-affinity locked them to hp-victus).

Once the lid was opened, kubelet resumed, the Terminating pods drained, and the StatefulSets/Deployments placed replicas back on hp-victus. No application-level intervention was needed for any of those three. Only `crowdsec-agent` was a real app-level issue (init container `wait-for-lapi-and-register` in CrashLoopBackOff across multiple nodes — unrelated to hp-victus, fixed by `rollout restart`).

Takeaways for future cluster cleanups:

1. **Before diagnosing per-pod, check node health.** `kubectl get nodes` first. A `NotReady` node with local-path-bound PVCs cascades into N apparent app failures.
2. **`docs/homelab-catalog.md` was the fastest path to root cause** — it lists which apps are pinned to which workstation. Read it before assuming N independent issues.
3. **Laptops as k3s nodes have a sleep/lid-close failure mode** the original plan didn't anticipate. Worth a follow-up Ansible role to disable lid-close suspend on `hp-victus` / `asus-laptop`, or a Prometheus alert on node Ready=Unknown for > 5 min. Captured in `~/.agent/lessons/home-config.md`.
4. **Existing WoL infrastructure (`infrastructure/pxe-server/tools/setup-autoinstall.sh`) didn't help here** because the machine was suspended, not powered off — WoL magic packets don't wake a suspended laptop reliably. The physical-access fallback was unavoidable.
