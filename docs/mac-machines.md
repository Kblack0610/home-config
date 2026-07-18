# Mac Machines Setup Guide

Dedicated macOS machines on the home LAN for iOS builds, Expo, and GitHub Actions CI/CD.

## Inventory

| Machine | Chip | RAM | IP | Hostname | Role |
|---------|------|-----|-----|----------|------|
| Mac Studio | M3 Ultra | 512 GB | 192.168.1.4 | mac-studio | LLM inference (MLX), iOS builds, GitHub Actions runner |
| Mac Mini | M1 | 16 GB | 192.168.1.7 | pc-home-m1-mini | iOS builds, Expo, GitHub Actions runner |

## Purpose

These machines run **native macOS workloads** that can't run in containers or on Linux:

- **Xcode / iOS builds** - compile, sign, and archive iOS apps
- **Expo prebuild** - generate native iOS/Android projects
- **CocoaPods** - iOS dependency management
- **TestFlight submission** - upload IPA to App Store Connect
- **GitHub Actions self-hosted runners** - CI/CD for mobile builds

They are **not** part of the K3s cluster (K3s is Linux-only). The Pi cluster handles containerized workloads; Macs handle Apple-native workloads.

## LLM Inference (Mac Studio)

The Mac Studio runs three `mlx_lm.server` instances as launchd services for local LLM inference:

| Service | Model | Port | Launchd Label |
|---------|-------|------|---------------|
| Code | mlx-community/Qwen3-Coder-Next-4bit | 8080 | com.mlx-lm.code |
| Smart | mlx-community/Qwen3-235B-A22B-4bit-DWQ | 8081 | com.mlx-lm.smart |
| Reasoning | mlx-community/DeepSeek-R1-Distill-Qwen-32B-MLX-4Bit | 8082 | com.mlx-lm.reasoning |

- **Python venv:** `~/mlx-env`
- **Logs:** `/tmp/mlx-lm-{code,smart,reasoning}.log`

### Management

```bash
# Stop/start a service
launchctl stop com.mlx-lm.code
launchctl start com.mlx-lm.code

# Check all MLX services
launchctl list | grep mlx

# View logs
tail -f /tmp/mlx-lm-code.log

# Verify API
curl http://192.168.1.4:8080/v1/models
```

## Initial Setup (New Mac)

### Prerequisites

- macOS with Xcode installed from App Store
- Static IP assigned (via router DHCP reservation or manual)
- SSH entry added to workstation `~/.ssh/config`

### 1. Bootstrap SSH Access

From the new Mac's terminal:
```bash
# Clone dotfiles
git clone https://github.com/kblack0610/.dotfiles.git ~/.dotfiles

# Run the universal installer (detects macOS, installs everything)
cd ~/.dotfiles/.local/src/installation_scripts
bash install.sh
```

This installs Homebrew, dev tools, applies macOS defaults (auto-login, no sleep, disable FileVault), and adds the workstation's SSH key to `~/.ssh/authorized_keys`.

### 2. Install build dependencies

On the new Mac (still native, one-time — installs node@20, pnpm, cocoapods, Java 17, etc. that Ansible does not yet manage):
```bash
git clone https://github.com/BlackNBrownStudios/platform.git ~/dev/bnb/platform
cd ~/dev/bnb/platform
./tools/setup-mac-runner.sh   # build tools only; do NOT register the runner here
```

### 3. Register the runner + persistence via Ansible (from the workstation)

Everything below the build tools is Ansible-managed. Add the host to `ansible/inventory.yml` under `macos_hosts`, then from the workstation:

```bash
cd ~/dev/home/home-config/ansible
export ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass

# Dry-run first (per role README). -K prompts for the Mac's sudo password
# (needed by the pmset power tasks; no NOPASSWD sudo on these boxes).
ansible-playbook playbooks/site.yml --limit <host> --tags mac -K --check --diff

# Apply. On a fresh box the runner registers cleanly; on a box with a stale
# orphaned registration, add force_reregister to self-heal it.
ansible-playbook playbooks/site.yml --limit <host> --tags mac -K \
  -e gh_runner_mac_force_reregister=true
```

This binds two roles (`ansible/playbooks/site.yml`, `hosts: macos_hosts`):

