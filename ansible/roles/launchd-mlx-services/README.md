# launchd-mlx-services

Manages the MLX inference LaunchAgent plists on `mac-studio`. Templates one plist per entry in `mlx_services` into `~/Library/LaunchAgents/`, then bootstraps only the agents whose plist actually changed.

## What it does

1. Asserts `~/mlx-env/bin/python3` exists (the venv is not managed by this role — see `docs/mac-machines.md:34`).
2. Ensures `~/Library/LaunchAgents/` exists.
3. Templates `com.mlx-lm.code|smart|reasoning.plist` from `templates/mlx-service.plist.j2`.
4. For each plist that changed: `launchctl bootout` (ignore-if-absent) + `launchctl bootstrap` to re-register under the current GUI user.

## What it does NOT do

- **Create the `~/mlx-env` Python venv or install `mlx-lm`.** Do that once per machine: `python3 -m venv ~/mlx-env && ~/mlx-env/bin/pip install mlx-lm`.
- **Download model weights.** `mlx_lm.server` pulls from HuggingFace on first run. Pre-warm if desired: `~/mlx-env/bin/huggingface-cli download <model>`.
- **Touch services whose plist did not change.** A re-run with no diff leaves running MLX processes untouched.

## Required variables

None — `defaults/main.yml` has the full three-service matrix matching `docs/mac-machines.md:24-53`. Override `mlx_services` in `host_vars/mac-studio.yml` to add or remove models.

## Safety / blast radius

Applying this role against live `mac-studio` with plist content that differs from what is already on disk will bootout + bootstrap the affected MLX service — which **will interrupt in-flight inference requests**. First run against a host that already has MLX running by hand:

1. Dry-run first: `ansible-playbook playbooks/site.yml --limit mac-studio --check --diff --tags mlx` — scan the `changed` lines for each plist. If all three come back `changed: true`, that is the templating mismatch and every service will bootstrap.
2. Consider templating against the running plists first: `ssh mac-studio plutil -convert xml1 -o - ~/Library/LaunchAgents/com.mlx-lm.code.plist` and diff against the rendered Jinja2 output to catch drift before apply.

## Verify

```bash
ssh mac-studio launchctl list | grep mlx
ssh mac-studio curl -s http://localhost:8080/v1/models
tail -f /tmp/mlx-lm-code.log
```

## Binding in site.yml

**Authored but NOT bound.** Applying unattended can kill live MLX inference mid-request. Enable the binding once the plist diff has been reviewed against the live hosts:

```yaml
- name: MLX LaunchAgents on mac-studio
  hosts: mac-studio
  gather_facts: true
  roles:
    - role: launchd-mlx-services
      tags: [mlx]
```
