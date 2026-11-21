# disk-health

Installs and runs `prometheus-smartctl-exporter` (port 9633) on every bare-metal Linux host (`linux_bare_metal` plus `desktop_workstations`), and runs a monthly `btrfs scrub` on each of asus-laptop's three filesystems. Scrub schedules and limits are in `host_vars/asus-laptop/disk-health.yml`; a host with no `disk_health_scrubs` gets the exporter only. Prometheus scrapes the hosts listed in `apps/monitoring/external-smartctl-exporter.yaml`, so a host added to the play goes there too. Alerts are in `apps/monitoring/prometheus-rules-disk-health.yaml`, and the Grafana dashboard is "Disk Health (SMART)" (`nas-disk-health.json`).

Each drive reports wear (`percentage_used`), spare blocks, media errors, the NVMe critical warning, temperature, bytes read and written, power-on time and power cycles. Exporter 0.14 does not export unsafe shutdowns; `smartctl -a /dev/nvme0` shows them.

Not covered: the Pis (SD cards have no SMART), the 8TB USB drive (below), mac-mini and the Windows machines (no exporter).

## Asus-laptop: what each signal covers

| Disk | SMART | btrfs error counters | Stale-backup alerts |
|---|---|---|---|
| Root NVMe | yes | yes | no |
| NAS NVMe | yes | yes | no |
| 8TB USB | no, see below | yes | yes |

The USB bridge rejected `-d sat`, `sat,12` and `sat,16`, and `-d scsi` reports no SMART capability (checked 2026-10-31). Vendor-specific bridge types were not tried, because a wrong passthrough command can upset a bridge. So the 8TB gets no early warning from SMART: its signals are btrfs errors and scrubs, and backups going stale.

## Run it

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit 'linux_bare_metal' --tags disk-health --check --diff
ansible-playbook playbooks/site.yml --limit 'linux_bare_metal' --tags disk-health
# The workstation's sudo asks for a password:
ansible-playbook playbooks/site.yml --limit cachyos-x8664-main --tags disk-health -K
```

## Check a scrub

```bash
ssh -p 2222 192.168.1.152 'systemctl list-timers "btrfs-scrub@*"'
ssh -p 2222 192.168.1.152 'sudo btrfs scrub status /mnt/nas/private'
ssh -p 2222 192.168.1.152 'sudo btrfs device stats /'
```

Start one by hand with `sudo systemctl start --no-block 'btrfs-scrub@mnt-nas-private.service'`. The instance name is `systemd-escape --path <path>`.
