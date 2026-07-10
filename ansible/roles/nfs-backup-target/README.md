# nfs-backup-target

Turns a host into an NFS server that hosts **off-box backups** for k3s workloads — a backup copy that lives on a *different machine* than the one that produced it.

## Why

On 2026-07-09 the forgejo nightly backup (a 55G hostPath tarball on its own node, `asus-laptop`) filled that node's root disk, tripped kubelet DiskPressure, and evicted the entire home-k3s workload cluster. A backup on the same disk as the data it protects is also not real DR. This role makes a second machine (`hp-victus`, 938G root) an NFS target so the backup job can keep an independent off-box copy.

## What it does

- Installs `nfs-utils`.
- Creates `{{ nfs_backup_export_path }}` (default `/srv/backups`) plus a subdir per producer (default `forgejo`).
- Writes `/etc/exports` exporting the root to the LAN (default `192.168.1.0/24`, `rw,sync,no_subtree_check,no_root_squash`).
- Enables + starts `nfs-server`, and self-heals the export live-state on rerun.

`no_root_squash` is used because the forgejo backup job runs as root and writes root-owned tarballs; acceptable on a LAN-only export. k3s pods egress SNAT'd to their node IP, so the server sees `192.168.1.x` (the node), not the pod IP.

## Consumers

- `apps/forgejo/backup-cronjob.yaml` mounts `nfs://<hp-victus>/srv/backups/forgejo` and copies the newest full + db there after writing the local copy (see that file's `OFFBOX` step).

## Bound in

`ansible/playbooks/site.yml` — play "NFS backup target (hp-victus)". Run:

```bash
ansible-playbook ansible/playbooks/site.yml --limit hp-victus --tags nfs
```
