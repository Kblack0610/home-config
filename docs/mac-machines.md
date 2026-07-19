# Mac Machines Setup Guide

Two Apple-Silicon Macs on the home LAN, each with a **single dedicated role** since the 2026-07-18 split:

- **mac-studio** (M3 Ultra) — dedicated **LLM inference node** (MLX). No CI, deeply debloated, tuned for large models.
- **mac-mini** (M1) — the **sole self-hosted mobile-CI runner** (iOS/Android builds, TestFlight/Play upload).

They are **not** part of the K3s cluster (K3s is Linux-only). The Pi cluster handles containerized workloads; Macs handle Apple-native + GPU-inference workloads.

## Inventory

| Machine | Chip | RAM | IP | inventory host | scutil hostname | Role |
|---------|------|-----|-----|----------------|-----------------|------|
| Mac Studio | M3 Ultra | 512 GB | 192.168.1.4 | `mac-studio` | mac-studio | **LLM inference node (MLX)** |
| Mac Mini | M1 | 16 GB | 192.168.1.7 | `mac-mini` | pc-home-m1-mini | **CI runner** |

### What lives where (post-split manifest)

| | mac-studio (LLM node) | mac-mini (CI node) |
|---|---|---|
| **Runs** | MLX server `com.mlx-server` :8090 (5 pinned + 5 on-demand candidates), node_exporter :9100, powermetrics textfile sampler | GH Actions runner `mac-mini-mac`, node_exporter :9100, iOS toolchain (Xcode, CocoaPods, node/pnpm/yarn, JDK17) |
| **Removed** | GH runner (torn down + deregistered); brew `node@20`/`pnpm`/`yarn`/`cocoapods`/`openjdk@17`; `Xcode.app`; `~/actions-runner`; (optional) `~/dev/bnb/platform` | — |
| **Kept despite debloat** | `node` (used by `opencode`), `ruby` (used by `cocoapods`+`tmuxinator`), `node_exporter` | full toolchain |
| **Tuned** | `iogpu.wired_limit_mb=491520` (480 GB); Spotlight/Time Machine/Siri/analytics/Power+App Nap off | — |

The split is expressed as **per-host plays** in `ansible/playbooks/site.yml`. No mobile workflow pins a runner by name (only by the `[self-hosted, macOS, arm64]` label set), so demoting mac-studio is transparent to CI as long as mac-mini is online.

## Role: LLM inference node (mac-studio)

Runs a single `mlx-openai-server` (cubist38) process serving chat, vision, and embedding models from one OpenAI-compatible endpoint on `:8090`. (This replaced the retired three-service `com.mlx-lm.{code,smart,reasoning}` layout on 8080/8081/8082.)

Served (`mlx_models` in `roles/launchd-mlx-server/defaults/main.yml`):

- **Pinned (~73 GB, always resident):** `code` (Qwen3-Coder-Next-4bit), `reasoning` (Qwen3.6-35B-A3B-4bit), `fast` (Qwen3-4B, 4bit + 8bit), `embedding` (modernbert-embed-base-4bit).
- **on_demand (JIT-loaded, idle-evicted — dual-tier A/B candidates, 2026-07):** `code` (Qwen3-Coder-Next-6bit), `code` (Qwen3-Coder-480B-A35B-4bit, ~250 GB), `reasoning` (Qwen3.5-397B-A17B-4bit, ~214 GB), `vlm` (Qwen3-VL-30B-A3B-8bit — needs `mlx-vlm>=0.6.5`), `embedding` (Qwen3-Embedding-0.6B).

Each local model has a matching route in `apps/litellm/configmap.yaml`; the `router_settings.model_group_alias` block there toggles each category (`code`/`reasoning`/`fast`/`vlm`/`embedding`) between the local box and the paid Lazer/Gemini tier in one line. The 1T Kimi K2 flagship is a manual "flagship mode" (sole-resident), not a live entry — see `roles/launchd-mlx-server/README.md`.

