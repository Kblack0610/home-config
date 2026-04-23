# Homelab Catalog

One-page index of services and their management plane. Use this to answer "where does X run, and who reconciles it?"

| Service | Host | Runtime | Managed by | Manifest |
|---------|------|---------|------------|----------|
| **Kubernetes cluster workloads** | | | | |
| Home Assistant | any pi | k3s pod | Flux | `apps/home-assistant/` |
| Forgejo + runner | thinkcentre | k3s pod (DinD) | Flux | `apps/forgejo/` |
| LiteLLM | hp-victus | k3s pod (NVIDIA) | Flux | `apps/litellm/` |
| ComfyUI | hp-victus | k3s pod (NVIDIA) | Flux | `apps/comfyui/` |
| Immich | asus-laptop | k3s pod (AMD ROCm) | Flux | `apps/immich/` |
| OrcaSlicer | asus-laptop | k3s pod (AMD GPU) | Flux | `apps/orcaslicer/` |
| OpenClaw | hp-victus | k3s pod | Flux | `apps/openclaw/` |
| NAS (Samba) | asus-laptop | k3s pod | Flux | `apps/nas/` |
| Neptune proxy (Fluidd) | — | k3s Service+Endpoints | Flux | `apps/neptune/` |
| Portfolio / bnb-studios / black-dev | any pi | k3s pod | Flux | `apps/{portfolio,bnb-studios,black-dev}/` |
| Ansible Runner (CronJob, drift detection) | any k8s node | k8s CronJob | Flux | `apps/ansible-runner/` |
| **Bare-metal (non-cluster) services** | | | | |
| MLX code server | mac-studio | launchd `com.mlx-lm.code` | Ansible role authored, unbound → SSH + brew today | `ansible/roles/launchd-mlx-services/` ; `docs/mac-machines.md` |
| MLX smart server | mac-studio | launchd `com.mlx-lm.smart` | Ansible role authored, unbound → SSH + brew today | `ansible/roles/launchd-mlx-services/` ; `docs/mac-machines.md` |
| MLX reasoning server | mac-studio | launchd `com.mlx-lm.reasoning` | Ansible role authored, unbound → SSH + brew today | `ansible/roles/launchd-mlx-services/` ; `docs/mac-machines.md` |
| Ollama (fallback) | mac-studio | brew services | Ansible role authored, unbound → SSH + brew today | `ansible/roles/ollama/` ; `docs/mac-machines.md` |
| GitHub Actions runner (macOS) | mac-studio, mac-mini | LaunchAgent | Ansible role authored, unbound → `platform/tools/setup-mac-runner.sh` today | `ansible/roles/github-actions-runner-mac/` |
| node_exporter (macOS) | mac-studio, mac-mini | brew services (port 9100) | Ansible role authored, unbound → manual brew today | `ansible/roles/node-exporter-mac/` |
| Homebrew baseline | mac-studio, mac-mini | user-scoped brew install | Ansible role authored, unbound → `install.sh` today | `ansible/roles/brew-common/` |
| **GitHub Actions runner (Linux)** | **thinkcentre** | **systemd `actions.runner.thinkcentre-linux.service`** | **Ansible (this repo)** | **`ansible/roles/github-actions-runner-linux/`** |
| Fluidd / Moonraker (live) | neptune-3d-printer (192.168.1.54) | embedded (on printer) | — | — |
| AdGuard Home | pi3 (192.168.1.193) | docker-compose | In-repo compose, bootstrapped via `flash-pi.sh` + `setup.sh` | `apps/pi3-adguard-home/` |
| Frigate NVR | (standalone Pi) | docker-compose | In-repo compose, run via `docker compose up -d` | `apps/frigate/` |
| ESP32 smart switch | ESP32 | firmware | manual flash | `apps/esp32-firmware/` |
| **Routing and DNS** | | | | |
| OpenWRT (router, DHCP, DNS) | 192.168.1.1 | host OS | `infrastructure/openwrt/openwrt.sh` ; Ansible skeleton in draft PR | `infrastructure/openwrt/` ; `ansible/roles/openwrt-config/` |
| DHCP reservations | 192.168.1.1 | OpenWRT dnsmasq | `infrastructure/dhcp/dhcp.sh` ; Ansible skeleton in draft PR | `infrastructure/dhcp/devices.yaml` ; `ansible/roles/openwrt-dhcp/` |
| k3s agent (install + join) | every k3s node | host systemd | PXE inline install today ; Ansible role authored, unbound | `infrastructure/pxe-server/http/kickstart/profiles/cluster.sh` ; `ansible/roles/k3s-agent/` |
| Traefik (ingress controller) | k3s cluster | k3s pod | k3s built-in / `infrastructure/traefik/` | `infrastructure/traefik/` |
| Cloudflare tunnel | asus-laptop/hp-victus | k3s pod | Flux (in `apps/` somewhere) | — |
| **Provisioning** | | | | |
| PXE server | pc-home-cachy-main (workstation) | scripts | `infrastructure/pxe-server/install.sh` | `infrastructure/pxe-server/` |
| Flux bootstrap | k3s cluster | k3s pod (`flux-system` ns) | one-time `flux bootstrap`, then self-hosted | `clusters/home-k3s/flux-system/` |
| Ansible | workstation / `ansible-runner` CronJob | (runs from) | git in this repo | `ansible/` |

## Management layers at a glance

```
Day 0 (cold boot): PXE  →  Day 1+ below kubelet: Ansible  →  Day 1+ cluster: Flux
```

See `docs/gitops.md` for the layer boundaries and `docs/architecture.md` for worked examples of a request or lease flowing through all three layers. `docs/ansible.md` tracks the host-layer phase rollout (A–D).

## Roles authored vs applied

Every Ansible role listed above that shows "role authored, unbound" exists as code under `ansible/roles/<name>/` but is **not** yet bound in `ansible/playbooks/site.yml`. Today only `github-actions-runner-linux` is live-applied (against `thinkcentre`). Enabling a role means:

1. Seed the per-group vault (`vault_k3s_token`, `vault_github_pat`).
2. Uncomment the role block in `ansible/playbooks/site.yml`.
3. `ansible-playbook playbooks/site.yml --limit <host> --check --diff` → review drift.
4. Apply on a single host, verify, then widen the `--limit`.

The `apps/ansible-runner/` CronJob dry-runs `site.yml` nightly at 04:00 against any bound hosts, so drift surfaces in `kubectl logs` without needing manual runs.

## How to add a new row

1. Pick the management plane: Flux (in-cluster) / Ansible (host OS) / PXE-only (boot image) / out-of-repo (conscious exception).
2. If **Flux**: add manifests under `apps/<name>/` and list `- <name>` in `apps/kustomization.yaml`.
3. If **Ansible**: add a role under `ansible/roles/<name>/` and bind it in `ansible/playbooks/site.yml`.
4. Update this table.
