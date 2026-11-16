# Move a local-path PVC to another node

A local-path PV is pinned to one node, and everything on it goes down with that node's disk. On 2026-10-31 hp-victus hit DiskPressure and took 13 PVs with it, including the pager.

## Procedure

1. Open a PR adding `nodeSelector` to the Deployment. Wait for CI, and do not merge yet.
2. Suspend both Flux levels, parent first, because `flux-system` manages `apps` and clears a suspend set on `apps` alone: `for k in flux-system apps; do kubectl -n flux-system patch kustomization $k --type=merge -p '{"spec":{"suspend":true}}'; done`
3. For each app: `KUBE_CONTEXT=home-k3s scripts/move-local-path-pvc.sh <ns> <pvc> <deployment> <node>`. For a PVC only CronJobs mount (such as `freqtrade/freqtrade-lab`), pass `-` as the deployment: the script suspends every CronJob in the namespace that mounts it, refuses while one of their pods is running, and resumes them after the copy. Such a PVC needs no `nodeSelector` PR, because its pods follow the new PV.
4. Check every move printed `Done`, or do not merge. Merged early, the `nodeSelector` strands the app Pending while its data is still on the old node.
5. Merge the PR, then resume: `for k in flux-system apps; do kubectl -n flux-system patch kustomization $k --type=merge -p '{"spec":{"suspend":false}}'; done`

Merge before resuming. Otherwise Flux removes the `nodeSelector`.

## StatefulSet volumes

A StatefulSet's claim (`<template>-<sts>-<ordinal>`) moves with `-` as the deployment, because the script cannot scale a StatefulSet itself. Suspend Flux (step 2), run `kubectl -n <ns> scale sts <sts> --replicas=0` and wait until its pods are gone, run the script once per claim, then scale back to the manifest's count and resume Flux. The recreated claim keeps its name and labels, so the StatefulSet adopts it and each pod follows its new PV. No `nodeSelector` PR is needed when the StatefulSet's affinity already allows the target.

When an affinity change rules out the node that holds a claim, the pod stays Pending (`volume node affinity conflict`) until the claim moves. Move the claims before that change merges, or right after it. On 2026-11-14, 9 `freqtrade-league` claims stayed on hp-victus after #953 excluded it, and those bots were down for 16 hours.

## If the script stops midway

The app is scaled to 0 and the old data is intact. Rerunning does not work once the old PVC is gone, because the script reads the old PV from it. Finish by hand from the script's own steps: recreate the PVC, run the two copy pods, compare hashes, then scale the app back up.

The script sets the old PV to `Retain`, stops the app, recreates the PVC on the target node, streams the data between two pods, compares hashes and restarts the app. Check the image supports the target's architecture first: Pis are arm64.

## Rollback

The old data stays on the old node until you remove it.

1. Scale the Deployment to 0 and delete the new PVC.
2. Clear the old PV's claim: `kubectl patch pv <pv> --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'`
3. Recreate the PVC with `spec.volumeName: <pv>`, restore the old `nodeSelector` and scale back up.

Anything written after the move is lost.

## Cleanup

After a week, set the old PV's reclaim policy back to `Delete` and delete the PV. The provisioner then removes the directory.
