# Mac Machines Setup Guide

One Apple-Silicon Mac on the home LAN is managed here, **mac-mini** (M1): the **sole self-hosted mobile-CI runner** (iOS/Android builds, TestFlight/Play upload) and the agent machine. The Mac Studio (M3 Ultra) is in the Unity CI pool only, until its sale (see [Unity CI pool](#unity-ci-pool-mac-studio-until-it-is-sold)).

The Macs are **not** part of the K3s cluster (K3s is Linux-only). The Pi cluster handles containerized workloads; the Mac handles Apple-native workloads.

## Inventory

| Machine | Chip | RAM | IP | inventory host | scutil hostname | Role |
|---------|------|-----|-----|----------------|-----------------|------|
| Mac Mini | M1 | 16 GB | 192.168.1.7 | `mac-mini` | pc-home-m1-mini | **CI runner** |

The baseline play runs on `macos_hosts`, the runner play on `mac-mini` (`ansible/playbooks/site.yml`). No mobile workflow pins a runner by name, only by the `[self-hosted, macOS, arm64]` label set.

## Headless prerequisites (or it won't recover unattended)

- **FileVault OFF + auto-login ON** - the runners are per-user LaunchAgents and the agent desktop needs a logged-in WindowServer session, and auto-login requires FileVault off. `macos-baseline` asserts this. **macOS 26 Tahoe re-enables FileVault by default** (incl. on upgrades); if `fdesetup status` shows On, run `sudo fdesetup disable` (interactive: admin pw + a secure-token user) + reboot before trusting recovery.

## Role: CI runner (mac-mini)

The sole self-hosted runner for `BlackNBrownStudios/platform` mobile CI. Native macOS workloads that can't run in a container: Xcode/iOS builds, Expo prebuild, CocoaPods, TestFlight submission.

- **Runner:** `mac-mini-mac`, labels `[self-hosted, macOS, arm64]`, per-user LaunchAgent (`github-actions-runner-mac` role, `present` mode).
- **Second runner:** `m1-playground-mac`, labels `[self-hosted, macOS, ARM64, unity-mac, host-mac-mini]`, scoped to `unity-core-playground`, builds the Mac player. No play manages it. `m1` is only an ssh alias for this machine, and the `host-mac-mini` label is what makes the exporter and the infra board count it here: without it the runner shows as a host called `m1`. Add it with `gh api -X POST repos/BlackNBrownStudios/unity-core-playground/actions/runners/<id>/labels -f 'labels[]=host-mac-mini'`.
- **Unity player modules (iOS, Mac IL2CPP):** the mac-mini play adds each module in `unity_ci_mac_modules` to each editor in `unity_ci_mac_editors` (`unity-ci-host-mac` `tasks/modules.yml`, tag `unity-modules`, no sudo), from Unity's pkg because the Hub refuses modules for an editor it did not install, and fails first if Xcode is missing or its license is not accepted: `ansible-playbook playbooks/site.yml --limit mac-mini --tags unity-modules`. The Mac player builds with IL2CPP, so without that module the Mac release job fails at the Addressables step with "Unable to build with the current configuration".
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

## Unity CI pool on a Mac (none since 2026-11-10)

No Mac is in the Unity pool: the Mac Studio (M3 Ultra) ran 6 slots from 2026-10-29 until it was sold on 2026-11-10, and aws-burst-2 took its slots. The play and roles below stay for a future Mac; the platform repo's iOS runner stays on mac-mini.

- Play "Unity CI runner pool (macOS)" in `ansible/playbooks/site.yml`, group `unity_ci_mac_runners` in `ansible/inventory.yml` (`unity_ci_slots`). Roles: `unity-ci-host-mac` (Homebrew GNU tools, Unity Hub, the editor at the project's version) and `github-actions-runner-mac` once per slot (`scope: org`, home `~/actions-runner/unity-pool-<n>`).
- Labels are `unity, unity-native, host-<name>` (+ `unity-full`), registered with `--no-default-labels`: without it the slots also carry `self-hosted, macOS`, which takes the platform repo's iOS jobs and dodginballs' `[self-hosted, unity]` jobs. The playground runs the harness natively there (`UNITY_CI_NATIVE=1` in each slot's `.env`, see unity-core-playground `scripts/test/in-docker.sh`, and [runbooks/unity-ci-host.md](runbooks/unity-ci-host.md#the-mac-pool)).
- The Studio ran six slots. Eight ran the box at load 115-157 on 32 cores with 0% idle (2026-10-31), and PlayMode throughput was no better than at 6 (about 20-28 jobs an hour against about 37, small sample), so slots 7-8 were withheld from 2026-11-02 and removed on 2026-11-03. The governor cannot judge a Mac's load (no CPU pressure or sensors), so the slot count is the limit.
- Unity saves its license under `/Library/Application Support/Unity`, which needs sudo once: `ansible-playbook playbooks/site.yml --limit <mac> --tags unity-ci-sudo -K`.
- The slots are LaunchAgents, so macOS Local Network privacy blocks their non-Apple binaries (Homebrew git, git-lfs) from the LAN: a fetch from `git.kblab.me` fails in a few ms with "Couldn't connect to server" while it works over ssh. Only Apple's binaries, root, and ssh sessions are exempt (Apple TN3179); a LaunchDaemon with `UserName` was blocked too, and `launchctl kickstart -k` did not clear it on the Studio (2026-10-29). The playground fetches through the public host instead (its `scripts/setup/forge-git-env.sh`), so do not move the slots to LaunchDaemons for this.
- To retire a Mac: the retire steps in the play's header comment (`site.yml`), then take the host out of `unity_ci_mac_runners` and `RUNNER_ROSTER` and run `scripts/check-runner-roster.py`.

## Reprovision from bare metal

On the new Mac, run `git clone https://github.com/kblack0610/.dotfiles.git ~/.dotfiles && cd ~/.dotfiles/.local/src/installation_scripts && bash install.sh` (Homebrew, dev tools, macOS defaults, workstation SSH key). Then add/confirm the host in `ansible/inventory.yml` under `macos_hosts` and run from the workstation; secrets decrypt with the SOPS age key (become password for `-K` is rbw item `mac_fleet_password`).

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

The load-bearing setting is `pmset autorestart 1`: after a power cut the Mac boots itself, auto-login reaches a logged-in session, and the per-user LaunchAgent (`RunAtLoad`) brings the runners back online - no human needed. `macos-baseline` sets and asserts this; `autorestart 0` is why the Macs previously stayed dark after outages (found 2026-07-17). FileVault MUST stay off and auto-login on, or the boot stops at the login window and nothing recovers.

Proved on the mac-mini 2026-11-07 with `sudo -n shutdown -r now` and both runners idle, no touch: kernel up 22s after the command, ssh, console login and every Peekaboo grant by 39s, both runner listeners started at 52s, both runners online on GitHub by 94s. A true power cut is not yet proved: that needs someone to pull the plug.

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
| Screen lock and screensaver (mac-mini) | Off (`sysadminctl -screenLock off`, screensaver `idleTime` 0, `askForPassword` 0) | A locked session blocks the desktop and store-console tools; auto-login already gives a session to anyone at the Mac | screen lock: person, once; screensaver: macos-agent-tools sets both, and asserts the lock delay and that the console session is unlocked now (ioreg `CGSSessionScreenIsLocked`) |
| Passwordless sudo (mac-mini) | Blanket, `/etc/sudoers.d/agent-machine` | Agents need root unattended | macos-agent-tools |
| Spotlight index (mac-mini) | Off, index removed | It reached 51 GB; nothing here searches with it | macos-agent-tools |
| Automatic macOS install (mac-mini) | Off, critical and config-data updates stay on | An update reboots mid-job and can drop the privacy grants | macos-agent-tools |
| Homebrew Tailscale agent (mac-mini) | Stopped | It looped on needing root; the root daemon stays, logged out | macos-agent-tools |

Only user-space + supported knobs are touched (`defaults`/`mdutil`/`tmutil`/`pmset`/`sysctl`, and `bootout` of user agents). SIP-protected `/System/Library` daemons (`mDNSResponder`, `opendirectoryd`, `sshd`, `WindowServer`, ...) are NEVER touched — they break SSH/login/Metal and are SIP-revived anyway.

## SSH Access

```bash
ssh m1            # Mac Mini M1  (user kennethblack, 192.168.1.7)
```

SSH config (`~/.ssh/config`):
```
Host m1 mac-mini
    HostName 192.168.1.7
    User kennethblack
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

The mac-mini runs [node_exporter](https://github.com/prometheus/node_exporter) on `:9100` (managed by the `node-exporter-mac` role), scraped by cluster Prometheus via the `mac-nodes` ServiceMonitor (`apps/monitoring/external-mac-nodes.yaml`, every 30s).

```bash
curl -s http://192.168.1.7:9100/metrics | head -5   # Mac Mini
```

| Dashboard | URL |
|-----------|-----|
| Grafana (Node Exporter Full) | Import dashboard ID `1860`, filter by instance |
| Home Assistant | Homelab dashboard -> Mac Machines section |

## Troubleshooting

### Runner offline after reboot (mac-mini)
```bash
ssh m1 "~/actions-runner/svc.sh status && ~/actions-runner/svc.sh start"
tail -f ~/actions-runner/_diag/Runner_*.log   # jobs not picked up
```

### Runner logs "Conflict. Retrying until reconnected" after reboot
Expected after any unclean stop (`shutdown -r now`, power cut): GitHub still holds the old broker session, so the new listener gets HTTP 409 and retries every 30s until it is dropped. On 2026-11-07 it took one retry, about 30s. If it keeps going past a few minutes, use "Runner offline after reboot" above.

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

`caffeinate -u` alone wakes the display but does not unlock. Then run the `agent-tools` tag so it does not lock again; it fails while the session is still locked. The usual cause is the screensaver (`idleTime`) with `askForPassword` 1, which `sysadminctl -screenLock status` does not show. To see what locked it, `/usr/bin/log show --predicate 'process == "loginwindow" AND eventMessage CONTAINS "LockFrom"'` names the reason (2026-11-07: `kLWLockFromScreenSaverIdleLaunch`, with loginwindow reading `idleTime: 10800`).

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
- **Xcode:** install from App Store, then `sudo xcode-select -s /Applications/Xcode.app`.
- **Runner health:** `https://github.com/BlackNBrownStudios/platform/settings/actions/runners`
