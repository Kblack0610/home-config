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
| **Runs** | MLX server `com.mlx-server` :8090 (5 pinned + 5 on-demand candidates), Unity CI pool slots `mac-studio-unity-pool-1..6` (since 2026-10-29, until the sale), node_exporter :9100, powermetrics textfile sampler | GH Actions runner `mac-mini-mac`, node_exporter :9100, iOS toolchain (Xcode, CocoaPods, node/pnpm/yarn, JDK17) |
| **Removed** | platform GH runner `mac-studio-mac` (torn down + deregistered); brew `node@20`/`pnpm`/`yarn`/`cocoapods`/`openjdk@17`; `Xcode.app`; `~/actions-runner`; (optional) `~/dev/bnb/platform` | — |
| **Kept despite debloat** | `node` (used by `opencode`), `ruby` (used by `cocoapods`+`tmuxinator`), `node_exporter` | full toolchain |
| **Tuned** | `iogpu.wired_limit_mb=491520` (480 GB); Spotlight/Time Machine/Siri/analytics/Power+App Nap off | — |

The split is expressed as **per-host plays** in `ansible/playbooks/site.yml`. No mobile workflow pins a runner by name (only by the `[self-hosted, macOS, arm64]` label set), so demoting mac-studio is transparent to CI as long as mac-mini is online.

## Role: LLM inference node (mac-studio)

Runs a single `mlx-openai-server` (cubist38) process serving chat, vision, and embedding models from one OpenAI-compatible endpoint on `:8090`. (This replaced the retired three-service `com.mlx-lm.{code,smart,reasoning}` layout on 8080/8081/8082.)

Served (`mlx_models` in `roles/launchd-mlx-server/defaults/main.yml`):

- **Pinned (~73 GB, always resident):** `code` (Qwen3-Coder-Next-4bit), `reasoning` (Qwen3.6-35B-A3B-4bit), `fast` (Qwen3-4B, 4bit + 8bit), `embedding` (modernbert-embed-base-4bit).
- **on_demand (JIT-loaded, idle-evicted — dual-tier A/B candidates, 2026-07):** `code` (Qwen3-Coder-Next-6bit), `code` (Qwen3-Coder-480B-A35B-4bit, ~250 GB), `reasoning` (Qwen3.5-397B-A17B-4bit, ~214 GB), `vlm` (Qwen3-VL-30B-A3B-8bit — needs `mlx-vlm>=0.6.5`), `embedding` (Qwen3-Embedding-0.6B).

Each local model has a matching route in `apps/litellm/config.yaml`; the `router_settings.model_group_alias` block there toggles each category (`code`/`reasoning`/`fast`/`vlm`/`embedding`) between the local box and the paid Lazer/Gemini tier in one line. The 1T Kimi K2 flagship is a manual "flagship mode" (sole-resident), not a live entry — see `roles/launchd-mlx-server/README.md`.

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
- **Second runner:** `m1-playground-mac`, labels `[self-hosted, macOS, ARM64, unity-mac, host-mac-mini]`, scoped to `unity-core-playground`, builds the Mac player. No play manages it. `m1` is only an ssh alias for this machine, and the `host-mac-mini` label is what makes the exporter and the infra board count it here: without it the runner shows as a host called `m1`. Add it with `gh api -X POST repos/BlackNBrownStudios/unity-core-playground/actions/runners/<id>/labels -f 'labels[]=host-mac-mini'`.
- **Unity iOS Build Support:** the mac-mini play adds it to each editor in `unity_ci_mac_editors` (`unity-ci-host-mac` `tasks/ios.yml`, tag `unity-ios`, no sudo), from Unity's pkg because the Hub refuses modules for an editor it did not install, and fails first if Xcode is missing or its license is not accepted: `ansible-playbook playbooks/site.yml --limit mac-mini --tags unity-ios`.
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

## Unity CI pool (mac-studio, until it is sold)

Since 2026-10-29 the Studio also runs 6 slots of the org-scoped Unity CI pool, beside MLX. This reverses part of the split above for as long as the box is here; the platform repo's iOS runner stays on mac-mini.

