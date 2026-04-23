# brew-common

Installs Homebrew (if missing) and a small baseline of CLI packages on macOS. Designed to be a prerequisite for every other macOS role (`node-exporter-mac`, `launchd-mlx-services`, `github-actions-runner-mac`, `ollama`).

## What it does

1. Checks for `/opt/homebrew/bin/brew` (override via `brew_prefix` for Intel Macs).
2. If missing, downloads the upstream installer and runs it with `NONINTERACTIVE=1`.
3. Installs `brew_common_packages` (default: `git curl wget jq coreutils`).
4. Installs `brew_extra_packages` (empty by default, extend in host_vars).

## Deliberately out of scope

- **macOS system defaults** (sleep, FileVault, auto-login, key repeat, Spotlight shortcut). These are blast-radius-sensitive and belong in a follow-up `macos-defaults` role. The current manual setup steps at `docs/mac-machines.md:111-122` stay authoritative until then.
- **Dotfiles / SSH key propagation.** The existing `~/.dotfiles/.local/src/installation_scripts/install.sh` does this at Mac bootstrap time.
- **Per-user packages (casks, GUI apps).** This role runs as the Ansible SSH user and installs formulae only. Casks need sudo + user-interactive prompts and don't belong here.

## Typical extension

`group_vars/macos_hosts/main.yml`:

```yaml
brew_extra_packages:
  - neovim
  - gh
  - ripgrep
  - fd
```

`host_vars/mac-studio.yml` (Intel host example would override `brew_prefix`):

```yaml
brew_extra_packages:
  - ollama   # but use the dedicated `ollama` role — this is just an example
```

## Verify

```bash
brew --version
brew list | grep -E '^(git|jq|coreutils)$'
```