- **Python venv:** `~/mlx-env` (not created by Ansible; `launchd-mlx-server` asserts it exists)
- **LaunchAgent:** `~/Library/LaunchAgents/com.mlx-server.plist` (per-user — Metal needs a logged-in GUI session, NOT a LaunchDaemon)

```bash
launchctl list | grep mlx-server        # com.mlx-server, numeric PID
curl http://192.168.1.4:8090/v1/models  # list served models
tail -f /tmp/mlx-server.log
```

### Ansible roles on this box

- **`launchd-mlx-server`** — the MLX server itself (models, config, HF token).
- **`launchd-powermetrics-textfile`** — feeds Apple-Silicon GPU/ANE/CPU power into node_exporter.
- **`macos-llm-node`** — the *specialization*: removes CI/build bloat, applies reversible OS debloat, and installs the LLM tuning (`iogpu.wired_limit_mb`) as a root LaunchDaemon. See `roles/macos-llm-node/README.md` for the full var table + an "Unapply" section (every toggle is reversible).
- **`macos-baseline`** (shared) — power persistence + SSH key sync + auto-login/FileVault assert.

### LLM tuning: `iogpu.wired_limit_mb`

macOS caps how much unified memory the GPU/Metal may wire for model weights (~65-75% by default). On a 512 GB M3 Ultra that fences off ~130-180 GB. `macos-llm-node` raises it to **491520 MB (480 GB)**, leaving a 32 GB OS reserve. macOS does not persist `sysctl -w` (and ignores `/etc/sysctl.conf`), so a root LaunchDaemon `com.kblab.llm-sysctl` (`RunAtLoad`) re-applies it at every boot; the role also applies it live so no reboot is needed to converge.

```bash
ssh mac-studio "sysctl iogpu.wired_limit_mb"   # 491520
ssh mac-studio "cat /tmp/llm-sysctl.log"       # boot-time apply log
```

**Never approach the full 512 GB** — starving macOS of wired memory hard-hangs the box. Watch Activity Monitor memory pressure after changing it. Revert with `sudo sysctl -w iogpu.wired_limit_mb=0` (Apple default) + remove the LaunchDaemon.

### Headless prerequisites (or it won't recover unattended)

- **FileVault OFF + auto-login ON** — MLX/Metal needs a logged-in WindowServer session, and auto-login requires FileVault off. `macos-baseline` asserts this. **macOS 26 Tahoe re-enables FileVault by default** (incl. on upgrades); if `fdesetup status` shows On, run `sudo fdesetup disable` (interactive: admin pw + a secure-token user) + reboot before trusting recovery.
- **1080p HDMI dummy plug** — with no display attached macOS gives only a low-res virtual framebuffer and can throttle Metal init. Fit a cheap **1080p** (not 4K) dummy plug and reboot once. (mac-studio currently reports a 1920x1080 display — satisfied.)

## Role: CI runner (mac-mini)

The sole self-hosted runner for `BlackNBrownStudios/platform` mobile CI. Native macOS workloads that can't run in a container: Xcode/iOS builds, Expo prebuild, CocoaPods, TestFlight submission.

