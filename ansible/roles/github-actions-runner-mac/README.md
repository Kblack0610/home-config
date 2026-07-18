# github-actions-runner-mac

Manages the full lifecycle of a GitHub Actions self-hosted runner on macOS (Apple Silicon): registers + runs it as a per-user LaunchAgent, or tears it down and deregisters it. Which one is chosen by `gh_runner_mac_state` (see **State** below).

Retires the manual flow at `BlackNBrownStudios/platform/tools/{setup-mac-runner,register-mac-runner}.sh` for the registration half. Build-tool installation (node@20, pnpm, cocoapods, Java 17) remains with `setup-mac-runner.sh` in the platform repo until a follow-up role picks it up.

## State

`gh_runner_mac_state` selects the desired end-state on the host:

- **`present`** (default) — obtain a token, download/extract the pinned runner, `config.sh --unattended`, and `svc.sh install`/`start`. See **What it does (present)**.
- **`absent`** — stop + uninstall the LaunchAgent, mint a *removal* token, `config.sh remove` (deregister GitHub-side), and wipe local markers. Best-effort + idempotent: a clean box is a no-op. Bind the role in absent mode to **demote** a host from being a runner. `mac-studio` uses this (it became a dedicated LLM node 2026-07-18); `mac-mini` is the sole `present` runner.

## What it does (present)

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

Bound in **two** per-host plays (2026-07-18 role split):

- **`hosts: mac-mini`** (`present`, default) — the sole self-hosted mobile-CI runner. Apply with the `mac` or `runner` tag. A recovery apply against a stale/orphaned registration needs `-e gh_runner_mac_force_reregister=true`; review `--check --diff` first.
- **`hosts: mac-studio`** (`gh_runner_mac_state: absent`) — tears the runner down so the LLM node stops being a CI target. Runs before the `macos-llm-node` + MLX plays.

The runner tasks run as the login user (no `become`), so `-K` is only needed for `macos-baseline`'s pmset tasks in the shared baseline play.

## Notes

- The runner binary auto-updates in place at runtime. The version default is only consulted on fresh install.
- Manual reset: `~/actions-runner/svc.sh uninstall && rm -rf ~/actions-runner && <remove runner in GitHub UI>`, then re-run this role. The `absent` state does this for you (and deregisters GitHub-side).
