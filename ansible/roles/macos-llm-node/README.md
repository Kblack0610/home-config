# macos-llm-node

Turns a Mac into a **dedicated LLM inference node**. Bound to `mac-studio` (M3 Ultra, 512 GB) in the 2026-07-18 role split, alongside `launchd-mlx-server` (which owns MLX itself) and `launchd-powermetrics-textfile`. This role owns only the *specialization*: strip CI/build bloat, apply safe OS debloat, and persist Apple Silicon LLM tuning.

## What it does

1. **Removes CI/build Homebrew formulae** — `node`, `node@20`, `pnpm`, `yarn`, `cocoapods`, `openjdk@17` (`state: absent`, idempotent).
2. **Removes filesystem bloat** (each behind a default-false safety var):
   - `Xcode.app` — MLX only needs the Command Line Tools; the role asserts the CLT is present, then repoints `xcode-select` at it.
   - `~/dev/bnb/platform` — the CI checkout. **Hard-fails if it has uncommitted OR unpushed work** before deleting.
   - `~/actions-runner` — only after the runner is torn down (`github-actions-runner-mac` in `absent` mode).
3. **Applies reversible OS debloat** — Spotlight index off, Time Machine auto-backup off, Siri/dictation off, Apple analytics submission off, Power Nap + App Nap off. Each is read-then-write idempotent.
4. **Persists Apple Silicon LLM tuning** — installs a root LaunchDaemon (`com.kblab.llm-sysctl`, `RunAtLoad`) that re-applies `iogpu.wired_limit_mb=491520` (480 GB of 512 GB) at every boot, and applies it live immediately. Raising this ceiling lets large MLX models wire more unified memory than the ~75% default policy allows.

## Safety model

- **Destructive removals are default-false.** A plain run removes brew formulae + flips OS toggles but never deletes Xcode / checkouts / the runner dir; the `mac-studio` play opts in explicitly. Always `--check --diff` first.
- **Only user-space + supported knobs.** The role only flips `defaults`/`mdutil`/`tmutil`/`pmset`/`sysctl` and boots out **user** (`gui/<uid>`) agents. It NEVER disables SIP and never touches SIP-protected `/System/Library` daemons (`mDNSResponder`, `opendirectoryd`, `sshd`, `WindowServer`, `configd`, ...), which would break SSH/login/Metal.
- **`macos-baseline` still runs on this host** (shared play) and re-asserts the workstation SSH key + power policy every apply — the lockout safety net.

## Tuning value (`iogpu.wired_limit_mb`)

`491520` MB = 480 GB, leaving a 32 GB OS reserve on the 512 GB M3 Ultra. **Never approach the full 512 GB** — starving macOS of wired memory hard-hangs the box. Set `macos_llm_node_sysctls: {}` to disable tuning (the LaunchDaemon still installs but is a no-op). Watch Activity Monitor memory pressure after changing it.

## Unapply (reverse everything)

```bash
# Brew tooling
brew install node node@20 pnpm yarn cocoapods openjdk@17
# OS toggles
sudo mdutil -a -i on
sudo tmutil enable
defaults write com.apple.assistant.support "Assistant Enabled" -bool true
sudo defaults write "/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist" AutoSubmit -bool true
sudo pmset -a powernap 1
defaults delete NSGlobalDomain NSAppSleepDisabled
# LLM tuning
sudo launchctl bootout system/com.kblab.llm-sysctl
sudo rm /Library/LaunchDaemons/com.kblab.llm-sysctl.plist   # reverts to Apple default next reboot
sudo sysctl -w iogpu.wired_limit_mb=0                       # or apply the default live now
# xcode-select (only if Xcode was removed and later reinstalled)
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
```

Per-toggle reverses are also inline in each task as `# REVERSIBLE:` comments.

## Binding in site.yml

Bound in the `Mac Studio LLM node (debloat + tuning)` play (`hosts: mac-studio`), which runs the `github-actions-runner-mac` role in `absent` mode first, then this role, then the existing MLX + powermetrics plays. Destructive vars (`macos_llm_node_remove_xcode`, `macos_llm_node_remove_platform_checkout`, `macos_llm_node_remove_runner_dir`) are enabled there once `--check --diff` is verified. `-K` is needed (become for pmset/sysctl/Xcode removal).

## Prerequisites (not owned here)

- **Headless GPU:** attach a cheap **1080p** HDMI dummy plug and reboot once — with no display macOS gives only a low-res virtual framebuffer and can throttle Metal init.
- **Unattended recovery:** FileVault OFF + auto-login ON (asserted by `macos-baseline`). macOS 26 Tahoe re-enables FileVault by default; if `fdesetup status` shows On, `sudo fdesetup disable` + reboot before trusting recovery.
- **MLX session:** MLX runs as a per-user LaunchAgent (`launchd-mlx-server`) because Metal needs a logged-in WindowServer session — not a LaunchDaemon.
