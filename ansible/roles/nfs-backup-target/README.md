# nfs-backup-target

Turns a host into an NFS server that backup jobs on another machine read from, so the backup lands on a *different machine* than the data it protects.

## Why

On 2026-07-09 the forgejo nightly backup (a 55G hostPath tarball on its own node, `asus-laptop`) filled that node's root disk, tripped kubelet DiskPressure, and evicted the entire home-k3s workload cluster. A backup on the same disk as the data it protects is also not real DR. This role first made `hp-victus` an NFS target for that off-box copy. Since Forgejo moved to hp-victus (2026-11-11) the direction is reversed: hp-victus serves the live data read-only, and the copies land on asus-laptop's second disk and its 8TB drive.

## What it does

- Installs `nfs-utils`.
- Creates each root in `nfs_exports`.
- Writes `/etc/exports` with one line per `nfs_exports` entry: `/srv/forgejo` (Forgejo's live data and hourly DB snapshots) read-only to asus-laptop only.
- Enables + starts `nfs-server`, and self-heals the export live-state on rerun.

`no_root_squash` is used because the backup jobs run as root and read Forgejo files owned by uid 1000 in 0700 dirs; the export is read-only and limited to one host. k3s pods egress SNAT'd to their node IP, so the server sees `192.168.1.x` (the node), not the pod IP.

## Consumers

- `/srv/forgejo`: `apps/forgejo/backup-cronjob.yaml` (the nightly full), `apps/nas/forgejo-packages-backup-cronjob.yaml` and `apps/nas/backup-verify-cronjob.yaml`, all running on asus-laptop.

## Bound in

`ansible/playbooks/site.yml` — play "NFS backup target (hp-victus)". Run:

```bash
ansible-playbook ansible/playbooks/site.yml --limit hp-victus --tags nfs
```
