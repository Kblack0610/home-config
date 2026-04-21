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
| `vault_github_pat` | `group_vars/linux_bare_metal/vault.yml` | PAT with `repo` scope. Same requirement as the Mac setup. |

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
- To uninstall: `systemctl disable --now actions.runner.<name>.service`, remove from the GitHub UI, `rm -rf /var/lib/actions-runner`.
