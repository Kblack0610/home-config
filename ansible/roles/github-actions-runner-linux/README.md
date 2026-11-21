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
7. On every apply outside `--check`, reads the runner from GitHub and, when its custom labels differ from `gh_runner_labels`, replaces them with one PUT to `<api_path>/actions/runners/<id>/labels`. A runner ci-runner-governor withholds (it carries one of `gh_runner_withheld_labels`) gets `gh_runner_labels` without `gh_runner_governed_labels`, plus `gh_runner_withheld_label`, so an apply never admits it. The plan is `filter_plugins/runner_labels.py`, tested by `tests/test_runner_labels.py`.
8. Templates `/etc/systemd/system/actions.runner.<name>.service` and starts it.

## Required variables

| Variable | Where | Notes |
|----------|-------|-------|
| `vault_github_pat` | `group_vars/ci_hosts/vault.sops.yaml` (every CI host, the AWS ones included) | PAT with `repo` scope, for `gh_runner_token_source: pat` (the default). Same requirement as the Mac setup. |

With `gh_runner_token_source: gh` no PAT is needed: the registration token is minted on the control node with its authenticated `gh` CLI, and only when the instance has no `.runner` marker yet.

## Repo or org

`gh_runner_scope: repo` (the default) registers against `gh_runner_repo` only. `gh_runner_scope: org` registers against the whole `gh_runner_owner` org, so every repo in its Default runner group can use the runner (the Unity runner pool does this). An org token needs an org admin: with `gh_runner_token_source: gh`, run `gh auth refresh -h github.com -s admin:org` on the control node first.

## Several runners on one host

Set `gh_runner_user_home` to a shared directory and `gh_runner_home` to a per-instance directory under it, and apply the role once per instance (the "CI runners (Unity slots and the seats that borrow them)" play in `playbooks/site.yml` loops it with `include_role`). Each instance gets its own systemd unit, `.runner` marker and work dir. `gh_runner_env` writes extra `KEY=VALUE` lines into the instance's `.env`; the Linux CI runners play sets `HOME` per instance so parallel jobs never share a pnpm store or `~/setup-pnpm`. `gh_runner_slice` puts the unit in a systemd slice (the `ci-host-budget` role owns `ci.slice` and its `ci-unity.slice` child and its per-slot slices, and their caps).

A unit or `.env` change restarts the runner, and the apply never kills a job for it. Under a job (a `Runner.Worker` under its `bin*/`) it drains the runner: one PUT of an empty custom-label set to `<api_path>/actions/runners/<id>/labels`, so no queued job matches it, then a wait on the worker's exit (`files/wait_worker_exit.py` in github-actions-runner-linux: a pidfd on Linux, kqueue on macOS, tested by `tests/test_wait_worker_exit.py`), then the restart, then a PUT of its labels back (the plan of step 7, from its labels before the drain and after, so a governor withhold during the drain holds). The apply blocks until the job ends, up to `gh_runner_drain_timeout` (360 minutes, GitHub's default job timeout). Past it the labels go back, `.restart-pending` stays in the runner home and the task reports changed, so ConvergenceDrift shows the pending restart until a later apply finishes it. An interrupted apply (Ctrl-C mid-wait) can leave the runner drained; re-run the apply.

All other variables have sensible defaults in `defaults/main.yml`.

## Verify

On the target host:

```bash
systemctl status 'actions.runner.*'   # runner name is <inventory_hostname>-linux
sudo -u actions-runner docker ps
```

In GitHub: `https://github.com/BlackNBrownStudios/platform/settings/actions/runners` — the runner should appear online with labels `self-hosted, linux, x64, docker`.

## Notes

- The GitHub runner binary **auto-updates itself in place** during normal operation. The `gh_runner_version` default is only consulted on fresh provisioning (before `config.sh` exists). Don't try to hold the binary version by re-running this role.
- To re-register a runner (its listener logs "The runner registration has been deleted from the server"), remove it in the GitHub UI if it is still listed, stop its unit, delete `.runner`, `.runner_migrated`, `.credentials` and `.credentials_rsaparams` in its `gh_runner_home` (for example `/var/lib/linux-ci/dotfiles-private-1`), then re-run the playbook. The runner keeps `.runner_migrated` as a second marker, so deleting `.runner` alone fails `config.sh` with "already configured" (razer, 2026-11-10).
- A seat dropped from the topology (`ci_seats`, under `/var/lib/linux-ci`) is retired by the next apply: `tasks/retire.yml` reads each `<dir>/.runner` there, and for every GitHub runner whose name is not in `gh_runner_keep_names` and whose dir is not in `gh_runner_keep_dirs` deletes its registration by id with the control node's gh (a 404, already deleted, passes), then stops and removes its unit and removes its dir. GitHub refuses (422) to delete a runner running a job, so that one waits for a later apply. A `.runner` that cannot be read, or that sits outside a direct child dir of the home, is never retired; the apply warns about it. The pick is `filter_plugins/runner_retire.py`, tested by `tests/test_runner_retire.py`. The lend seats of hc-825 (`<host>-unity-pool-N-light-M`, `-heavy-M`, `<host>-<repo>-N`) were retired this way (hc-839).
- To uninstall any other single runner: `systemctl disable --now actions.runner.<name>.service`, remove it in the GitHub UI, and delete its directory. `rm -rf /var/lib/actions-runner` is safe only when no other runner on the host uses it.

## Uninstall

The playbooks only add runners. To remove Unity CI pool slots (the "CI runners (Unity slots and the seats that borrow them)" play), first take them out of `unity_ci_runners` in `ansible/inventory.yml` and re-run `scripts/render-ci-topology.py`, then run this on the host as yourself:

```bash
bash scripts/unity-ci-runner-remove.sh            # every Unity slot on this host
bash scripts/unity-ci-runner-remove.sh --slot 2   # one slot
```

For each slot it stops and disables the unit, deletes the unit file and `/var/lib/actions-runner/unity-pool-<n>`, and deregisters the runner from the org with your `gh` (on a host without `gh`, it prints the command to run from the workstation). Once no Unity slot is left, it also removes the asset-library copies and the Unity machine id. The CI slices stay; `ci-host-budget` owns them, and the host's other runners use them. It removes the `actions-runner` user and `/var/lib/actions-runner` only when no runner of any kind remains, so the platform slots on asus-laptop and hp-victus keep working. Docker and the editor images stay. The workstation's editor images are the source that `unity-ci-host` copies to the other hosts. To set a host up again, add it back to the inventory and run `ansible-playbook playbooks/site.yml --limit <host> --tags unity-ci`.