- **`macos-baseline`** — power policy (`pmset autorestart 1` + no-sleep), syncs the workstation SSH key into `authorized_keys` (so you can't get locked out), and asserts auto-login is on + FileVault off.
- **`github-actions-runner-mac`** — downloads/registers the runner as a per-user LaunchAgent. The registration token is minted on the workstation via the already-authenticated `gh` CLI (`gh_runner_mac_token_source: gh`) — no PAT stored in the repo.

Verify:
```bash
gh api repos/BlackNBrownStudios/platform/actions/runners --jq '.runners[]|{name,status}'  # both online
ssh <host> "launchctl list | grep actions.runner"   # numeric PID, not '-'
ssh <host> "pmset -g | grep autorestart"            # autorestart 1
```

## Power / persistence (survive a power failure)

The load-bearing setting is `pmset autorestart 1`: after a power cut the Mac boots itself, auto-login reaches a logged-in session, and the runner's LaunchAgent (`RunAtLoad`) brings the runner back online — no human needed. `macos-baseline` sets and asserts this; `autorestart 0` is why both Macs previously stayed dark after outages (found 2026-07-17). FileVault MUST stay off and auto-login on, or the boot stops at the login window and nothing recovers.

## Why not MDM

Considered and rejected. MDM's unique capability is zero-touch DEP/ADE enrollment, which requires Apple Business Manager (DUNS + business verification). Everything we actually need — power policy, auto-login, runner, SSH keys — is host-OS config that Ansible already owns per this repo's deployment model (`docs/gitops.md`). Without ABM, MDM only offers user-removable profiles, which is weaker than Ansible and adds a server to run. It also does not solve power-failure recovery; that is purely `pmset autorestart`.

## macOS Defaults

Auto-login, no-sleep, and FileVault-off are applied by the install script; **`pmset autorestart` and no-sleep are enforced idempotently by the `macos-baseline` Ansible role** (source of truth for power policy).

| Setting | Value | Why | Owner |
|---------|-------|-----|-------|
| Auto-restart after power failure | Enabled | Unattended boot after outage | macos-baseline (pmset) |
| Sleep / disk sleep / display sleep | Disabled | Headless server, must stay awake | macos-baseline (pmset) |
| Wake-on-LAN (womp) | Enabled | Remote wake | macos-baseline (pmset) |
| FileVault | Disabled | Allows auto-login on restart | install script (asserted by macos-baseline) |
| Auto-login | Enabled (current user) | Unattended restarts after power outage | install script (asserted by macos-baseline) |
| Key repeat / Spotlight shortcut | Fast / Disabled | Dev preference | install script |

## Workflows

| Workflow | File | Trigger | Runner |
|----------|------|---------|--------|
| Mobile Local Build | `mobile-local-build.yml` | PRs to main (mobile changes) | `[self-hosted, macOS]` |
| Mobile Local Release | `mobile-local-release.yml` | Git tags `*-mobile-v*.*.*` | `[self-hosted, macOS]` |
| Deploy Mobile (backup) | `deploy-mobile.yml` | Manual | `ubuntu-latest` (EAS Cloud) |

## Required GitHub Secrets

### iOS
| Secret | Purpose |
|--------|---------|
| `KEYCHAIN_PASSWORD` | Mac login password for keychain unlock during code signing |
| `APPLE_ID` | Apple ID for TestFlight upload |
| `EXPO_APPLE_PASSWORD` | App-specific password for Apple ID |
| `APPLE_TEAM_ID` | Apple Developer Team ID |

### Android
| Secret | Purpose |
|--------|---------|
| `ANDROID_KEYSTORE_BASE64` | Base64-encoded keystore file |
| `ANDROID_KEYSTORE_PASSWORD` | Keystore password |
| `ANDROID_KEY_ALIAS` | Key alias name |
| `ANDROID_KEY_PASSWORD` | Key password |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_KEY` | Google Play service account JSON |

## SSH Access

From the workstation:
```bash
ssh m1            # Mac Mini M1
ssh mac-studio    # Mac Studio M3 Ultra
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

## Troubleshooting

### Runner offline after reboot
```bash
ssh m1
~/actions-runner/svc.sh status
~/actions-runner/svc.sh start
```

### Runner not picking up jobs
```bash
# Check logs
tail -f ~/actions-runner/_diag/Runner_*.log

# Verify environment
cat ~/actions-runner/.env
```

### Can't SSH after reboot
Likely `SetEnv` issue in SSH config. Use:
```bash
ssh -F /dev/null kennethblack@192.168.1.7
```

### Build fails with code signing error
```bash
# Unlock keychain manually
security unlock-keychain ~/Library/Keychains/login.keychain-db
```

## Monitoring

Both Macs run [node_exporter](https://github.com/prometheus/node_exporter) via Homebrew, scraped by Prometheus in the K3s cluster.

### Stack

```
node_exporter (port 9100) → Prometheus (ServiceMonitor) → Grafana / Home Assistant
```

### Install / Verify

```bash
# Install (already in install_mac.sh)
brew install node_exporter
brew services start node_exporter

# Verify
curl -s http://192.168.1.4:9100/metrics | head -5   # Mac Studio
curl -s http://192.168.1.7:9100/metrics | head -5   # Mac Mini
```

### Dashboards

| Dashboard | URL |
|-----------|-----|
| Mac Studio metrics | http://192.168.1.4:9100/metrics |
| Mac Mini metrics | http://192.168.1.7:9100/metrics |
| Grafana (Node Exporter Full) | Import dashboard ID `1860` and filter by instance |
| Home Assistant | Homelab dashboard → Mac Machines section |

### K8s Resources

- `apps/monitoring/external-mac-nodes.yaml` — headless Service + Endpoints + ServiceMonitor
- Prometheus scrapes both IPs every 30s via the `mac-nodes` ServiceMonitor

## Maintenance

- **Xcode updates**: Install from App Store, then `sudo xcode-select -s /Applications/Xcode.app`
- **Homebrew updates**: `brew update && brew upgrade`
- **Runner updates**: Runner auto-updates when GitHub releases new versions
- **Check runner health**: `https://github.com/BlackNBrownStudios/platform/settings/actions/runners`
