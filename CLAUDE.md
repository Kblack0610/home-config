# home-config Project Rules

Inherits shared rules from `~/CLAUDE.md`. The following are specific to this repository.

## Deployment Model

Three layers own different parts of the lifecycle. Route work to the right tool before touching files:

| Layer | Owns | Directory |
|-------|------|-----------|
| **PXE** | Cold-boot OS install on bare metal | `infrastructure/pxe-server/` |
| **Ansible** | Host-OS services (systemd, launchd, brew), OpenWRT config, things below the kubelet | `ansible/` |
| **Flux** | Kubernetes workloads inside k3s | `apps/`, `clusters/` |

Rules:

- **All `apps/` changes deploy through Flux.** Never run `kubectl apply` on Flux-managed resources. The workflow is: edit manifests → commit → push → Flux reconciles (or `flux reconcile kustomization apps --with-source`).
- **All host-OS services go through Ansible.** Don't SSH to a box and write a systemd unit by hand; write an Ansible role under `ansible/roles/<name>/`, bind it in `ansible/playbooks/site.yml`, and run via `ansible-playbook` (or the Semaphore UI at `semaphore.kblab.me`). The old `infrastructure/{dhcp,openwrt}/*.sh` scripts are being migrated to Ansible in phases — check `docs/ansible.md` before adding another shell script.
- Direct `kubectl apply` is only valid for initial Flux bootstrap, resources outside Flux's watch path (`infrastructure/`), or emergency recovery with Flux suspended.
- See `docs/gitops.md` for the layer boundary and `docs/ansible.md` for the host-layer workflow.
