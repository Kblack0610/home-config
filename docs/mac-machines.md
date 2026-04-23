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

### 2. Set Up as GitHub Actions Runner

From the new Mac:
```bash
# Clone the platform repo
git clone https://github.com/BlackNBrownStudios/platform.git ~/dev/bnb/platform
cd ~/dev/bnb/platform

# Install build dependencies (node@20, pnpm, cocoapods, Java 17, etc.)
./tools/setup-mac-runner.sh

# Register runner with GitHub (two options):

# Option A: One-time token from GitHub UI
# Get token from: https://github.com/BlackNBrownStudios/platform/settings/actions/runners/new
./tools/register-mac-runner.sh <REGISTRATION_TOKEN>

# Option B: PAT-based (can be automated)
export GITHUB_PAT=ghp_xxxxxxxxxxxx
./tools/register-mac-runner.sh --pat
```

The runner installs as a LaunchDaemon and auto-starts on boot.

### 3. Verify

```bash
# Check runner service
~/actions-runner/svc.sh status

# Check on GitHub
# https://github.com/BlackNBrownStudios/platform/settings/actions/runners
```

## macOS Defaults (Applied by install script)

| Setting | Value | Why |
|---------|-------|-----|
| Sleep | Disabled (all power sources) | Headless server, must stay awake |
| Display sleep | Disabled | Same |
| Hibernate | Disabled | Fast wake if sleep triggers |
| FileVault | Disabled | Allows auto-login on restart |
| Auto-login | Enabled (current user) | Unattended restarts after power outage |
| Key repeat | Fast | Dev preference |
| Spotlight shortcut | Disabled | Frees Cmd+Space for Raycast |

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
