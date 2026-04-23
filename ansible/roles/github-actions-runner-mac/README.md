# github-actions-runner-mac

Registers a GitHub Actions self-hosted runner on macOS (Apple Silicon) and installs it as a per-user LaunchAgent via `svc.sh install`.

Retires the manual flow at `BlackNBrownStudios/platform/tools/{setup-mac-runner,register-mac-runner}.sh` for the registration half. Build-tool installation (node@20, pnpm, cocoapods, Java 17) remains with `setup-mac-runner.sh` in the platform repo until a follow-up role picks it up.

## What it does

1. Asserts `vault_github_pat` is set and not the placeholder (same secret used by the Linux role).
2. Downloads and extracts the pinned `actions-runner-osx-arm64-<ver>.tar.gz` into `~/actions-runner`.
3. Fetches a short-lived registration token from the GitHub REST API using the PAT.
4. Runs `./config.sh --unattended` (idempotent via `.runner` marker).
5. Runs `./svc.sh install` and `./svc.sh start` — the runner becomes a LaunchAgent at `~/Library/LaunchAgents/actions.runner.<owner>-<repo>.<name>.plist`.

## Required variables

| Variable | Where | Notes |
|----------|-------|-------|
| `vault_github_pat` | `group_vars/macos_hosts/vault.yml` | PAT with `repo` scope. Same secret the Linux role uses — if you share the vault across groups, define it once. |

All other variables have defaults in `defaults/main.yml`.

## Why LaunchAgent and not LaunchDaemon

GUI/keychain access on macOS requires the runner to be in a user session. Mobile builds sign IPAs against the login user's keychain. A LaunchDaemon (root-scoped) cannot unlock the login keychain. The existing `register-mac-runner.sh` uses LaunchAgent scope too.

## Verify

On the Mac:

```bash
~/actions-runner/svc.sh status
launchctl list | grep actions.runner
```

In GitHub: `https://github.com/BlackNBrownStudios/platform/settings/actions/runners` — the runner should appear online with labels `self-hosted, macOS, arm64`.

## Binding in site.yml

**Authored but NOT bound.** First apply against a live Mac re-registers the runner (the `--replace` flag will evict an existing runner with the same name). Don't run unattended — review the `--check --diff` output first.

## Notes

- The runner binary auto-updates in place at runtime. The version default is only consulted on fresh install.
- To reset: `~/actions-runner/svc.sh uninstall && rm -rf ~/actions-runner && <remove runner in GitHub UI>`, then re-run this role.
