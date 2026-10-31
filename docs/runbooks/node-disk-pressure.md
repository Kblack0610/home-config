# Node disk pressure

Kubelet evicts pods when a node's disk reaches 5% free, and keeps evicting until 15% is free (`evictionHard` 5%, `evictionMinimumReclaim` 10%). A node that trips loses its pods until about 10% of the disk comes back, and local-path PVs pin those pods to that node, so they stay Pending. On 2026-10-31 this took Prometheus, ntfy, vaultwarden and vikunja down for 8 hours.

## What happens as a disk fills

| Free | What happens | Where |
|---|---|---|
| 25% | A withheld Unity slot may come back | `apps/ci-runner-governor/config.json` |
| 20% | `NodeDiskLow` warning. The governor withholds the host's Unity slots. `LocalPathPvFarOverRequest` pages if a PV on the node is past 150% of its request | `apps/monitoring/prometheus-rules-capacity.yaml` |
| 15% | `NodeDiskFillingFast` pages if the 2h trend reaches zero within 4 hours | same |
| 10% | `NodeDiskCritical` pages | same |
| 5% | Kubelet evicts until 15% is free | kubelet config |

## What fills these disks

Largest first, from razer and hp-victus on 2026-10-31:

- `/var/lib/actions-runner/unity-pool-*/_work/*/*/Builds`: perf and release trees, 40G to 60G each. `ci-disk-prune.sh` removes perf trees after 1 day and release trees after 7 days idle, daily at 04:30, on idle slots only.
- `/var/lib/containerd` and docker images: Unity editor and test images, 15G to 30G each. Editor images are never pruned.
- `/var/lib/rancher/k3s/storage`: local-path PVs. Size is not enforced, so a PV can pass its request (Frigate reached 153G in an 80Gi PV).
- `/srv/backups/forgejo` on hp-victus: the two newest Forgejo off-box copies. The job refuses to go under 20% free.

## When it pages

1. See what grew: `sudo du -xh --max-depth=2 /var/lib | sort -rh | head -20`, then run the prune with `sudo systemctl start pmp-ci-docker-cleanup.service`. It skips busy slots and logs each removal to its journal.
2. A CI host: the governor has already withheld its Unity slots. Removing a whole `Builds/` tree on an idle slot costs only a cold build.
3. Do not delete from `/var/lib/rancher/k3s/storage` by hand. To move a PVC off the node, see [move-local-path-pvc.md](move-local-path-pvc.md).
4. After an eviction, delete the leftover Evicted pods. A DaemonSet pod stays Failed until you do, and node-exporter is one of them.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Off-box job fails with `.tmp: File exists` or a size mismatch, from before #446 | A manual job and the scheduled one ran together; `concurrencyPolicy` does not cover `create job --from` | Fixed in #446; rerun the job |
| Prune removes nothing while a slot looks idle | Its newest file is inside the age limit, or `Runner.Worker` is running | `pgrep -af Runner.Worker`; the next daily run takes it |
| A host's Unity slots stay withheld after the disk recovers | Re-admit needs 25% free, and the host has to report room and have jobs waiting | `kubectl -n monitoring logs deploy/ci-runner-governor` |
