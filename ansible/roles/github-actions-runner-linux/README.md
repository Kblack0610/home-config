# github-actions-runner-linux

Registers a GitHub Actions self-hosted runner on a Linux host and installs it as a systemd service.

Mirrors `platform/tools/setup-mac-runner.sh` + `register-mac-runner.sh` for the Linux side.

## What it does

1. Installs system packages via `pacman` (docker, git, git-lfs, node, pnpm, jq).
2. Ensures `docker.service` is running.
3. Creates a `actions-runner` system user in the `docker` group.
4. Downloads and extracts the pinned `actions/runner` tarball (sha256-verified) into `/var/lib/actions-runner`.
5. Uses the PAT from vault to fetch a short-lived registration token from the GitHub API.
6. Calls `./config.sh --unattended` to register the runner (idempotent via `.runner` marker file).
7. Templates `/etc/systemd/system/actions.runner.<name>.service` and starts it.

## Required variables

| Variable | Where | Notes |
|----------|-------|-------|
| `vault_github_pat` | `group_vars/linux_bare_metal/vault.yml` | PAT with `repo` scope, for `gh_runner_token_source: pat` (the default). Same requirement as the Mac setup. |

With `gh_runner_token_source: gh` no PAT is needed: the registration token is minted on the control node with its authenticated `gh` CLI, and only when the instance has no `.runner` marker yet.

## Repo or org

`gh_runner_scope: repo` (the default) registers against `gh_runner_repo` only. `gh_runner_scope: org` registers against the whole `gh_runner_owner` org, so every repo in its Default runner group can use the runner (the Unity runner pool does this). An org token needs an org admin: with `gh_runner_token_source: gh`, run `gh auth refresh -h github.com -s admin:org` on the control node first.

## Several runners on one host

Set `gh_runner_user_home` to a shared directory and `gh_runner_home` to a per-instance directory under it, and apply the role once per instance (the "platform CI runners" play in `playbooks/site.yml` loops it with `include_role`). Each instance gets its own systemd unit, `.runner` marker and work dir. `gh_runner_env` writes extra `KEY=VALUE` lines into the instance's `.env`; the platform play sets `HOME` per instance so parallel jobs never share a pnpm store or `~/setup-pnpm`. `gh_runner_slice` puts the unit in a systemd slice (the `platform-ci-host` role owns `pmp-ci.slice` and its caps).

All other variables have sensible defaults in `defaults/main.yml`.

## Verify

On the target host:

```bash
systemctl status actions.runner.thinkcentre-linux.service
sudo -u actions-runner docker ps
```

In GitHub: `https://github.com/BlackNBrownStudios/platform/settings/actions/runners` — the runner should appear online with labels `self-hosted, linux, x64, docker`.

## Notes

- The GitHub runner binary **auto-updates itself in place** during normal operation. The `gh_runner_version` default is only consulted on fresh provisioning (before `config.sh` exists). Don't try to hold the binary version by re-running this role.
- To rotate / re-register a runner, delete `/var/lib/actions-runner/.runner` on the host and remove the matching runner in the GitHub UI, then re-run the playbook.
- To uninstall a single runner: `systemctl disable --now actions.runner.<name>.service`, remove it in the GitHub UI, and delete its directory. `rm -rf /var/lib/actions-runner` is safe only when no other runner on the host uses it.

## Uninstall

The playbooks only add runners. To remove Unity CI pool slots (the "Unity CI runner pool" play), first take them out of `unity_ci_runners` in `ansible/inventory.yml` and `RUNNER_ROSTER` in `apps/github-runner-exporter/deployment.yaml`, then run this on the host as yourself:

```bash
bash scripts/unity-ci-runner-remove.sh            # every Unity slot on this host
bash scripts/unity-ci-runner-remove.sh --slot 2   # one slot
```

For each slot it stops and disables the unit, deletes the unit file and `/var/lib/actions-runner/unity-pool-<n>`, and deregisters the runner from the org with your `gh` (on a host without `gh`, it prints the command to run from the workstation). Once no Unity slot is left, it also removes `unity-ci.slice`, the asset-library copy and the Unity machine id. It removes the `actions-runner` user and `/var/lib/actions-runner` only when no runner of any kind remains, so the platform slots on asus-laptop and hp-victus keep working. Docker and the editor images stay. The workstation's editor images are the source that `unity-ci-host` copies to the other hosts. To set a host up again, add it back to the inventory and run `ansible-playbook playbooks/site.yml --limit <host> --tags unity-ci`.
