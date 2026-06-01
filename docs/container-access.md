# Accessing Container Filesystems

How to inspect and edit files inside running pods on the cluster — useful for debugging runtime state and live-tweaking workspace files (e.g. openclaw's `/home/node/.openclaw/workspace/`). Most of our images ship without an editor, so plain `kubectl exec` only gets you so far.

## Caveat: GitOps wins

Anything sourced from a ConfigMap or Secret will be **reverted by the next pod restart** (init containers re-seed from `/bootstrap`). Live-edit only files that live on PVCs or emptyDirs and are *not* re-written on startup. For config or secret changes, edit the manifest in this repo and let Flux reconcile.

Examples for openclaw:
- Safe to live-edit: `/home/node/.openclaw/workspace/`, `/home/node/.openclaw/logs/`
- Will be re-seeded on restart: `/home/node/.openclaw/openclaw.json`, `/home/node/.openclaw/AGENTS.md`

## Pattern 1 — Quick shell

For `ls`, `cat`, `tail`, checking processes:

```bash
kubectl exec -it -n openclaw deploy/openclaw -- /bin/sh
```

No editor available in most images.

## Pattern 2 — Ephemeral debug container with nvim

Best for poking around and light edits without leaving the cluster.

```bash
kubectl debug -it -n openclaw deploy/openclaw \
  --image=alpine:latest --target=openclaw \
  -- sh -c 'apk add --no-cache neovim && exec sh'
```

How it works:
- `--target=openclaw` shares the target container's process namespace.
- The target's filesystem is visible at `/proc/1/root/`.
- nvim installs into the ephemeral debug container; the debug container is discarded on exit.

Edit a file:
```sh
nvim /proc/1/root/home/node/.openclaw/workspace/notes.md
```

The package install (`apk add neovim`) re-runs every session. If this becomes a daily workflow, bake a tiny `kube-debug:nvim` image and reference it instead of `alpine:latest`.

## Pattern 3 — `kubectl cp` round-trip

Best when you want your full local nvim setup (LSP, plugins, theme) or are doing multi-file work.

```bash
POD=$(kubectl get pod -n openclaw -l app=openclaw -o jsonpath='{.items[0].metadata.name}')

kubectl cp openclaw/$POD:home/node/.openclaw/workspace /tmp/openclaw-workspace
nvim /tmp/openclaw-workspace/
kubectl cp /tmp/openclaw-workspace openclaw/$POD:home/node/.openclaw/workspace
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
  pod=$(kubectl get pod -n "$ns" -l "app=$deploy" -o jsonpath='{.items[0].metadata.name}')
  tmp=$(mktemp)
  kubectl cp "$ns/$pod:${remote#/}" "$tmp" && \
    nvim "$tmp" && \
    kubectl cp "$tmp" "$ns/$pod:${remote#/}"
  rm -f "$tmp"
}
```

Then:
```sh
kdebug openclaw openclaw
kedit  openclaw openclaw /home/node/.openclaw/workspace/notes.md
```

## What not to do

- **Don't add nvim to app images.** Bloats the image and grows the attack surface for a developer convenience.
- **Don't add a long-running debug sidecar.** Sidecars cost resources 24/7 for something used occasionally.
- **Don't mount a ReadWriteOnce PVC into a second pod** while the owning pod is running — the original pod will fail to schedule. Openclaw, Home Assistant, Forgejo, and Headscale all use RWO PVCs.
