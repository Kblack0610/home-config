# Homelab Catalog

One-page index of services and their management plane. Use this to answer "where does X run, and who reconciles it?"

| Service | Host | Runtime | Managed by | Manifest |
|---------|------|---------|------------|----------|
| **Kubernetes cluster workloads** | | | | |
| Home Assistant | any pi | k3s pod | Flux | `apps/home-assistant/` |
| Frigate bridge (NVR UI) | (standalone Pi) | docker-compose | SSH (ad-hoc) | out-of-repo |
| Forgejo + runner | thinkcentre | k3s pod (DinD) | Flux | `apps/forgejo/` |
| LiteLLM | hp-victus | k3s pod (NVIDIA) | Flux | `apps/litellm/` |
| ComfyUI | hp-victus | k3s pod (NVIDIA) | Flux | `apps/comfyui/` |
| Immich | asus-laptop | k3s pod (AMD ROCm) | Flux | `apps/immich/` |
| OrcaSlicer | asus-laptop | k3s pod (AMD GPU) | Flux | `apps/orcaslicer/` |
| OpenClaw | hp-victus | k3s pod | Flux | `apps/openclaw/` |
| NAS (Samba) | asus-laptop | k3s pod | Flux | `apps/nas/` |
| Neptune proxy (Fluidd) | — | k3s Service+Endpoints | Flux | `apps/neptune/` |
| Portfolio / bnb-studios / black-dev | any pi | k3s pod | Flux | `apps/{portfolio,bnb-studios,black-dev}/` |
| Semaphore UI (Ansible run history) | any pi | k3s pod | Flux | `apps/semaphore/` |
| **Bare-metal (non-cluster) services** | | | | |
| MLX code server | mac-studio | launchd `com.mlx-lm.code` | SSH + brew (→ Ansible Phase D) | `docs/mac-machines.md` |
| MLX smart server | mac-studio | launchd `com.mlx-lm.smart` | SSH + brew (→ Ansible Phase D) | `docs/mac-machines.md` |
| MLX reasoning server | mac-studio | launchd `com.mlx-lm.reasoning` | SSH + brew (→ Ansible Phase D) | `docs/mac-machines.md` |
| Ollama (fallback) | mac-studio | brew services | SSH + brew (→ Ansible Phase D) | `docs/mac-machines.md` |
| GitHub Actions runner (macOS) | mac-studio, mac-mini | LaunchDaemon | `platform/tools/setup-mac-runner.sh` (→ Ansible Phase D) | — |
| **GitHub Actions runner (Linux)** | **thinkcentre** | **systemd `actions.runner.thinkcentre-linux.service`** | **Ansible (this repo)** | **`ansible/roles/github-actions-runner-linux/`** |
| Fluidd / Moonraker (live) | neptune-3d-printer (192.168.1.54) | embedded (on printer) | — | — |
| AdGuard Home | pi3 | docker-compose | SSH (ad-hoc) | out-of-repo |
| Frigate NVR | (standalone Pi) | docker-compose | SSH (ad-hoc) | out-of-repo |
| ESP32 smart switch | ESP32 | firmware | manual flash | out-of-repo |
| **Routing and DNS** | | | | |
| OpenWRT (router, DHCP, DNS) | 192.168.1.1 | host OS | `infrastructure/openwrt/openwrt.sh` (→ Ansible Phase C) | `infrastructure/openwrt/` |
| DHCP reservations | 192.168.1.1 | OpenWRT dnsmasq | `infrastructure/dhcp/dhcp.sh` (→ Ansible Phase C) | `infrastructure/dhcp/devices.yaml` |
| Traefik (ingress controller) | k3s cluster | k3s pod | k3s built-in / `infrastructure/traefik/` | `infrastructure/traefik/` |
| Cloudflare tunnel | asus-laptop/hp-victus | k3s pod | Flux (in `apps/` somewhere) | — |
| **Provisioning** | | | | |
| PXE server | pc-home-cachy-main (workstation) | scripts | `infrastructure/pxe-server/install.sh` | `infrastructure/pxe-server/` |
| Flux bootstrap | k3s cluster | k3s pod (`flux-system` ns) | one-time `flux bootstrap`, then self-hosted | `clusters/home-k3s/flux-system/` |
| Ansible | workstation / Semaphore pod | (runs from) | git in this repo | `ansible/` |

## Management layers at a glance

```
Day 0 (cold boot): PXE  →  Day 1+ below kubelet: Ansible  →  Day 1+ cluster: Flux
```

See `docs/gitops.md` for the layer boundaries and which tool owns which lifecycle event.

## How to add a new row

1. Pick the management plane: Flux (in-cluster) / Ansible (host OS) / PXE-only (boot image) / out-of-repo (conscious exception).
2. If **Flux**: add manifests under `apps/<name>/` and list `- <name>` in `apps/kustomization.yaml`.
3. If **Ansible**: add a role under `ansible/roles/<name>/` and bind it in `ansible/playbooks/site.yml`.
4. Update this table.