- Play "Unity CI runner pool (macOS)" in `ansible/playbooks/site.yml`, group `unity_ci_mac_runners` in `ansible/inventory.yml` (`unity_ci_slots`). Roles: `unity-ci-host-mac` (Homebrew GNU tools, Unity Hub, the editor at the project's version) and `github-actions-runner-mac` once per slot (`scope: org`, home `~/actions-runner/unity-pool-<n>`).
- Labels are `unity, unity-native, host-mac-studio` (+ `unity-full`), registered with `--no-default-labels`: without it the slots also carry `self-hosted, macOS`, which takes the platform repo's iOS jobs and dodginballs' `[self-hosted, unity]` jobs. The playground runs the harness natively there (`UNITY_CI_NATIVE=1` in each slot's `.env`, see unity-core-playground `scripts/test/in-docker.sh`, and [runbooks/unity-ci-host.md](runbooks/unity-ci-host.md#the-mac-pool)).
- Six slots, all taking pool jobs. Eight ran the box at load 115-157 on 32 cores with 0% idle (2026-10-31), and PlayMode throughput was no better than at 6 (about 20-28 jobs an hour against about 37, small sample), so slots 7-8 were withheld from 2026-11-02 and removed on 2026-11-03. The governor cannot judge a Mac's load (no CPU pressure or sensors), so the slot count is the limit.
- Unity saves its license under `/Library/Application Support/Unity`, which needs sudo once: `ansible-playbook playbooks/site.yml --limit mac-studio --tags unity-ci-sudo -K`.
- The slots are LaunchAgents, so macOS Local Network privacy blocks their non-Apple binaries (Homebrew git, git-lfs) from the LAN: a fetch from `git.kblab.me` fails in a few ms with "Couldn't connect to server" while it works over ssh. Only Apple's binaries, root, and ssh sessions are exempt (Apple TN3179); a LaunchDaemon with `UserName` was blocked too, and `launchctl kickstart -k` did not clear it on the Studio (2026-10-29). The playground fetches through the public host instead (its `scripts/setup/forge-git-env.sh`), so do not move the slots to LaunchDaemons for this.
- Before the sale: follow the retire steps in the play's header comment (`site.yml`): uninstall each slot with its `svc.sh`, delete the runners in the org settings, take the host out of `unity_ci_mac_runners` and `RUNNER_ROSTER`, and run `scripts/check-runner-roster.py`.

## Reprovision from bare metal

Both flows start the same: on the new Mac, `git clone https://github.com/kblack0610/.dotfiles.git ~/.dotfiles && cd ~/.dotfiles/.local/src/installation_scripts && bash install.sh` (Homebrew, dev tools, macOS defaults, workstation SSH key). Then add/confirm the host in `ansible/inventory.yml` under `macos_hosts` and run from the workstation; secrets decrypt with the SOPS age key (become password for `-K` is rbw item `mac_fleet_password`).

### LLM node (mac-studio)

```bash
# 1. MLX venv (native, one-time): python3.12 -m venv ~/mlx-env && ~/mlx-env/bin/pip install mlx-openai-server
# 2. Confirm FileVault off + auto-login on + a 1080p display/dummy-plug attached.
cd ~/dev/home/home-config/ansible
ansible-playbook playbooks/site.yml --limit mac-studio --tags mac -K --check --diff   # review first
ansible-playbook playbooks/site.yml --limit mac-studio --tags mac -K                  # apply
# Verify:
gh api repos/BlackNBrownStudios/platform/actions/runners --jq '.runners[]|.name'  # NO mac-studio-mac (the org pool's mac-studio-unity-pool-N are separate)
ssh mac-studio "curl -s http://127.0.0.1:8090/v1/models | head -c 80"             # MLX serving
ssh mac-studio "sysctl iogpu.wired_limit_mb"                                       # 491520
```

From a worktree, Ansible finds no collections (`collections_path` is the git-ignored `./collections`), so a run fails on `community.general.pacman`: prefix it with `ANSIBLE_COLLECTIONS_PATH=~/dev/home/home-config/ansible/collections`.

The mac-studio play sets the destructive removal vars (`macos_llm_node_remove_xcode`, `..._platform_checkout`) true (`..._runner_dir` is off while the Unity pool slots live under `~/actions-runner`) — always `--check --diff` first, and note the checkout removal HARD-FAILS if `~/dev/bnb/platform` has uncommitted/unpushed work.

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
| Screen lock and screensaver (mac-mini) | Off (`sysadminctl -screenLock off`, screensaver `idleTime` 0, `askForPassword` 0) | A locked session blocks the desktop and store-console tools; auto-login already gives a session to anyone at the Mac | screen lock: person, once; screensaver: macos-agent-tools sets both and asserts the lock |
| Passwordless sudo (mac-mini) | Blanket, `/etc/sudoers.d/agent-machine` | Agents need root unattended; mac-studio keeps its password | macos-agent-tools |
| Spotlight index (mac-mini) | Off, index removed | It reached 51 GB; nothing here searches with it | macos-agent-tools |
| Automatic macOS install (mac-mini) | Off, critical and config-data updates stay on | An update reboots mid-job and can drop the privacy grants | macos-agent-tools |
| Homebrew Tailscale agent (mac-mini) | Stopped | It looped on needing root; the root daemon stays, logged out | macos-agent-tools |
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

## Driving it from an agent

The mac-mini is the agent machine: set aside for agents to have full control of every action and operation on it, unattended. Three ways in, from the workstation:

| Need | Use |
|---|---|
| Shell, files, builds (`xcodebuild`, Unity, brew), launching apps (`open -a`) | `ssh mac-mini '<command>'` |
| The desktop: screenshots, clicking by accessibility label, typing, menus, dialogs, windows, any app | the on-demand MCP `agent-desktop`: `claude-mcp agent-desktop` ([Peekaboo](https://github.com/steipete/Peekaboo), pinned in dotfiles-private `.claude/mcp-optional/agent-desktop.json` and in `macos-agent-tools` defaults) |
| Web pages in the signed-in Google Chrome (store consoles) | the on-demand MCP `stores-browser` (Playwright Extension; the `store-console` skill) |

`sudo` needs no password on the mini, and a plain `ssh mac-mini 'brew ...'` finds Homebrew through `~/.zshenv`. Secrets come from rbw on the workstation, piped over ssh, never typed into a file: `mac_fleet_password` is the Mac's login and keychain password. The dotfiles-private `agent-machine` skill is how an agent works here, including driving Peekaboo from a plain ssh shell.

The desktop needs a one-time grant by a person, because macOS (TCC) lets only a click in System Settings allow screen capture and synthetic input, and refuses synthetic clicks on those switches. macOS attributes an ssh session's requests to `/usr/libexec/sshd-keygen-wrapper`, so that is what gets the grant: System Settings > Privacy & Security > Accessibility, and > Screen & System Audio Recording: + > Cmd+Shift+G > `/usr/libexec/sshd-keygen-wrapper` > switch it on. Click Allow on any "sshd-keygen-wrapper wants to control ..." prompt. `ansible-playbook playbooks/site.yml --limit mac-mini --tags agent-tools` (role `macos-agent-tools`) checks the grants and the screen lock and prints these steps when one is missing. The CI runner is a LaunchAgent, not sshd, so CI jobs do not get the grant.

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

### An agent cannot see or click the desktop (mac-mini)
`peekaboo permissions status` (or the `agent-tools` tag) shows which grant is off. macOS drops a grant after some updates and can re-ask for Screen Recording; redo the one-time grant in [Driving it from an agent](#driving-it-from-an-agent). A screenshot of the login window means the screen locked: the screen lock must be off (`sysadminctl -screenLock status`).

### The screen is locked (mac-mini)

Agents unlock it themselves; it needs no person. A locked session looks like the dimmed wallpaper in a screenshot, and `ssh -T mac-mini 'ioreg -n Root -d1 -a | grep -A1 CGSSessionScreenIsLocked'` prints `<true/>` (no output means unlocked). Browser and Peekaboo calls block on it without an error, so check this first when a desktop or store-console step hangs. Unlock with the login password from rbw, never written to a file or a command line:

```bash
rbw get mac_fleet_password | ssh -T mac-mini 'read -r v; caffeinate -u -t 2; sleep 1
  osascript -e "tell application \"System Events\" to key code 49"; sleep 1
  osascript -e "on run argv" -e "tell application \"System Events\" to keystroke (item 1 of argv)" -e "delay 0.3" -e "tell application \"System Events\" to key code 36" -e "end run" "$v"; unset v'
```

`caffeinate -u` alone wakes the display but does not unlock. Then run the `agent-tools` tag so it does not lock again: the usual cause is the screensaver (`idleTime`) with `askForPassword` 1, which `sysadminctl -screenLock status` does not show.

### A macOS prompt asks to "bypass the system private window picker" (mac-mini)

"com.apple.sshd-session is requesting to bypass the system private window picker and directly access your screen and audio" follows the Screen Recording grant and comes back every 30 days, on the date in `kScreenCapturePrivacyHintDate` for `/usr/libexec/sshd-keygen-wrapper` in `~/Library/Group Containers/group.com.apple.replayd/ScreenCaptureApprovals.plist`. That file only reports the clock: edits to the hint date, the last-alerted date or the 30-day policy were all put back by the next capture on macOS 26.5.2, even with `replayd` stopped (tested 2026-11-06). Only a person's click resets it. It covers the middle of the screen until someone at the Mac clicks Allow; agents do not click consent prompts.

### Chrome starts but shows no window (mac-mini)

On its first launch Chrome shows a Welcome dialog (default browser, usage statistics), and launched over ssh it can hang there with one process and no window. Quit it and launch it again from the desktop (Peekaboo `app launch "Google Chrome" --foreground`); untick both boxes and press OK.

### Build fails with code signing error (mac-mini)
```bash
security unlock-keychain ~/Library/Keychains/login.keychain-db
```

## Maintenance

- **Homebrew updates:** `brew update && brew upgrade`
- **Runner updates (mac-mini):** auto-updates when GitHub releases a new version.
- **Xcode (mac-mini only):** install from App Store, then `sudo xcode-select -s /Applications/Xcode.app`. mac-studio intentionally has no Xcode (CLT only).
- **Runner health:** `https://github.com/BlackNBrownStudios/platform/settings/actions/runners`
