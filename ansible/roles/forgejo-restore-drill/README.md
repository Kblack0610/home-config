# forgejo-restore-drill

Runs `scripts/forgejo-restore-drill.sh` weekly on `asus-laptop`, the backup host: it restores the newest Forgejo full and its DB snapshot into a container with no network and checks healthz, the version, the db and `git fsck` on every repo. The schedule and the Forgejo URL are in `defaults/main.yml`.

The runner (`files/forgejo-restore-drill-run.sh`) takes the image tag from the live `/api/v1/version`, so the drill follows a Forgejo upgrade without a re-run of this role, and writes `forgejo_restore_drill_*` to the node-exporter textfile directory. The drill script itself is copied from `scripts/`, so a change to it needs a re-run of this role to reach the host.

The Sentinel watch `forgejo-restore-drill` (`~/.agent/watches/forgejo-restore-drill.yaml`) queries Prometheus and pages when the last run failed or no run passed in 8 days.

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit asus-laptop --tags forgejo-restore-drill --check --diff
ansible-playbook playbooks/site.yml --limit asus-laptop --tags forgejo-restore-drill
```

## Check a run

```bash
ssh -p 2222 192.168.1.152 'systemctl list-timers forgejo-restore-drill.timer'
ssh -p 2222 192.168.1.152 'sudo systemctl start forgejo-restore-drill.service'   # an ad-hoc run, about 5 min
ssh -p 2222 192.168.1.152 'journalctl -u forgejo-restore-drill -n 40 --no-pager'
ssh -p 2222 192.168.1.152 'cat /var/lib/node-exporter-textfile/forgejo_restore_drill.prom'
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `DRILL FAILED: cannot read the deployed version` | Forgejo was down at run time. The drill needs only the backups, so run it again once Forgejo is up. |
| `DRILL FAILED: ... has ...G free, needs ...G` | `/mnt/backups` is short of space for the unpack; see `docs/backup-runbook.md`. |
