# home-config Project Rules

Inherits shared rules from `~/.claude/CLAUDE.md`. The following are specific to this repository.

## Concurrent agents: use git worktrees

When more than one agent (Claude or otherwise) may touch this repo in parallel, work in an isolated git worktree, NOT the shared `~/dev/home/home-config/`. The shared checkout is for human / primary-Claude use only.

```bash
# One-time per task:
git worktree add ~/.worktrees/home-config-<agent>-<task> origin/master

# Cleanup:
git worktree remove ~/.worktrees/home-config-<agent>-<task>
```

Why: during the 2026-05 cleanup session, three concurrent agents (microbin, minio, nut, gatus) all branched off and committed to the SHARED checkout, which collided with my open work — a NUT commit landed on a chore branch I'd just created, my comfyui edits got reverted by a launcher regeneration, and Forgejo briefly merged a PR but never advanced master because of a race with a parallel push. Worktrees eliminate all of these failure modes (separate index, separate HEAD, separate working tree).

## Storage on asus-laptop

Three independent btrfs disks with no RAID, and the 8TB USB drive is a backup target, not NAS space. Inventory, jobs and what each disk failure loses: `docs/backup-runbook.md`.

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

### Host access naming (gotcha)

Bare-metal Pis have **four different names** for the same box, each resolved by a different system. Get the wrong one and your command goes nowhere:

| Where | Name shape | Example | Resolved by |
|-------|-----------|---------|-------------|
| `ansible/inventory.yml` `--limit` | bare alias | `pi5-master`, `pi5-worker2`, `pi4-worker4` | Ansible only — **not** DNS-resolvable |
| `ssh`, `kubectl`, `curl`, `nc`, etc. | DHCP name | `pi5-master-lan`, `pi5-worker2-lan` | OpenWRT dnsmasq via `infrastructure/dhcp/devices.yaml` |
| `kubectl get nodes`, `kubectl debug node/...` | k8s node name | `raspberrypi`, `raspberrypi-e3a771f1` | OS hostname (Pis kept default `raspberrypi[-suffix]`) |
| Always works | IP | `192.168.1.20` (master), `.21–.24, .124` (workers) | — |

Macs use SSH-config aliases (`m1`, `mac-studio`) instead — see `docs/mac-machines.md`. The `-lan` suffix is a Pi/x86 convention only.

Rule of thumb:
- runbook or shell command → use `-lan` or IP
- ansible `--limit` → bare alias
- `kubectl debug node/...` (the SSH-broken escape hatch) → k8s node name from `kubectl get nodes`

Full mapping table + the `kubectl debug node/<name> --profile=sysadmin -- chroot /host` recipe for fixing dead-SSH nodes lives in [`docs/host-access.md`](./docs/host-access.md).

## Local LLM routing

Anything that needs a self-hosted model calls the **LiteLLM gateway** (`apps/litellm`, OpenAI-compatible `/v1`). Never curl a model host by IP:port, and never `ollama pull` as a workaround when an endpoint is unreachable.

- **Ollama is decommissioned.** Mac Studio serves models via `mlx-server` (:8090), fronted by LiteLLM. A hardcoded `:11434` is always stale.
- **LiteLLM fronts three tiers:** local MLX (free), Gemini (free tier), and paid **Lazer** (`proxy.lazertechnologies.com`). Lazer is the user's own AI allowance ($250/mo, LiteLLM team `lazer-pool`), not a client proxy, and personal data may use it. Reach it through the gateway with a scoped key so spend is metered.
- **Consumers hold a scoped virtual key, never the master key.** Scope `allowed_models` to the tiers that consumer may use, so a wrong model name fails closed at the gateway instead of leaking content to a paid or cloud upstream. Minting: `apps/litellm/README.md` -> Create a key.
- **Config points at the gateway, not a host.** If a config key names a model endpoint, verify something actually reads it; a documented-but-dead key hides the real endpoint in a code default.

Why: 2026-07-20 the `comms` email-triage skill classified tier-2 against `192.168.1.4:11434`, decommissioned months earlier. Its fail-closed path defaulted to FYI, so triage silently degraded to "everything FYI" with no error, and an agent debugging it proposed `ollama pull` because nothing mentioned LiteLLM.

## Secrets

- **One store for secrets in git: SOPS + age.** Kubernetes Secrets (Flux decrypts at apply time) and Ansible's `vault_*` vars (`ansible/group_vars/<group>/vault.sops.yaml`, decrypted at playbook run time by the `community.sops` vars plugin) are both SOPS files. ansible-vault is retired; see `ansible/README.md` "Secrets".
- **SOPS age key**: `~/.config/sops/age/keys.txt`. Decrypts every `*.sops.yaml` and the in-cluster ansible-runner Secret. Without it, `flux bootstrap` works but SOPS-encrypted Kustomizations fail to decrypt, and `ansible-playbook` stops at "Failed to get the data key". Full key-rotation procedure in `docs/gitops.md`.
- **The ansible-runner holds a second, Ansible-only age key** (`apps/ansible-runner/secret.yaml` key `sops-age-key`). The Ansible files are encrypted to both keys, so the runner can read them and no Kubernetes Secret.
- **Passwords a human types live in rbw** (`mac_fleet_password`, `linux_fleet_password`). The repo holds only a hash when Ansible needs one.

## OpenClaw: skills over MCPs

When extending OpenClaw's tool surface, prefer skills (bundled, ClawHub-installed, or authored locally via `skill-creator`) over MCP servers. Skills are first-class in OpenClaw's runtime — bundled with the image, declarative `SKILL.md` prompts, composable with native tools (`read`, `write`, `bash`, `browser`), and discoverable via `openclaw skills`. Many skills only need a CLI binary in the image to become ready (see `apps/openclaw/Dockerfile`).

Reach for an MCP server only when no skill covers the capability and the binary or API isn't a candidate for a `bins:`-based skill. Document any MCP addition with the rationale ("no skill for X because Y") in the same commit.

Discovery sequence before adding new tooling:

1. `kubectl -n openclaw exec deploy/openclaw -- openclaw skills check` — what's already ready, what's blocked by `bins:` / `env:` / `config:`.
2. `... openclaw skills search <topic>` — anything on ClawHub.
3. If a `bins:` requirement is the only blocker, add it to `apps/openclaw/Dockerfile` and rebuild via the `openclaw-image` Forgejo Actions workflow.
4. Only if all three fail: add an MCP server under `mcp.servers` in `apps/openclaw/configmap.yaml`.