- **Runner:** `mac-mini-mac`, labels `[self-hosted, macOS, arm64]`, per-user LaunchAgent (`github-actions-runner-mac` role, `present` mode).
- **Toolchain (installed by the platform repo's `tools/setup-mac-runner.sh`, not Ansible):** Xcode, CocoaPods, node/pnpm/yarn, JDK17, ruby.

**Capacity note:** 16 GB RAM is the ceiling. Fine for single-stream iOS builds but tight — Xcode + a booted Simulator + Metal/UI tests push 16 GB into swap. Keep runner concurrency at 1 (a single runner process already = 1 concurrent job) and periodically clear DerivedData / simulator runtimes / npm+CocoaPods caches. If mobile CI volume grows, 16 GB is the first bottleneck.

### Workflows

| Workflow | File | Trigger | Runner |
|----------|------|---------|--------|
| Mobile Local Build | `mobile-local-build.yml` | PRs to main (mobile changes) | `[self-hosted, macOS]` |
| Mobile Local Release | `mobile-local-release.yml` | Git tags `*-mobile-v*.*.*` | `[self-hosted, macOS]` |
| Deploy Mobile (backup) | `deploy-mobile.yml` | Manual | `ubuntu-latest` (EAS Cloud) |

### Required GitHub Secrets

**iOS:** `KEYCHAIN_PASSWORD` (login pw for keychain unlock), `APPLE_ID`, `EXPO_APPLE_PASSWORD` (app-specific pw), `APPLE_TEAM_ID`.
**Android:** `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`, `GOOGLE_PLAY_SERVICE_ACCOUNT_KEY`.

## Reprovision from bare metal

Both flows start the same: on the new Mac, `git clone https://github.com/kblack0610/.dotfiles.git ~/.dotfiles && cd ~/.dotfiles/.local/src/installation_scripts && bash install.sh` (Homebrew, dev tools, macOS defaults, workstation SSH key). Then add/confirm the host in `ansible/inventory.yml` under `macos_hosts` and run from the workstation with `ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass` (become password for `-K` is rbw item `mac_fleet_password`).

### LLM node (mac-studio)

```bash
# 1. MLX venv (native, one-time): python3.12 -m venv ~/mlx-env && ~/mlx-env/bin/pip install mlx-openai-server
# 2. Confirm FileVault off + auto-login on + a 1080p display/dummy-plug attached.
cd ~/dev/home/home-config/ansible
ansible-playbook playbooks/site.yml --limit mac-studio --tags mac -K --check --diff   # review first
ansible-playbook playbooks/site.yml --limit mac-studio --tags mac -K                  # apply
# Verify:
gh api repos/BlackNBrownStudios/platform/actions/runners --jq '.runners[]|.name'  # NO mac-studio-mac
ssh mac-studio "curl -s http://127.0.0.1:8090/v1/models | head -c 80"             # MLX serving
ssh mac-studio "sysctl iogpu.wired_limit_mb"                                       # 491520
```

The mac-studio play sets the destructive removal vars (`macos_llm_node_remove_xcode`, `..._platform_checkout`, `..._runner_dir`) true — always `--check --diff` first, and note the checkout removal HARD-FAILS if `~/dev/bnb/platform` has uncommitted/unpushed work.

### CI node (mac-mini)

```bash
# 1. Build tools (native, one-time): git clone platform && ./tools/setup-mac-runner.sh  (do NOT register here)
cd ~/dev/home/home-config/ansible
ansible-playbook playbooks/site.yml --limit mac-mini --tags mac -K --check --diff
# Fresh box registers cleanly; a stale orphaned registration needs force:
ansible-playbook playbooks/site.yml --limit mac-mini --tags mac -K -e gh_runner_mac_force_reregister=true
# Verify:
gh api repos/BlackNBrownStudios/platform/actions/runners --jq '.runners[]|{name,status}'  # mac-mini-mac online
ssh mac-mini "launchctl list | grep actions.runner"   # numeric PID
```

## Power / persistence (survive a power failure)

The load-bearing setting is `pmset autorestart 1`: after a power cut the Mac boots itself, auto-login reaches a logged-in session, and the per-user LaunchAgent (`RunAtLoad`) brings the workload (MLX or runner) back online — no human needed. `macos-baseline` sets and asserts this; `autorestart 0` is why both Macs previously stayed dark after outages (found 2026-07-17). FileVault MUST stay off and auto-login on, or the boot stops at the login window and nothing recovers.

## Why not MDM

Considered and rejected. MDM's unique capability is zero-touch DEP/ADE enrollment, which requires Apple Business Manager (DUNS + business verification). Everything we actually need — power policy, auto-login, runner, SSH keys, debloat, tuning — is host-OS config that Ansible already owns per this repo's deployment model (`docs/gitops.md`). Without ABM, MDM only offers user-removable profiles, which is weaker than Ansible and adds a server to run. It also does not solve power-failure recovery; that is purely `pmset autorestart`.

## macOS Defaults / debloat ownership

| Setting | Value | Why | Owner |
|---------|-------|-----|-------|
| Auto-restart after power failure | Enabled | Unattended boot after outage | macos-baseline (pmset) |
| Sleep / disk sleep / display sleep | Disabled | Headless server, must stay awake | macos-baseline (pmset) |
| Wake-on-LAN (womp) | Enabled | Remote wake | macos-baseline (pmset) |
| FileVault | Disabled | Allows auto-login on restart | install script (asserted by macos-baseline) |
| Auto-login | Enabled (current user) | Unattended restarts after power outage | install script (asserted by macos-baseline) |
| **Spotlight index / Time Machine / Siri / analytics / Power+App Nap** | **Off** | **Headless LLM node debloat** | **macos-llm-node** (mac-studio only, all reversible) |
| **iogpu.wired_limit_mb** | **491520 (480 GB)** | **Large-model MLX headroom** | **macos-llm-node** (LaunchDaemon) |

Only user-space + supported knobs are touched (`defaults`/`mdutil`/`tmutil`/`pmset`/`sysctl`, and `bootout` of user agents). SIP-protected `/System/Library` daemons (`mDNSResponder`, `opendirectoryd`, `sshd`, `WindowServer`, ...) are NEVER touched — they break SSH/login/Metal and are SIP-revived anyway.

## SSH Access

```bash
ssh m1            # Mac Mini M1  (user kennethblack, 192.168.1.7)
ssh mac-studio    # Mac Studio M3 Ultra (user kblack0610, 192.168.1.4)
```

SSH config (`~/.ssh/config`):
```
Host m1 mac-mini
    HostName 192.168.1.7
    User kennethblack
    IdentityFile ~/.ssh/id_ed25519

Host mac-studio
    HostName 192.168.1.4
    User kblack0610
    IdentityFile ~/.ssh/id_ed25519
```

## Monitoring

Both Macs run [node_exporter](https://github.com/prometheus/node_exporter) on `:9100` (managed by the `node-exporter-mac` role), scraped by cluster Prometheus via the `mac-nodes` ServiceMonitor (`apps/monitoring/external-mac-nodes.yaml`, every 30s). mac-studio additionally exports Apple-Silicon GPU/ANE/CPU power via the powermetrics textfile sampler.

```bash
curl -s http://192.168.1.4:9100/metrics | head -5   # Mac Studio
curl -s http://192.168.1.7:9100/metrics | head -5   # Mac Mini
```

| Dashboard | URL |
|-----------|-----|
| Grafana (Node Exporter Full) | Import dashboard ID `1860`, filter by instance |
| Home Assistant | Homelab dashboard -> Mac Machines section |

## Troubleshooting

### MLX not serving (mac-studio)
```bash
ssh mac-studio "launchctl kickstart -k gui/$(id -u)/com.mlx-server"   # restart the LaunchAgent
ssh mac-studio "tail -50 /tmp/mlx-server.log"
```

### Runner offline after reboot (mac-mini)
```bash
ssh m1 "~/actions-runner/svc.sh status && ~/actions-runner/svc.sh start"
tail -f ~/actions-runner/_diag/Runner_*.log   # jobs not picked up
```

### Can't SSH after reboot
Likely a `SetEnv` issue in SSH config: `ssh -F /dev/null kennethblack@192.168.1.7`.

### Build fails with code signing error (mac-mini)
```bash
security unlock-keychain ~/Library/Keychains/login.keychain-db
```

## Maintenance

- **Homebrew updates:** `brew update && brew upgrade`
- **Runner updates (mac-mini):** auto-updates when GitHub releases a new version.
- **Xcode (mac-mini only):** install from App Store, then `sudo xcode-select -s /Applications/Xcode.app`. mac-studio intentionally has no Xcode (CLT only).
- **Runner health:** `https://github.com/BlackNBrownStudios/platform/settings/actions/runners`
