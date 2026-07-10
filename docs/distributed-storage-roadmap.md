# Distributed storage roadmap (bare-metal k3s)

## Why this exists

Today home-k3s has **no distributed or replicated block storage**. The only StorageClass is `local-path` (default, `rancher.io/local-path`), which pins each PVC's data to whichever node first schedules the pod, via node affinity. Heavy stateful apps (forgejo, immich, minio, postgres, monitoring, gatus, jellyfin, karakeep, nas) all landed on **asus-laptop**, plus `hostPath` mounts under `/mnt/nas/...` that physically exist only there. See `docs/architecture.md` (Storage section): "There is no NFS/Longhorn/Ceph in this cluster."

Consequences:

- **asus-laptop is a single point of failure.** On 2026-07-09 its root disk hit the kubelet DiskPressure threshold, tainted the node, evicted pods, and took the cluster down (forgejo down -> git push + Flux both fail). Root cause + interim fix: memory `project_home_k3s_disk_pressure.md`, PR #108.
- **Stateful pods cannot reschedule.** If the storage node dies, the PVC dies with it; k3s just pulls the pod back to the (dead) node's affinity.
- **New stateful apps inherit the problem.** FleetDM's MySQL (`apps/fleet`) is currently pinned to **hp-victus** via a `fleet.storage/node` label purely to keep it *off* asus-laptop. That trades one single-node dependency for another - it is an **interim** measure until this roadmap lands.

Goal: introduce **replicated block storage across the bare-metal nodes** so a stateful PVC survives (and reschedules past) the loss of any one node, and so no single box is the cluster's storage SPOF.

## Recommended approach: Longhorn

[Longhorn](https://longhorn.io) is the idiomatic replicated block storage for k3s (both are Rancher/SUSE): a CNCF project, Helm/Flux-installable, synchronous N-way replication of each volume across nodes, built-in snapshots + backup-to-S3 (we already run MinIO at `s3.kblab.me`), and a `longhorn` StorageClass that is a drop-in replacement for `local-path` on RWO PVCs.

Why Longhorn over the alternatives here:

- **Rook/Ceph** - far heavier (Ceph mons/osds/mgr); overkill for ~8 small nodes and hungry on the Pis.
- **OpenEBS Mayastor** - needs hugepages + NVMe assumptions the Pi nodes do not meet.
- **NFS (single server)** - centralizes rather than distributes; just moves the SPOF.

### Node-fit caveat (important)

Longhorn replicas want real disks and steady CPU. The 29 GB Pi nodes are poor replica targets. Practical topology: run Longhorn replicas on the **x86 boxes with disk** (asus-laptop 1.9 TB, hp-victus 956 GB) and optionally the one 119 GB Pi (`raspberrypi-23a7710c`), with `replica-count = 2` and anti-affinity so the two copies never share a node. The small Pis stay compute-only (schedule Longhorn as a workload but disable them as replica storage via node tags). This still removes the single-node SPOF for the heavy apps, which is the whole point.

## Phased plan

- **Phase 1 - Install (non-disruptive).** Add `apps/longhorn/` (Flux): namespace + HelmRelease pinned to a version, `defaultReplicaCount: 2`, backup target = the MinIO S3 bucket. Do NOT change the default StorageClass yet. Verify the Longhorn UI (LAN-only ingress) shows all intended nodes healthy.
- **Phase 2 - Tag replica nodes.** Label/taint so replicas live only on the x86 nodes (+ big Pi); small Pis excluded as storage. Confirm scheduling.
- **Phase 3 - New apps first.** Point **FleetDM's MySQL** PVC at `storageClassName: longhorn` (drop the `fleet.storage/node` pin) as the first real tenant - it is new, low-risk, and already the motivating case. Validate failover: cordon hp-victus, confirm the MySQL pod reschedules onto another replica node and Fleet recovers.
- **Phase 4 - Migrate the heavy apps off asus-laptop.** One at a time, lowest-risk first (gatus, karakeep, then immich/forgejo/postgres), each: scale down -> copy PVC data into a Longhorn volume (backup/restore or `pvmigrate`) -> repoint the manifest -> verify -> back up. Keep the per-app backup CronJobs throughout. `hostPath`/`/mnt/nas` apps need a separate decision (Samba re-export vs Longhorn RWX).
- **Phase 5 - Flip the default + decommission the SPOF pattern.** Once the heavy apps are on Longhorn, make `longhorn` the default StorageClass, and update `docs/architecture.md` (the "no NFS/Longhorn/Ceph" statement) + the local-path guidance.

## Interaction with the UPS/NUT shutdown

Longhorn replicas must flush cleanly on the NUT FSD graceful-shutdown broadcast (`docs/nut-ups.md`). Verify replica rebuild does not thrash on the ~25-min UPS budget, and that the settle order (Flux suspend -> cordon -> agent stop) still lets Longhorn detach volumes before halt. Add to the roadmap's Phase 1 acceptance checks.

## Verification (per phase)

- Phase 1: `kubectl -n longhorn get pods` healthy; Longhorn UI shows nodes/disks; a test PVC binds and mounts.
- Phase 3: kill/cordon the node hosting a Longhorn PVC's pod; the pod reschedules and data is intact (this is the failover proof local-path can never pass).
- Phase 5: `kubectl get sc` shows `longhorn (default)`; no heavy app remains `nodeSelector`/`hostPath`-pinned to asus-laptop.

## Status

Not started. This roadmap was written alongside `apps/fleet` (2026-07), whose hp-victus pin is the interim stand-in for Phase 3. Track as a follow-up epic.
