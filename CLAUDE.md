# home-config Project Rules

Inherits shared rules from `~/CLAUDE.md`. The following are specific to this repository.

## Concurrent agents: use git worktrees

When more than one agent (Claude or otherwise) may touch this repo in parallel, work in an isolated git worktree, NOT the shared `~/dev/home/home-config/`. The shared checkout is for human / primary-Claude use only.

```bash
# One-time per task:
git worktree add ~/.worktrees/home-config-<agent>-<task> origin/master

# Cleanup:
git worktree remove ~/.worktrees/home-config-<agent>-<task>
```

Why: during the 2026-05 cleanup session, three concurrent agents (microbin, minio, nut, gatus) all branched off and committed to the SHARED checkout, which collided with my open work — a NUT commit landed on a chore branch I'd just created, my comfyui edits got reverted by a launcher regeneration, and Forgejo briefly merged a PR but never advanced master because of a race with a parallel push. Worktrees eliminate all of these failure modes (separate index, separate HEAD, separate working tree).

## Canonical Remote — forgejo only

This repo has two remotes: `forgejo` (canonical, Flux watches it) and `origin` (passive github mirror). **Always push to forgejo, never origin** — github is auto-mirrored on every push to master by `.forgejo/workflows/mirror-to-github.yaml`. Full doc: `docs/gitops.md#canonical-remote`.

If forgejo push fails with HTTPS-credential errors on Linux, the helper config probably defaults to `osxkeychain`. Fix once: `git config --global --add credential.https://git.kblab.me.helper store` (uses the existing token in `~/.git-credentials`).

## Deployment Model

Three layers own different parts of the lifecycle. Route work to the right tool before touching files:

| Layer | Owns | Directory |
|-------|------|-----------|
| **PXE** | Cold-boot OS install on bare metal | `infrastructure/pxe-server/` |
| **Ansible** | Host-OS services (systemd, launchd, brew), OpenWRT config, things below the kubelet | `ansible/` |
| **Flux** | Kubernetes workloads inside k3s | `apps/`, `clusters/` |

Rules:

- **All `apps/` changes deploy through Flux.** Never run `kubectl apply` on Flux-managed resources. The workflow is: edit manifests → commit → push → Flux reconciles (or `flux reconcile kustomization apps --with-source`).
- **All host-OS services go through Ansible.** Don't SSH to a box and write a systemd unit by hand; write an Ansible role under `ansible/roles/<name>/`, bind it in `ansible/playbooks/site.yml`, and run via `ansible-playbook` locally or via the `apps/ansible-runner/` CronJob (drift check) / `kubectl create job --from=cronjob/convergence-check` (ad-hoc). The old `infrastructure/{dhcp,openwrt}/*.sh` scripts are being migrated to Ansible in phases — check `docs/ansible.md` before adding another shell script.
- Direct `kubectl apply` is only valid for initial Flux bootstrap, resources outside Flux's watch path (`infrastructure/`), or emergency recovery with Flux suspended.
- See `docs/gitops.md` for the layer boundary and `docs/ansible.md` for the host-layer workflow.

## Secrets & Vault

- **ansible-vault password file**: `~/.ansible-vault-pass` on the workstation (mode 600, gitignored). Used by every `ansible-playbook` invocation against this repo. Set `ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass` in your shell, or pass `--vault-password-file ~/.ansible-vault-pass` per-run. Don't use `--ask-vault-pass` in this repo — the file already exists. Recovery if lost: `sops -d apps/ansible-runner/secret.yaml | grep ansible-vault-pass` (requires `~/.config/sops/age/keys.txt`).
- **SOPS age key**: `~/.config/sops/age/keys.txt`. Decrypts every `*.sops.yaml` and the in-cluster ansible-runner Secret. Without it, `flux bootstrap` works but SOPS-encrypted Kustomizations fail to decrypt. Full key-rotation procedure in `docs/gitops.md`.
- **Two SOPS / vault layers, don't confuse them**: SOPS protects in-cluster Secrets via `.sops.yaml` (Flux decrypts at apply time); ansible-vault protects host-OS secrets via `group_vars/<group>/vault.yml` (the workstation/runner decrypts at playbook run time). They share no keys — SOPS uses age, ansible-vault uses the password file. The ansible-runner CronJob bridges them: it stores the ansible-vault password as a SOPS-encrypted Secret so the in-cluster runner can decrypt host vaults at run time.

## OpenClaw: skills over MCPs

When extending OpenClaw's tool surface, prefer skills (bundled, ClawHub-installed, or authored locally via `skill-creator`) over MCP servers. Skills are first-class in OpenClaw's runtime — bundled with the image, declarative `SKILL.md` prompts, composable with native tools (`read`, `write`, `bash`, `browser`), and discoverable via `openclaw skills`. Many skills only need a CLI binary in the image to become ready (see `apps/openclaw/Dockerfile`).

Reach for an MCP server only when no skill covers the capability and the binary or API isn't a candidate for a `bins:`-based skill. Document any MCP addition with the rationale ("no skill for X because Y") in the same commit.

Discovery sequence before adding new tooling:

1. `kubectl -n openclaw exec deploy/openclaw -- openclaw skills check` — what's already ready, what's blocked by `bins:` / `env:` / `config:`.
2. `... openclaw skills search <topic>` — anything on ClawHub.
3. If a `bins:` requirement is the only blocker, add it to `apps/openclaw/Dockerfile` and rebuild via the `openclaw-image` Forgejo Actions workflow.
4. Only if all three fail: add an MCP server under `mcp.servers` in `apps/openclaw/configmap.yaml`.
