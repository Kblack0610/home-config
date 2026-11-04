# Accessing Container Filesystems

How to inspect and edit files inside running pods on the cluster - useful for debugging runtime state and live-tweaking files on a PVC (e.g. Home Assistant's `/config/www/`). Most of our images ship without an editor, so plain `kubectl exec` only gets you so far.

## Caveat: GitOps wins

Anything sourced from a ConfigMap or Secret will be **reverted by the next pod restart** (init containers re-seed from `/bootstrap`). Live-edit only files that live on PVCs or emptyDirs and are *not* re-written on startup. For config or secret changes, edit the manifest in this repo and let Flux reconcile.

Examples for Home Assistant (`apps/home-assistant/`, PVC mounted at `/config`):
- Safe to live-edit: your own files under `/config/www/` (but not `www/community/` or `www/snapcast-tablet/`, which init containers rewrite); read `/config/home-assistant.log` freely
- Will be re-seeded on restart: `/config/configuration.yaml` and `/config/dashboards/*.yaml` (copied from the `home-assistant-managed-config` ConfigMap by the `sync-managed-config` init container)
- Do not hand-edit `/config/.storage/` while HA runs: it is HA's own state and HA overwrites it

## Pattern 1 — Quick shell

For `ls`, `cat`, `tail`, checking processes:

```bash
kubectl exec -it -n home-assistant deploy/home-assistant -- /bin/sh
```

No editor available in most images.

## Pattern 2 — Ephemeral debug container with nvim

Best for poking around and light edits without leaving the cluster.

```bash
kubectl debug -it -n home-assistant deploy/home-assistant \
  --image=alpine:latest --target=home-assistant \
  -- sh -c 'apk add --no-cache neovim && exec sh'
```

How it works:
- `--target=home-assistant` shares the target container's process namespace.
- The target's filesystem is visible at `/proc/1/root/`.
- nvim installs into the ephemeral debug container; the debug container is discarded on exit.

Edit a file:
```sh
nvim /proc/1/root/config/www/notes.md
```

The package install (`apk add neovim`) re-runs every session. If this becomes a daily workflow, bake a tiny `kube-debug:nvim` image and reference it instead of `alpine:latest`.

## Pattern 3 — `kubectl cp` round-trip

Best when you want your full local nvim setup (LSP, plugins, theme) or are doing multi-file work.

```bash
POD=$(kubectl get pod -n home-assistant -l app.kubernetes.io/name=home-assistant -o jsonpath='{.items[0].metadata.name}')

kubectl cp home-assistant/$POD:config/www /tmp/ha-www
nvim /tmp/ha-www/
kubectl cp /tmp/ha-www home-assistant/$POD:config/www
```

Note: `kubectl cp` paths inside the pod are relative (no leading `/`).

## Optional shell helpers

Drop these into your shell config (zsh/bash) for one-liner access:

```sh
# Drop into a debug container with nvim, targeting a deployment
# usage: kdebug <namespace> <deploy>
kdebug() {
  local ns=$1 deploy=$2
  kubectl debug -it -n "$ns" "deploy/$deploy" \
    --image=alpine:latest --target="$deploy" \
    -- sh -c 'command -v nvim >/dev/null || apk add --no-cache neovim; exec sh'
}

# Edit a single file inside a container via cp round-trip
# usage: kedit <namespace> <deploy> <remote-path>
kedit() {
  local ns=$1 deploy=$2 remote=$3
  local pod tmp
  pod=$(kubectl get pod -n "$ns" -l "app.kubernetes.io/name=$deploy" -o jsonpath='{.items[0].metadata.name}')
  tmp=$(mktemp)
  kubectl cp "$ns/$pod:${remote#/}" "$tmp" && \
    nvim "$tmp" && \
    kubectl cp "$tmp" "$ns/$pod:${remote#/}"
  rm -f "$tmp"
}
```

Then:
```sh
kdebug home-assistant home-assistant
kedit  home-assistant home-assistant /config/www/notes.md
```

## What not to do

- **Don't add nvim to app images.** Bloats the image and grows the attack surface for a developer convenience.
- **Don't add a long-running debug sidecar.** Sidecars cost resources 24/7 for something used occasionally.
- **Don't mount a ReadWriteOnce PVC into a second pod** while the owning pod is running - the original pod will fail to schedule. Home Assistant, Forgejo, and Headscale all use RWO PVCs.
