# nfs-backup-target

Turns a host into an NFS server that hosts **off-box backups** for k3s workloads — a backup copy that lives on a *different machine* than the one that produced it.

## Why

On 2026-07-09 the forgejo nightly backup (a 55G hostPath tarball on its own node, `asus-laptop`) filled that node's root disk, tripped kubelet DiskPressure, and evicted the entire home-k3s workload cluster. A backup on the same disk as the data it protects is also not real DR. This role makes a second machine (`hp-victus`, 938G root) an NFS target so the backup job can keep an independent off-box copy.

## What it does

- Installs `nfs-utils`.
- Creates each root in `nfs_exports` and a subdir per producer under `/srv/backups` (`nfs_backup_subdirs`, default `forgejo`).
- Writes `/etc/exports` with one line per `nfs_exports` entry: `/srv/backups` read-write to the LAN, and `/srv/forgejo` (Forgejo's live data and hourly DB snapshots) read-only to asus-laptop only.
- Enables + starts `nfs-server`, and self-heals the export live-state on rerun.

`no_root_squash` is used because the forgejo backup job runs as root and writes root-owned tarballs; acceptable on a LAN-only export. k3s pods egress SNAT'd to their node IP, so the server sees `192.168.1.x` (the node), not the pod IP.

## Consumers

- `/srv/forgejo`, from the Forgejo cutover PR on: `apps/forgejo/backup-cronjob.yaml` (the nightly full), `apps/nas/forgejo-packages-backup-cronjob.yaml` and `apps/nas/backup-verify-cronjob.yaml`, all running on asus-laptop.
- `/srv/backups/forgejo`: `forgejo-backup-offbox` and `backup-verify` until the cutover PR moves the off-box copy to the 8TB drive; its entry goes when the old copy is deleted.

## Bound in

`ansible/playbooks/site.yml` — play "NFS backup target (hp-victus)". Run:

```bash
ansible-playbook ansible/playbooks/site.yml --limit hp-victus --tags nfs
```
