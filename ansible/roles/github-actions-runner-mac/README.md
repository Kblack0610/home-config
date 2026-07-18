# github-actions-runner-mac

Registers a GitHub Actions self-hosted runner on macOS (Apple Silicon) and installs it as a per-user LaunchAgent via `svc.sh install`.

Retires the manual flow at `BlackNBrownStudios/platform/tools/{setup-mac-runner,register-mac-runner}.sh` for the registration half. Build-tool installation (node@20, pnpm, cocoapods, Java 17) remains with `setup-mac-runner.sh` in the platform repo until a follow-up role picks it up.

## What it does

1. Obtains a short-lived registration token (see **Token source** below).
2. Downloads and extracts the pinned `actions-runner-osx-arm64-<ver>.tar.gz` into `~/actions-runner`.
3. Optionally self-heals a stale registration (see **Re-registration** below).
4. Runs `./config.sh --unattended` (idempotent via `.runner` marker).
5. Runs `./svc.sh install` and `./svc.sh start` — the runner becomes a LaunchAgent at `~/Library/LaunchAgents/actions.runner.<owner>-<repo>.<name>.plist`.

## Token source

`gh_runner_mac_token_source` selects how the registration token is minted:

- **`gh`** (default) — minted on the **control node** via the already-authenticated `gh` CLI (`gh api ... /registration-token`). No standing secret in the repo; right for workstation applies. Requires `gh` logged in with `repo` scope on the target repo.
- **`pat`** — GitHub REST API with `vault_github_pat`. For the in-cluster ansible-runner CronJob, which has no `gh` binary. Set a real PAT (scope `repo`) in `group_vars/macos_hosts/vault.yml`.

## Re-registration

The `config.sh` step is guarded by the `.runner` marker, so it is a no-op once a runner is registered. If the local marker is stale (GitHub already dropped the runner), pass `-e gh_runner_mac_force_reregister=true`: the role stops + uninstalls the LaunchAgent and removes the marker before reconfiguring. Default `false` keeps steady-state drift-checks idempotent.

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

**Bound** in the `macOS baseline + monitoring` play (`hosts: macos_hosts`), alongside `macos-baseline`. Apply with the `mac` or `runner` tag. First recovery apply against the current orphaned boxes needs `-e gh_runner_mac_force_reregister=true`; review `--check --diff` first. The runner/authorized_keys tasks run as the login user, so `-K` is only needed for `macos-baseline`'s pmset tasks.

## Notes

- The runner binary auto-updates in place at runtime. The version default is only consulted on fresh install.
- To reset: `~/actions-runner/svc.sh uninstall && rm -rf ~/actions-runner && <remove runner in GitHub UI>`, then re-run this role.
