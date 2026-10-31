# disk-health

Runs a monthly `btrfs scrub` on each of `asus-laptop`'s three filesystems and keeps `prometheus-smartctl-exporter` running. Schedules and limits are in `defaults/main.yml`. Alerts are in `apps/monitoring/prometheus-rules-disk-health.yaml`.

## What each signal covers

| Disk | SMART | btrfs error counters | Stale-backup alerts |
|---|---|---|---|
| Root NVMe | yes | yes | no |
| NAS NVMe | yes | yes | no |
| 8TB USB | no, see below | yes | yes |

The USB bridge rejected `-d sat`, `sat,12` and `sat,16`, and `-d scsi` reports no SMART capability (checked 2026-10-31). Vendor-specific bridge types were not tried, because a wrong passthrough command can upset a bridge. So the 8TB gets no early warning from SMART: its signals are btrfs errors and scrubs, and backups going stale.

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit asus-laptop --tags disk-health --check --diff
ansible-playbook playbooks/site.yml --limit asus-laptop --tags disk-health
```

## Check a scrub

```bash
ssh -p 2222 192.168.1.152 'systemctl list-timers "btrfs-scrub@*"'
ssh -p 2222 192.168.1.152 'sudo btrfs scrub status /mnt/nas/private'
ssh -p 2222 192.168.1.152 'sudo btrfs device stats /'
```

Start one by hand with `sudo systemctl start --no-block 'btrfs-scrub@mnt-nas-private.service'`. The instance name is `systemd-escape --path <path>`.
