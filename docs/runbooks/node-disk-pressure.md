# Node disk pressure

Kubelet evicts pods when a node's disk reaches 5% free, and keeps evicting until 15% is free (`evictionHard` 5%, `evictionMinimumReclaim` 10%). A node that trips loses its pods until about 10% of the disk comes back, and local-path PVs pin those pods to that node, so they stay Pending. On 2026-10-31 this took Prometheus, ntfy, vaultwarden and vikunja down for 8 hours.

## What happens as a disk fills

| Free | What happens | Where |
|---|---|---|
| 25% | A withheld Unity slot may come back. Below this, `CiHostHeldOutByDisk` pages after 30 minutes if slots are withheld and jobs wait | `apps/ci-runner-governor/config.json`, `apps/monitoring/prometheus-rules-capacity.yaml` |
| 20% | `NodeDiskLow` warning. The governor withholds the host's Unity slots. `LocalPathPvFarOverRequest` pages if a PV on the node is past 150% of its request | `apps/monitoring/prometheus-rules-capacity.yaml` |
| 15% | `NodeDiskFillingFast` pages if the 2h trend reaches zero within 4 hours | same |
| 10% | `NodeDiskCritical` pages | same |
| 5% | Kubelet evicts until 15% is free | kubelet config |

## What fills these disks

Largest first, from razer and hp-victus on 2026-10-31:

- `/var/lib/actions-runner/unity-pool-*/_work/*/*/Builds`: perf and release trees, 40G to 60G each. `ci-disk-prune.sh` removes perf trees after 1 day, release trees after 7 days idle, and every copy of a release tree but the newest per target, hourly, on idle slots only.
- `/var/lib/containerd` and docker images: Unity editor and test images, 15G to 30G each. Editor images are never pruned.
- `/var/lib/rancher/k3s/storage`: local-path PVs. Size is not enforced, so a PV can pass its request (Frigate reached 153G in an 80Gi PV).
- `/srv/backups/forgejo` on hp-victus: the two newest Forgejo off-box copies. The job refuses to go under 20% free.

## When it pages

The disk and CI alerts here carry `fleet: fix`, so besides ntfy each one becomes a `red` queue item for an agent (sentinel watch `alertmanager-fleet`, route in agentctl `triage-routes`, skill `fix-red`). A silence in Alertmanager stops both; give it a reason.

1. See what grew: `sudo du -xh --max-depth=2 /var/lib | sort -rh | head -20`, then run the prune with `sudo systemctl start linux-ci-docker-cleanup.service`. It skips busy slots and logs each removal to its journal.
2. A CI host: the governor has already withheld its Unity slots. Removing a whole `Builds/` tree on an idle slot costs only a cold build.
3. Do not delete from `/var/lib/rancher/k3s/storage` by hand. To move a PVC off the node, see [move-local-path-pvc.md](move-local-path-pvc.md).
4. After an eviction, delete the leftover Evicted pods. A DaemonSet pod stays Failed until you do, and node-exporter is one of them.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Off-box job fails with `.tmp: File exists` or a size mismatch, from before #446 | A manual job and the scheduled one ran together; `concurrencyPolicy` does not cover `create job --from` | Fixed in #446; rerun the job |
| Prune removes nothing while a slot looks idle | Its newest file is inside the age limit, or `Runner.Worker` is running | `pgrep -af Runner.Worker`; the next hourly run takes it |
| A host's Unity slots stay withheld after the disk recovers | Re-admit needs 25% free, and the host has to report room and have jobs waiting | `kubectl -n monitoring logs deploy/ci-runner-governor` |
| Slots withheld for CPU pressure stay out on a cool, idle host, and the log shows no new line | Between 20% and 25% free the verdict is STEADY: nothing withholds, nothing re-admits (razer, 2026-11-01) | Remove idle `Builds/` trees until free is over 25%; the governor re-admits one slot per 10 minutes |
| On mac-mini, removing `Builds/` trees frees almost nothing although `du` counts 15G+ | Unity's player output on APFS is mostly clones of `Library/` data, so the space sits in each tree's `Library/`. The playground runner (`~/actions-runner-playground`, no play) keeps one build tree per game beside its checkout (2026-11-10: 7 dirs, 137G) | The hourly `com.kblab.ci-tree-prune` removes idle trees, oldest first, under 25% free ([Build-tree prune](../mac-machines.md#build-tree-prune)); its log is `/tmp/kblab-ci-tree-prune.log`. To run it now: `launchctl kickstart gui/$(id -u)/com.kblab.ci-tree-prune` on the Mac |
| pi5-master (29G SD card) goes under 20% free | Node-local copies under `/var/backups/*`: the home-assistant job kept 30 archives of 300-420 MB (9.7G) | Since hc-212 it keeps 7; older days are on the NAS (`backups/home-k3s/home-assistant`). Delete local archives the NAS already holds |
