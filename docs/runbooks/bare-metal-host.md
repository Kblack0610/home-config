# Join a bare-metal host

A fresh x86 box becomes a k3s agent, and a CI host if it carries slots. These are the steps ms-a2, deskslim-2 (2026-11-16) and deskslim-1 (2026-11-18) and tuf-b550 (2026-11-20) took. One PR carries every repo change.

1. At the machine (human). BIOS: Restore on AC Power Loss = Power On ([baremetal-power.md](../baremetal-power.md)), Secure Boot off, network boot on for the install. Reserve its MAC in `infrastructure/dhcp/devices.yaml` first, so it comes up on its own IP.
2. PXE (`infrastructure/pxe-server/README.md`): pick **[4] Cluster** at the menu. The menu defaults to local boot after 10 s, so an unattended box never reinstalls. It installs CachyOS, sshd and the workstation's key, with no k3s, and the host comes up as `cluster-<last 4 of MAC>`. Afterwards put the local disk first in the boot order.
3. First login over ssh: `sudo hostnamectl set-hostname <host>`. `sudo -n true` must pass: an install before #1099 (2026-11-17) dropped `/etc/sudoers.d/wheel-nopasswd`, and the login password stays the PXE `changeme` until the passwords role runs (`roles/login-passwords`). Read what the inventory needs: `lscpu -e=CPU,CORE` (the SMT sibling layout), `free -g`, `cat /etc/ssh/ssh_host_ed25519_key.pub`.
4. Inventory (`ansible/inventory.yml`): the host under `linux_bare_metal` with `ansible_host`, `ssh_host_ed25519`, `k3s_role: agent` and its friendly-name label. Then `watchdog_x86` (which puts it in `k3s_nodes`, so in `fleet_pulse` and `thermal_capped`), `netconsole_senders`, and `nut_clients` plus its row in [nut-ups.md](../nut-ups.md). A CI host also goes in `unity_ci_runners` and `ci_hosts` ([unity-ci-host.md](unity-ci-host.md)). Add it to `HOSTS` in `apps/ansible-runner/cronjob.yaml`, the nightly convergence check.
5. Render, then run the checks in `.forgejo/workflows/checks.yaml` locally: `scripts/render-ci-topology.py` and `scripts/render-fleet-pulse.py` write `apps/ci-topology/topology.json`, `apps/gatus-fleet/config.yaml` and `apps/fleet-exporter/roster.json`.
6. Apply from `ansible/`, in this order:
   - `ansible-playbook playbooks/site.yml --limit <host> --tags authorized-keys,passwords,time,no-suspend,watchdog,crash-evidence,tailscale`
   - `--limit <host> --tags k3s`
   - `kubectl label node <host> node-role.kubernetes.io/workstation=true` (a kubelet may not set its own role), then `--limit <host> --tags node-labels`
   - the whole `site.yml --limit <host>`, which runs the CI play
7. Verify: `kubectl get node <host>` is Ready with the same labels as its peers, every slot is online in the exporter, and the first Unity job proves the activation.

`roles/k3s-agent` owns every k3s install and upgrade; PXE only installs the OS. Flux then schedules pods onto the node by its labels.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `couldn't resolve module/action 'community.general.pacman'` | Running from a worktree: `ansible/collections/` is gitignored | `ln -s ~/dev/home/home-config/ansible/collections ansible/collections` in the worktree |
| `sudo: a password is required` over ssh | Installed by PXE before #1099 | Become with the PXE default once and restore `/etc/sudoers.d/wheel-nopasswd` (`%wheel ALL=(ALL:ALL) NOPASSWD: ALL`, mode 440, `visudo -c`); the passwords role then replaces `changeme` |
| `crash-evidence not live: netconsole=loaded printk=1` on the first apply | The read-back ran before the handler that applied the loglevel (fixed 2026-11-18: it is a task now) | Rerun `--tags crash-evidence` |
| `List running CI jobs before restarting docker` or `Copy missing editor images` fails on `/var/run/docker.sock` | A fresh Arch host has docker installed but stopped: ci-host-budget read it as stale, and ci-host started it only after the images (both fixed 2026-11-18) | Rerun the whole `site.yml --limit <host>` |
| Node Ready but ROLES shows `<none>` | The workstation role label is set by hand | Step 6's `kubectl label` |
