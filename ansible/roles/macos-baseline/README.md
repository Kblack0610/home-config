# macos-baseline

Host-OS persistence baseline for the headless Mac build/CI boxes (mac-studio, mac-mini). Makes a Mac survive a power failure and stay reachable, so it can act as an always-on GitHub Actions runner without a human in the loop. Pairs with `github-actions-runner-mac` (which installs the runner itself).

## What it does

1. **Power policy** (`pmset -a`, needs root): sets `autorestart 1` (boot after a power failure — the load-bearing setting), plus `sleep 0`, `disksleep 0`, `displaysleep 0`, `womp 1`. Idempotent: reads each current value and only writes when it differs.
2. **SSH key sync**: ensures the control-node's public key (`~/.ssh/id_ed25519.pub` by default) is in the Mac user's `authorized_keys` (`exclusive: false`). Prevents the lockout that hit mac-mini on 2026-07-17.
3. **Unattended-restart assertion** (read-only): verifies auto-login is enabled and FileVault is off. Both are required for a power-on to reach a logged-in session where the runner's per-user LaunchAgent fires. Fails the play with remediation steps rather than silently leaving a box that won't come back online.

## Why these two prerequisites are asserted, not set

Auto-login and FileVault are one-time, GUI-driven, security-sensitive choices (FileVault off is a deliberate trade-off for a headless server). The install script sets them; this role only checks them so drift is caught loudly.

## Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `macos_workstation_pubkey_path` | `{{ lookup('env','HOME') }}/.ssh/id_ed25519.pub` | Control-node pubkey to keep authorized. |
| `macos_power_settings` | see `defaults/main.yml` | List of `{key, value}` passed to `pmset -a`. |

## Requirements

- `ansible.posix` collection (for `authorized_key`).
- Interactive sudo (`-K` / `--ask-become-pass`): the Macs have no NOPASSWD sudo, and `pmset -a` needs root.

## Verify

```bash
ssh <host> "pmset -g | grep autorestart"          # -> autorestart 1
ssh <host> "grep -c <your-key-comment> ~/.ssh/authorized_keys"  # -> >= 1
```
