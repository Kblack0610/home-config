# usb-backup-mounts

Owns the seven btrfs subvolume mounts of the 8TB USB backup drive on `asus-laptop`, and makes them survive the drive re-enumerating under a different device node.

## Why

On **2026-08-09 09:39:43** the drive dropped off the bus:

```
usb 4-2: USB disconnect, device number 3
sd 2:0:0:0: [sdc] Synchronizing SCSI cache
sd 2:0:0:0: [sdc] Synchronize Cache(10) failed: Result: hostbyte=DID_ERROR
```

It came back as `/dev/sda`. The fstab entries were already keyed by UUID, so a reboot would have recovered cleanly — but nothing reboots this box, and nothing remounted. The seven mounts stayed in the table pointing at a device node that no longer existed:

```
$ mount | grep backup-8t
/dev/sdc1 on /mnt/backup-8t/immich type btrfs (rw,noatime,compress=zstd:1,...)
...
$ lsblk /dev/sdc
lsblk: /dev/sdc: not a block device
```

**Every signal lied in the same direction.** `df` reported 7.3T with 177G used. `mountpoint -q` returned true — which is precisely what defeated the backup job's own mount-safety guard, the one written for this exact scenario. Only real I/O failed, with EIO, 59k read errors and climbing.

Result: immich originals, the immich DB copy, all 14 btrfs snapshots and the 3D-print backup were dead for three days while every check reported healthy. It surfaced only because `KubeJobFailed` was firing — and that alert was being ignored, because `flux-audit` had been firing it nightly for months by design.

## What it does

Rewrites the seven fstab entries with `noauto,x-systemd.automount`. `systemd-fstab-generator` then emits a `.automount` unit plus a `.mount` unit that `BindsTo` the underlying `.device` unit, which buys two things:

| Event | Before | After |
|---|---|---|
| Device disappears | mount goes stale, still listed, `mountpoint` still true | systemd stops the `.mount` unit; path left clean |
| Device returns (any node name) | nothing; stays broken until someone notices | next access triggers an automatic remount, resolved by UUID |

Deliberately **no** `x-systemd.idle-timeout`: an idle unmount would make the `node_filesystem_*` series come and go, and `apps/monitoring/prometheus-rules-backup-drive.yaml` alerts on those series being absent.

## Run it

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass
cd ansible
ansible-playbook playbooks/site.yml --limit asus-laptop --tags backup-mounts --check --diff
ansible-playbook playbooks/site.yml --limit asus-laptop --tags backup-mounts
```

`asus-laptop` listens on **port 2222** (`ansible_port` in `inventory.yml`), not 22.

## Verifying it actually works

Mounted-ness is not the question; device-backed-ness is. Check the source device, not the mountpoint:

```bash
ssh -p 2222 kblack0610@192.168.1.152 'findmnt -o TARGET,SOURCE,FSTYPE /mnt/backup-8t/immich'
ssh -p 2222 kblack0610@192.168.1.152 'systemctl status mnt-backup\\x2d8t-immich.automount'
```

To prove the recovery path end-to-end, unmount and then touch the path — it should remount by itself:

```bash
sudo umount /mnt/backup-8t/immich && ls /mnt/backup-8t/immich   # remounts on access
```

## Related

- `apps/immich/originals-backup-cronjob.yaml` and `apps/nas/3d-prints-backup-cronjob.yaml` — both now probe with a real write (`require_live_mount`) rather than trusting `mountpoint`.
- `apps/monitoring/prometheus-rules-backup-drive.yaml` — alerts within minutes if a backup mount vanishes, instead of waiting for the next scheduled job to fail.
